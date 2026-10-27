"""Run event publishing bound to the current async context.

The runtime publishes through a ``RunPublisher`` bound once per run, so nested
main-agent, worker and tool calls do not need to pass the publisher through the
call chain. Event payloads stay exactly as defined by ``app.session.events``.

``ToolEventHooks`` is the default tool lifecycle hook implementation injected
into ``ToolDispatcher``: it turns each settled call into the tool event trace
and log line, in the same order the kernel always published them.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from app.infra.observability.logger import get_logger, log_ref
from app.session.events import RunEvent
from app.session.injector import NullPublisher, RunPublisher

if TYPE_CHECKING:
    from app.agent.runtime.domain_port import ToolResultSignal
    from app.agent.tools.execution import ToolTask
    from app.agent.tools.registry import ToolExecutionResult

logger = get_logger(__name__)

# Bound by ``run_chat`` for the duration of one run; concurrent runs keep their
# own value because the variable is copied into each task's context.
_run_events: ContextVar[RunPublisher] = ContextVar(
    "arcadegent_run_events", default=NullPublisher()
)


def bind_run_events(events: RunPublisher) -> Token[RunPublisher]:
    """Bind ``events`` to the current context and return the reset token."""
    return _run_events.set(events)


def reset_run_events(token: Token[RunPublisher]) -> None:
    """Restore the publisher that was bound before ``bind_run_events``."""
    _run_events.reset(token)


def current_run_events() -> RunPublisher:
    """Publisher bound to the current context (or a null publisher)."""
    return _run_events.get()


def publish(event: str, data: dict[str, Any] | None = None, *, output_id: str | None = None) -> None:
    """Publish one business event on the publisher bound to this context."""
    _run_events.get().publish(event, data, output_id=output_id)


def emit_agent_changed(
    *,
    to_agent: str,
    reason: str,
    from_agent: str | None = None,
    worker_run_id: str | None = None,
) -> None:
    """Emit an agent change event to the replay buffer."""
    payload: dict[str, Any] = {
        "active_subagent": to_agent,
        "to_subagent": to_agent,
        "reason": reason,
    }
    if from_agent:
        payload["from_subagent"] = from_agent
    if worker_run_id:
        payload["worker_run_id"] = worker_run_id
    publish(RunEvent.SUBAGENT_CHANGED, payload)


def publish_assistant_text(text: str, *, output_id: str, stream_mode: str) -> None:
    """Publish reply text as a delta of one output; reasoning is never published."""
    if not text:
        return
    publish(
        RunEvent.ASSISTANT_TOKEN,
        {"delta": text, "active_subagent": "main_agent", "stream_mode": stream_mode},
        output_id=output_id,
    )


SignalFactory = Callable[["ToolExecutionResult"], "ToolResultSignal"]


class ToolEventHooks:
    """Default tool hooks: publish the tool event trace and log the outcome.

    ``signal`` is the domain's ``tool_result_signal`` bound to the state that
    ran the tool. Its events are published before ``tool.completed`` and its
    ``completed_fields`` are merged into that payload, keeping the application
    event order stable.
    """

    def __init__(
        self,
        *,
        session_id: str,
        agent_name: str,
        worker_run_id: str | None = None,
        signal: SignalFactory | None = None,
    ) -> None:
        self._session_id = session_id
        self._agent_name = agent_name
        self._worker_run_id = worker_run_id
        self._signal = signal

    async def before(self, task: "ToolTask") -> None:
        publish(
            RunEvent.TOOL_STARTED,
            {
                "tool": task.call.name,
                "call_id": task.call.call_id,
                "active_subagent": self._agent_name,
                "worker_run_id": self._worker_run_id,
            },
        )

    def on_update(self, task: "ToolTask", partial: dict[str, Any]) -> None:
        """Reserved: no runtime publishes partial tool output yet."""
        return None

    async def after(self, task: "ToolTask", result: "ToolExecutionResult") -> "ToolExecutionResult":
        if result.status == "completed":
            signal = self._signal(result) if self._signal is not None else None
            if signal is not None:
                for event_name, event_data in signal.events:
                    publish(event_name, event_data)
            payload: dict[str, Any] = {
                "tool": result.tool_name,
                "call_id": result.call_id,
                "active_subagent": self._agent_name,
                "worker_run_id": self._worker_run_id,
            }
            if signal is not None:
                payload.update(signal.completed_fields)
            publish(RunEvent.TOOL_COMPLETED, payload)
            logger.info(
                "tool.completed session_ref=%s tool_ref=%s agent_ref=%s",
                log_ref(self._session_id),
                log_ref(result.tool_name),
                log_ref(self._agent_name),
            )
        else:
            error_message = result.error_message or "tool execution failed"
            publish(
                RunEvent.TOOL_FAILED,
                {
                    "tool": result.tool_name,
                    "call_id": result.call_id,
                    "error": error_message,
                    "active_subagent": self._agent_name,
                    "worker_run_id": self._worker_run_id,
                },
            )
            logger.warning(
                "tool.failed session_ref=%s tool_ref=%s agent_ref=%s",
                log_ref(self._session_id),
                log_ref(result.tool_name),
                log_ref(self._agent_name),
            )
        return result
