"""Application service: run arcade chat turns through the session run manager.

Bridges the chat runtime and the generic session layer: it prepares the stored
session before a run is accepted, wraps the runtime as a run executor, and
records cancelled/failed runs back into the session store when they finish.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.agent.runtime.react_runtime import ReactRuntime
from app.agent.runtime.session_state import SessionOwnershipError, ensure_working_memory_shape
from app.infra.db.protocols import SessionStateRepository
from app.infra.observability.logger import get_logger, log_ref
from app.protocol.messages import ChatRequest, ChatResponse
from app.session.injector import RunContext, RunPublisher
from app.session.models import RunRecord, ServiceDrainingError, SessionBusyError
from app.session.runs import RunManager

logger = get_logger(__name__)

FAILED_RUN_REASON = "执行失败，当前请求已停止；再次输入会继续使用已保存的上下文。"
_REASON_TEXT = {
    "service_shutdown": "服务正在重启，当前请求已停止；再次输入会继续使用已保存的上下文。",
}


def _utc_now_iso() -> str:
    # Same format the runtime stores in session state.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ChatRunService:
    """Dispatch, run inline and cancel chat turns as session runs."""

    def __init__(
        self,
        *,
        runtime: ReactRuntime,
        runs: RunManager,
        session_store: SessionStateRepository,
    ) -> None:
        self._runtime = runtime
        self._runs = runs
        self._session_store = session_store

    def dispatch(self, request: ChatRequest) -> tuple[ChatRequest, RunRecord]:
        """Accept a turn as a background run; returns the normalized request and run."""
        normalized = self._prepare(request)
        record = self._runs.dispatch(
            normalized.session_id or "",
            self._executor(normalized),
            on_terminal=self._on_terminal,
        )
        return normalized, record

    async def run(self, request: ChatRequest) -> tuple[ChatResponse, RunRecord]:
        """Run a turn in the caller's task with the same lifecycle as dispatch."""
        normalized = self._prepare(request)
        return await self._runs.run_inline(
            normalized.session_id or "",
            self._executor(normalized),
            on_terminal=self._on_terminal,
        )

    async def cancel(self, session_id: str, *, run_id: str, reason: str) -> RunRecord:
        """Cancel the named run of the session."""
        return await self._runs.cancel(session_id, run_id, reason=reason)

    def _prepare(self, request: ChatRequest) -> ChatRequest:
        """Normalize the session id, enforce owner scope and mark the stored session running.

        Done before the run is accepted so the session is listed and owned as
        soon as the dispatch response is returned.
        """
        session_id = request.session_id or f"s_{uuid4().hex[:12]}"
        # Check what RunManager would reject before touching the stored session,
        # so a refused request leaves it unchanged.
        if self._runs.draining:
            raise ServiceDrainingError()
        if self._runs.is_active(session_id):
            raise SessionBusyError(session_id)
        state = self._session_store.get_or_create_session(session_id)
        if request.client_id is not None:
            if state.client_id is not None and state.client_id != request.client_id:
                raise SessionOwnershipError(session_id)
            state.client_id = state.client_id or request.client_id
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        self._session_store.save_session(state)
        if request.session_id == session_id:
            return request
        return request.model_copy(update={"session_id": session_id})

    def _executor(self, request: ChatRequest):
        async def execute(context: RunContext) -> ChatResponse:
            return await self._runtime.run_chat(request, events=context.events)

        return execute

    def record_interrupted(self, session_id: str, *, reason: str, events: RunPublisher) -> bool:
        """Mark a session whose run stopped mid-way as failed, keeping its context.

        Returns False when the runtime already stored its own terminal state
        (and published session.failed) for a failure it handled itself.
        """
        state = self._session_store.get_session(session_id)
        if state is None or state.status != "running":
            return False
        state.status = "failed"
        state.last_error = reason
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        state.working_memory["last_error"] = {"message": reason, "source": "stream"}
        state.updated_at = _utc_now_iso()
        self._session_store.save_session(state)
        events.publish("session.failed", {"error": reason, "active_subagent": state.active_subagent})
        return True

    async def _on_terminal(self, record: RunRecord, events: RunPublisher) -> None:
        if record.status == "completed":
            return
        reason = _REASON_TEXT.get(record.cancel_reason or "", record.cancel_reason) or FAILED_RUN_REASON
        if self.record_interrupted(record.session_id, reason=reason, events=events):
            logger.info(
                "chat.run.interrupted session_ref=%s run_ref=%s status=%s",
                log_ref(record.session_id),
                log_ref(record.run_id),
                record.status,
            )
