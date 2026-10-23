"""Run event publishing bound to the current async context.

The runtime publishes through a ``RunPublisher`` bound once per run, so nested
main-agent, worker and tool calls do not need to pass the publisher through the
call chain. Event payloads stay exactly as defined by ``app.session.events``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from app.session.events import RunEvent
from app.session.injector import NullPublisher, RunPublisher

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
