"""Ports through which the execution kernel reaches turn-level domain behavior.

The kernel owns its own invariants (per-turn scratch reset, ``last_error``
bookkeeping, turn conclusion and worker dispatch via tool capabilities) and
runs a complete turn with ``BaseTurnDomain`` alone. A domain only adds
semantics on top: lifecycle hooks change domain memory (see ``lifecycle``),
and the small protocols below produce data (hints, worker envelopes, the
application response). A domain subclasses ``BaseTurnDomain`` and overrides
only what it needs.

``request`` is the application request object and ``build_response`` returns
the application response; both are opaque to the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.runtime.lifecycle import BaseLifecycleHooks
from app.agent.runtime.session_state import AgentSessionState
from app.agent.tools.registry import ToolExecutionResult


@dataclass(frozen=True)
class ToolResultSignal:
    """Domain-provided events and fields derived from one completed tool result.

    ``events`` are published before the generic ``tool.completed`` event so the
    application's event order stays stable. ``completed_fields`` are merged into
    that event's payload (for example a route distance).
    """

    events: tuple[tuple[str, dict[str, Any]], ...] = ()
    completed_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnReply:
    """Response produced by ``BaseTurnDomain`` when no domain projection exists."""

    session_id: str
    reply: str


class TurnMetadata(Protocol):
    def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
        """Hints merged into the provider request for this state."""

    def session_event_fields(self, state: AgentSessionState) -> dict[str, Any]:
        """Extra fields for the ``session.started`` event payload."""

    def turn_summary(self, state: AgentSessionState) -> str:
        """Short summary for logs (never request or reply content)."""


class ToolObserver(Protocol):
    def tool_result_signal(self, *, state: AgentSessionState, result: ToolExecutionResult) -> ToolResultSignal:
        """Events and fields a completed tool result contributes."""


class WorkerPolicy(Protocol):
    def promote_worker_artifacts(
        self,
        *,
        parent_memory: dict[str, Any],
        worker_memory: dict[str, Any],
        turn_index: int,
    ) -> dict[str, Any]:
        """Merge a finished worker's artifacts into the parent memory."""

    def build_worker_envelope(
        self,
        *,
        worker_name: str,
        run_id: str,
        task: str,
        worker_memory: dict[str, Any],
        final_text: str | None,
        promoted_artifacts: dict[str, Any],
        failed_error: str | None,
    ) -> dict[str, Any]:
        """Build the result envelope returned to the parent agent.

        The kernel reads ``status`` (``failed`` marks the tool as failed),
        ``summary`` and ``error`` from it.
        """


class TurnPresenter(Protocol):
    def fallback_reply(self, state: AgentSessionState, request: Any) -> str:
        """Reply used when the model produced no usable final text."""

    def turn_display_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        """Display payload archived with the final assistant turn."""

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> Any:
        """Project final session state into the application response."""


class TurnDomain(TurnMetadata, ToolObserver, WorkerPolicy, TurnPresenter, Protocol):
    """Turn-level domain behavior injected into the runtime."""


def _preview(text: str, *, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


class BaseTurnDomain(BaseLifecycleHooks):
    """Domain-neutral defaults; the kernel runs correctly with this alone.

    State changes (turn start memory, tool-result projection, worker seeding)
    are lifecycle hooks inherited from ``BaseLifecycleHooks``; the methods
    below are data-producing policies the kernel calls for a return value.
    """

    def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
        return {}

    def session_event_fields(self, state: AgentSessionState) -> dict[str, Any]:
        return {}

    def turn_summary(self, state: AgentSessionState) -> str:
        return ""

    def tool_result_signal(self, *, state: AgentSessionState, result: ToolExecutionResult) -> ToolResultSignal:
        return ToolResultSignal()

    def promote_worker_artifacts(
        self,
        *,
        parent_memory: dict[str, Any],
        worker_memory: dict[str, Any],
        turn_index: int,
    ) -> dict[str, Any]:
        return {}

    def build_worker_envelope(
        self,
        *,
        worker_name: str,
        run_id: str,
        task: str,
        worker_memory: dict[str, Any],
        final_text: str | None,
        promoted_artifacts: dict[str, Any],
        failed_error: str | None,
    ) -> dict[str, Any]:
        status = "failed" if failed_error else "completed"
        summary = (final_text or "").strip() or failed_error or "Worker finished without a summary."
        return {
            "worker": worker_name,
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "result": {"summary": summary},
            "artifacts": promoted_artifacts,
            "missing_fields": [],
            "error": failed_error,
            "task": task,
            "task_preview": _preview(task),
        }

    def fallback_reply(self, state: AgentSessionState, request: Any) -> str:
        reply = state.working_memory.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()
        return "The agent did not produce a reply for this turn."

    def turn_display_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        return None

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> Any:
        return TurnReply(session_id=session_id, reply=final_text)
