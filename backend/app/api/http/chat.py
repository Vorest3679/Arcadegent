"""HTTP API layer: chat endpoint backed by orchestrator runtime."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.agent.runtime.orchestrator import SessionAlreadyRunningError
from app.agent.runtime.session_state import AgentSessionState, AgentTurn, get_working_memory_artifact
from app.api.deps import get_container
from app.core.container import AppContainer
from app.infra.observability.logger import get_logger
from app.protocol.messages import (
    ArcadeShopSummaryDto,
    ChatHistoryTurnDto,
    ChatRequest,
    ChatResponse,
    ChatSessionDispatchDto,
    ChatSessionDetailDto,
    ChatSessionSummaryDto,
    IntentType,
    RouteSummaryDto,
)

router = APIRouter(prefix="/api", tags=["chat"])
logger = get_logger(__name__)


def _normalize_intent(raw: str) -> IntentType:
    if raw == "navigate":
        return "navigate"
    if raw == "search_nearby":
        return "search_nearby"
    return "search"


def _single_line(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def _build_title(turns: list[AgentTurn]) -> str:
    for turn in turns:
        if turn.role != "user":
            continue
        title = _single_line(turn.content, limit=32)
        if title:
            return title
    return "New chat"


def _build_preview(turns: list[AgentTurn]) -> str | None:
    for turn in reversed(turns):
        if turn.role not in {"assistant", "user"}:
            continue
        preview = _single_line(turn.content, limit=72)
        if preview:
            return preview
    return None


def _to_turn(turn: AgentTurn) -> ChatHistoryTurnDto:
    return ChatHistoryTurnDto(
        role=turn.role,
        content=turn.content,
        name=turn.name,
        call_id=turn.call_id,
        created_at=turn.created_at,
    )


def _to_summary(state: AgentSessionState) -> ChatSessionSummaryDto:
    return ChatSessionSummaryDto(
        session_id=state.session_id,
        title=_build_title(state.turns),
        preview=_build_preview(state.turns),
        intent=_normalize_intent(state.intent),
        status=state.status,
        turn_count=len(state.turns),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _to_shop(raw: dict) -> ArcadeShopSummaryDto:
    return ArcadeShopSummaryDto(
        source=str(raw.get("source") or ""),
        source_id=int(raw.get("source_id") or 0),
        source_url=str(raw.get("source_url") or ""),
        name=str(raw.get("name") or "unknown arcade"),
        name_pinyin=raw.get("name_pinyin"),
        address=raw.get("address"),
        transport=raw.get("transport"),
        province_code=raw.get("province_code"),
        province_name=raw.get("province_name"),
        city_code=raw.get("city_code"),
        city_name=raw.get("city_name"),
        county_code=raw.get("county_code"),
        county_name=raw.get("county_name"),
        status=raw.get("status"),
        type=raw.get("type"),
        pay_type=raw.get("pay_type"),
        locked=raw.get("locked"),
        ea_status=raw.get("ea_status"),
        price=raw.get("price"),
        start_time=raw.get("start_time"),
        end_time=raw.get("end_time"),
        fav_count=raw.get("fav_count"),
        updated_at=raw.get("updated_at"),
        arcade_count=int(raw.get("arcade_count") or 0),
    )


def _state_shops(state: AgentSessionState) -> list[ArcadeShopSummaryDto]:
    shops_raw: list[dict] = []
    memory_shops = get_working_memory_artifact(state.working_memory, "shops")
    if isinstance(memory_shops, list):
        shops_raw.extend(item for item in memory_shops if isinstance(item, dict))
    memory_shop = get_working_memory_artifact(state.working_memory, "shop")
    if isinstance(memory_shop, dict):
        source_id = memory_shop.get("source_id")
        exists = any(item.get("source_id") == source_id for item in shops_raw)
        if not exists:
            shops_raw.append(memory_shop)
    return [_to_shop(row) for row in shops_raw[:20]]


def _state_route(state: AgentSessionState) -> RouteSummaryDto | None:
    memory_route = get_working_memory_artifact(state.working_memory, "route")
    if not isinstance(memory_route, dict):
        return None
    try:
        return RouteSummaryDto.model_validate(memory_route)
    except Exception:
        return None


def _to_detail(state: AgentSessionState) -> ChatSessionDetailDto:
    return ChatSessionDetailDto(
        session_id=state.session_id,
        intent=_normalize_intent(state.intent),
        active_subagent=state.active_subagent,
        status=state.status,
        last_error=state.last_error,
        reply=state.working_memory.get("reply") if isinstance(state.working_memory.get("reply"), str) else None,
        shops=_state_shops(state),
        route=_state_route(state),
        turn_count=len(state.turns),
        created_at=state.created_at,
        updated_at=state.updated_at,
        turns=[_to_turn(turn) for turn in state.turns],
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    logger.info(
        "api.chat.request session_id=%s intent=%s page_size=%s message=%s",
        request.session_id or "new",
        request.intent or "auto",
        request.page_size,
        " ".join(request.message.split())[:160],
    )
    try:
        response = await container.orchestrator.run_chat(request)
    except SessionAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "api.chat.response session_id=%s intent=%s shops=%s",
        response.session_id,
        response.intent,
        len(response.shops),
    )
    return response


@router.post(
    "/chat/sessions",
    response_model=ChatSessionDispatchDto,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch_chat_session(
    request: ChatRequest,
    container: AppContainer = Depends(get_container),
) -> ChatSessionDispatchDto:
    logger.info(
        "api.chat.dispatch session_id=%s intent=%s page_size=%s message=%s",
        request.session_id or "new",
        request.intent or "auto",
        request.page_size,
        " ".join(request.message.split())[:160],
    )
    try:
        session_id = await container.orchestrator.dispatch_chat(request)
    except SessionAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChatSessionDispatchDto(session_id=session_id, status="running")


@router.get("/v1/chat/sessions", response_model=list[ChatSessionSummaryDto])
def list_chat_sessions(
    limit: int = Query(default=40, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> list[ChatSessionSummaryDto]:
    sessions = container.session_store.list_snapshots(limit=limit)
    return [_to_summary(state) for state in sessions if state.turns]


@router.get("/v1/chat/sessions/{session_id}", response_model=ChatSessionDetailDto)
def get_chat_session(
    session_id: str,
    container: AppContainer = Depends(get_container),
) -> ChatSessionDetailDto:
    session = container.session_store.snapshot(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    return _to_detail(session)


@router.delete("/v1/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_session(
    session_id: str,
    container: AppContainer = Depends(get_container),
) -> Response:
    if container.orchestrator.is_session_running(session_id):
        raise HTTPException(status_code=409, detail=f"session '{session_id}' is currently running")
    deleted = container.session_store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
