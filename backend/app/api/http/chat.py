"""HTTP API layer: chat endpoint backed by orchestrator runtime."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.agent.runtime.orchestrator import SessionAlreadyRunningError
from app.agent.runtime.session_state import AgentSessionState, AgentTurn, get_working_memory_artifact
from app.api.deps import get_container
from app.core.container import AppContainer
from app.infra.observability.logger import get_logger
from app.protocol.messages import (
    ChatHistoryTurnDto,
    ChatRequest,
    ChatResponse,
    ChatSessionDispatchDto,
    ChatSessionDetailDto,
    ChatSessionSummaryDto,
    ClientLocationContext,
    IntentType,
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


def _state_shop_rows(state: AgentSessionState) -> list[dict]:
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
    return shops_raw[:20]


def _state_client_location(state: AgentSessionState) -> ClientLocationContext | None:
    memory_location = get_working_memory_artifact(state.working_memory, "client_location")
    if not isinstance(memory_location, dict):
        return None
    try:
        return ClientLocationContext.model_validate(memory_location)
    except Exception:
        return None


def _to_detail(state: AgentSessionState, *, container: AppContainer) -> ChatSessionDetailDto:
    raw_shops = _state_shop_rows(state)
    shops = container.arcade_payload_mapper.summaries_from_rows(raw_shops)
    route = container.arcade_payload_mapper.route_from_payload(
        get_working_memory_artifact(state.working_memory, "route")
    )
    destination_raw = get_working_memory_artifact(state.working_memory, "destination")
    if not isinstance(destination_raw, dict) and route is not None:
        destination_raw = raw_shops[0] if raw_shops else None
    destination = (
        container.arcade_payload_mapper.summary_from_row(destination_raw)
        if isinstance(destination_raw, dict)
        else None
    )
    return ChatSessionDetailDto(
        session_id=state.session_id,
        intent=_normalize_intent(state.intent),
        active_subagent=state.active_subagent,
        status=state.status,
        last_error=state.last_error,
        reply=state.working_memory.get("reply") if isinstance(state.working_memory.get("reply"), str) else None,
        shops=shops,
        route=route,
        client_location=_state_client_location(state),
        destination=destination,
        view_payload=get_working_memory_artifact(state.working_memory, "view_payload")
        if isinstance(get_working_memory_artifact(state.working_memory, "view_payload"), dict)
        else None,
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
    return _to_detail(session, container=container)


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
