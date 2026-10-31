"""Storage port of the agent kernel.

``SessionStore`` is the part of the session repository the kernel calls. The
application's ``SessionStateRepository`` (``app.infra.db.protocols``) extends
it with history listing and display reads, which depend on application DTOs
and stay outside the kernel.
"""

from __future__ import annotations

from typing import Protocol

from app.agent.runtime.session_state import AgentSessionState


class SessionStore(Protocol):
    """Load and persist the session state of one run."""

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        """Fetch an existing session or return a fresh, unpersisted one."""
        ...

    def save_session(self, state: AgentSessionState) -> None:
        """Persist one mutated session state."""
        ...
