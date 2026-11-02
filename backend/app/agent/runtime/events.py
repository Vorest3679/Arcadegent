"""Run event ports and publishing bound to the current async context.

The kernel publishes through a ``RunPublisher`` bound once per run, so nested
main-agent, worker and tool calls do not need to pass the publisher through the
call chain. ``KernelEvent`` names the business events the kernel itself
publishes; the application's full catalog (``app.session.events``) is built
from them plus application events. Domains publish their own events under the
``domain.<namespace>.<name>`` form (see ``domain_event``), which the catalog
accepts without listing them.

``ToolEventHooks`` is the default tool lifecycle hook implementation injected
into ``ToolDispatcher``: it turns each settled call into the tool event trace
and log line, in the same order the kernel always published them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextvars import ContextVar, Token
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from app.agent._logging import get_logger, log_ref

if TYPE_CHECKING:
    from app.agent.runtime.domain_port import ToolResultSignal
    from app.agent.tools.execution import ToolTask
    from app.agent.tools.registry import ToolExecutionResult

logger = get_logger(__name__)


class RunPublisher(Protocol):
    """Run-bound publishing port; must not interpret business payloads."""

    def publish(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        output_id: str | None = None,
    ) -> None:
        """Append one business event to the run's log; dropped once the run is sealed."""


class NullPublisher:
    """Publisher that drops everything; used when nobody observes a run."""

    def publish(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        output_id: str | None = None,
    ) -> None:
        return None


class KernelEvent(StrEnum):
    """Business events published by the kernel; payloads are documented in ``app.session.events``."""

    SESSION_STARTED = "session.started"
    SUBAGENT_CHANGED = "subagent.changed"
    WORKER_STARTED = "worker.started"
    WORKER_COMPLETED = "worker.completed"
    WORKER_FAILED = "worker.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    ASSISTANT_TOKEN = "assistant.token"
    ASSISTANT_COMPLETED = "assistant.completed"
    SESSION_FAILED = "session.failed"


DOMAIN_EVENT_PREFIX = "domain."
_DOMAIN_SEGMENT = r"[a-z][a-z0-9_]*"
_DOMAIN_EVENT = re.compile(rf"domain\.{_DOMAIN_SEGMENT}\.{_DOMAIN_SEGMENT}")


def domain_event(namespace: str, name: str) -> str:
    """Event name ``domain.<namespace>.<name>`` for a domain-defined event.

    Both parts are lowercase identifiers (``[a-z][a-z0-9_]*``); anything else
    raises ``ValueError`` so a bad name fails where it is declared.
    """
    event = f"{DOMAIN_EVENT_PREFIX}{namespace}.{name}"
    if not is_domain_event(event):
        raise ValueError(f"invalid domain event name: {event!r}")
    return event


def is_domain_event(event: str) -> bool:
    """Whether ``event`` has the ``domain.<namespace>.<name>`` form."""
    return _DOMAIN_EVENT.fullmatch(event) is not None


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
    publish(KernelEvent.SUBAGENT_CHANGED, payload)


def publish_assistant_text(text: str, *, output_id: str, stream_mode: str) -> None:
    """Publish reply text as a delta of one output; reasoning is never published."""
    if not text:
        return
    publish(
        KernelEvent.ASSISTANT_TOKEN,
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

    Publishing lives in ``commit`` rather than ``after``: commit runs in the
    loop coroutine and in model call order, so overlapping parallel calls still
    produce one deterministic tool trace after their execution settled.
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
            KernelEvent.TOOL_STARTED,
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
        """Execution finished inside the call's task; publishing waits for commit."""
        return result

    async def commit(self, task: "ToolTask", result: "ToolExecutionResult") -> "ToolExecutionResult":
        """Publish ``tool.completed``/``tool.failed`` and log the settled call."""
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
            publish(KernelEvent.TOOL_COMPLETED, payload)
            logger.info(
                "tool.completed session_ref=%s tool_ref=%s agent_ref=%s",
                log_ref(self._session_id),
                log_ref(result.tool_name),
                log_ref(self._agent_name),
            )
        else:
            error_message = result.error_message or "tool execution failed"
            publish(
                KernelEvent.TOOL_FAILED,
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
