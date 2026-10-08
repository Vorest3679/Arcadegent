"""Entry points handed to the runtime and application services.

The runtime only sees these: a run-bound publisher, the run context and the
executor/terminal-hook signatures. It never touches the run registry or the
run log directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.session.models import RunRecord


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


@dataclass(frozen=True)
class RunContext:
    """What an executor receives: the run identity and its bound publisher."""

    session_id: str
    run_id: str
    events: RunPublisher


#: The work of one run: receives the context and returns the caller's result.
RunExecutor = Callable[[RunContext], Awaitable[Any]]

#: Called once while a run finishes, before its terminal event is published and
#: the log is sealed, so the application can persist business state and publish
#: its own closing business event.
TerminalHook = Callable[[RunRecord, RunPublisher], Awaitable[None]]


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


@dataclass
class CollectingPublisher:
    """Publisher that keeps events in memory; for evaluation and tests."""

    events: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)

    def publish(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        output_id: str | None = None,
    ) -> None:
        self.events.append((event, dict(data or {}), output_id))

    def names(self) -> list[str]:
        return [name for name, _data, _output_id in self.events]
