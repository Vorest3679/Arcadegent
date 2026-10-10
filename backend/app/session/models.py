"""Run state: statuses, the run record, legal transitions and session errors.

Pure data and rules only. Nothing here runs a task, stores events or knows
about agents, HTTP or business payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

RunStatus = Literal["pending", "running", "cancelling", "completed", "failed", "cancelled"]

TERMINAL: frozenset[RunStatus] = frozenset({"completed", "failed", "cancelled"})

_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    "pending": frozenset({"running", "cancelling", "failed"}),
    "running": frozenset({"cancelling", "completed", "failed"}),
    # A cancel that arrives after execution already ended does not rewrite
    # the outcome: the run finishes with what actually happened.
    "cancelling": frozenset({"cancelled", "completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    """Management record of one run; never carries business data."""

    session_id: str
    run_id: str
    status: RunStatus = "pending"
    accepted_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    # Stable code such as "executor_failed" or "persist_failed"; never a raw
    # exception message, so it is safe to show to clients.
    error_code: str | None = None
    cancel_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.accepted_at:
            self.accepted_at = utc_now_iso()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


class SessionError(RuntimeError):
    """Base class for session-layer errors."""


class SessionBusyError(SessionError):
    """The session already has an active run (HTTP 409)."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session '{session_id}' is already running")
        self.session_id = session_id


class RunNotFoundError(SessionError):
    """The requested run is unknown for this session (HTTP 404)."""

    def __init__(self, session_id: str, run_id: str | None = None) -> None:
        target = f"run '{run_id}'" if run_id else "run"
        super().__init__(f"{target} of session '{session_id}' not found")
        self.session_id = session_id
        self.run_id = run_id


class RunConflictError(SessionError):
    """The targeted run is not the session's current run (HTTP 409)."""

    def __init__(self, session_id: str, run_id: str) -> None:
        super().__init__(f"run '{run_id}' is not the current run of session '{session_id}'")
        self.session_id = session_id
        self.run_id = run_id


class ServiceDrainingError(SessionError):
    """The service is shutting down and accepts no new runs (HTTP 503)."""

    def __init__(self) -> None:
        super().__init__("service is shutting down")


class InvalidTransitionError(SessionError):
    """A programming error: an illegal run status change was requested."""


def transition(record: RunRecord, to: RunStatus, *, error_code: str | None = None) -> bool:
    """Move ``record`` to ``to`` following the allowed transitions.

    pending -> running -> completed/failed; pending/running -> cancelling ->
    cancelled, or completed/failed when execution ended before the cancel
    took effect.

    Returns False without changes when the record already has that status
    (repeated terminal or cancelling requests are idempotent); raises
    InvalidTransitionError for any other illegal change.
    """
    if record.status == to:
        return False
    if to not in _ALLOWED[record.status]:
        raise InvalidTransitionError(f"run '{record.run_id}' cannot move from {record.status} to {to}")
    record.status = to
    now = utc_now_iso()
    if to == "running":
        record.started_at = now
    if to in TERMINAL:
        record.finished_at = now
    if error_code is not None:
        record.error_code = error_code
    return True
