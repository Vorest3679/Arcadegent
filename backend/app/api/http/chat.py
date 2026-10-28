"""HTTP API layer: chat endpoints backed by session runs of the chat runtime."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import StringConstraints

from app.agent.runtime.session_state import AgentSessionState, SessionOwnershipError
from app.api.deps import get_container
from app.core.container import AppContainer
from app.infra.observability.logger import get_logger, log_ref
from app.protocol.messages import (
    ChatRequest,
    ChatRunDto,
    ChatResponse,
    ChatSessionDetailDto,
    ChatSessionDispatchDto,
    ChatSessionSummaryDto,
)
from app.services.chat_rate_limit import ChatRateLimitExceeded
from app.services.chat_session_detail import normalize_intent
from app.session.models import (
    RunConflictError,
    RunNotFoundError,
    ServiceDrainingError,
    SessionBusyError,
    SessionError,
)

router = APIRouter(prefix="/api", tags=["chat"])
logger = get_logger(__name__)

CANCEL_REASON = "实时连接中断，当前请求已停止；再次输入会继续使用已保存的上下文。"


class ChatSubmissionRequest(ChatRequest):
    """HTTP submissions require an identity; internal runtime calls may omit it."""

    client_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


def _limit_chat(request: ChatSubmissionRequest, http_request: Request, container: AppContainer) -> None:
    # Uvicorn resolves forwarding headers only for explicitly trusted proxies.
    # Never read X-Forwarded-For/X-Real-IP directly from user-controlled headers.
    if http_request.client is None:
        raise HTTPException(status_code=400, detail="无法确定请求来源 IP")
    source_ip = http_request.client.host
    try:
        address = ip_address(source_ip)
        source_ip = str(getattr(address, "ipv4_mapped", None) or address)
    except ValueError:
        # ASGI test transports and Unix sockets may use a non-IP peer name.
        pass
    try:
        container.chat_rate_limiter.acquire(client_id=request.client_id, source_ip=source_ip)
    except ChatRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc


def _http_error(exc: SessionError | SessionOwnershipError) -> HTTPException:
    if isinstance(exc, SessionOwnershipError):
        return HTTPException(status_code=404, detail=f"session '{exc.session_id}' not found")
    if isinstance(exc, (SessionBusyError, RunConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RunNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ServiceDrainingError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="session error")


def _live_status(session_id: str, stored_status: str, *, container: AppContainer):
    """Active runs decide the shown status; otherwise the stored label is used."""
    return "running" if container.run_manager.is_active(session_id) else stored_status


def _current_run(session_id: str, *, container: AppContainer) -> ChatRunDto | None:
    record = container.run_manager.current(session_id)
    if record is None:
        return None
    return ChatRunDto(run_id=record.run_id, status=record.status)


def _to_detail(state: AgentSessionState, *, container: AppContainer) -> ChatSessionDetailDto:
    return container.chat_session_detail.project(
        state,
        status=_live_status(state.session_id, state.status, container=container),
        current_run=_current_run(state.session_id, container=container),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatSubmissionRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    _limit_chat(request, http_request, container)
    logger.info(
        "api.chat.request session_ref=%s intent=%s page_size=%s message_chars=%s",
        log_ref(request.session_id),
        request.intent or "auto",
        request.page_size,
        len(request.message),
    )
    try:
        response, record = await container.chat_runs.run(request)
    except (SessionError, SessionOwnershipError) as exc:
        raise _http_error(exc) from exc
    response = response.model_copy(update={"run_id": record.run_id})
    logger.info(
        "api.chat.response session_ref=%s intent=%s shops=%s",
        log_ref(response.session_id),
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
    request: ChatSubmissionRequest,
    http_request: Request,
    container: AppContainer = Depends(get_container),
) -> ChatSessionDispatchDto:
    _limit_chat(request, http_request, container)
    logger.info(
        "api.chat.dispatch session_ref=%s intent=%s page_size=%s message_chars=%s",
        log_ref(request.session_id),
        request.intent or "auto",
        request.page_size,
        len(request.message),
    )
    try:
        _normalized, record = container.chat_runs.dispatch(request)
    except (SessionError, SessionOwnershipError) as exc:
        raise _http_error(exc) from exc
    return ChatSessionDispatchDto(session_id=record.session_id, run_id=record.run_id, status="running")


@router.post("/chat/sessions/{session_id}/runs/{run_id}/cancel", response_model=ChatSessionDetailDto)
async def cancel_chat_session(
    session_id: str,
    run_id: str = Path(min_length=1, max_length=64),
    client_id: str | None = Query(default=None, min_length=1, max_length=128),
    container: AppContainer = Depends(get_container),
) -> ChatSessionDetailDto:
    session = container.session_store.get_session(session_id, client_id=client_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    try:
        await container.chat_runs.cancel(session_id, run_id=run_id, reason=CANCEL_REASON)
    except SessionError as exc:
        raise _http_error(exc) from exc
    updated = container.session_store.get_session(session_id, client_id=client_id)
    # The DTO mapping may geocode rows; keep it off the event loop.
    return await asyncio.to_thread(_to_detail, updated or session, container=container)


@router.get("/chat/sessions", response_model=list[ChatSessionSummaryDto])
def list_chat_sessions(
    limit: int = Query(default=40, ge=1, le=200),
    client_id: str | None = Query(default=None, min_length=1, max_length=128),
    container: AppContainer = Depends(get_container),
) -> list[ChatSessionSummaryDto]:
    records = container.session_store.list_session_summaries(limit=limit, client_id=client_id)
    summaries: list[ChatSessionSummaryDto] = []
    for record in records:
        status = _live_status(record.session_id, record.status, container=container)
        if record.turn_count == 0 and status != "running":
            continue
        summaries.append(
            ChatSessionSummaryDto.model_validate(
                {**record.model_dump(), "intent": normalize_intent(record.intent), "status": status}
            )
        )
    return summaries


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetailDto)
def get_chat_session(
    session_id: str,
    client_id: str | None = Query(default=None, min_length=1, max_length=128),
    container: AppContainer = Depends(get_container),
) -> ChatSessionDetailDto:
    session = container.session_store.get_session_for_display(session_id, client_id=client_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    return _to_detail(session, container=container)


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_session(
    session_id: str,
    client_id: str | None = Query(default=None, min_length=1, max_length=128),
    container: AppContainer = Depends(get_container),
) -> Response:
    if container.run_manager.is_active(session_id):
        # Only here is a full read needed: a foreign session must stay a 404, not a 409.
        if container.session_store.get_session(session_id, client_id=client_id) is None:
            raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
        raise HTTPException(status_code=409, detail=f"session '{session_id}' is currently running")
    deleted = container.session_store.delete_session(session_id, client_id=client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' not found")
    container.run_manager.forget(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
