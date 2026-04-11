"""Event layer: strongly-typed stream events used by ReplayBuffer and SSE API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


EventName = Literal[
    "session.started",
    "subagent.changed",
    "worker.started",
    "worker.completed",
    "worker.failed",
    "assistant.token",
    "tool.started",
    "tool.progress",
    "tool.completed",
    "tool.failed",
    "navigation.route_ready",
    "assistant.completed",
    "session.failed",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StreamEvent(BaseModel):
    """Single stream event persisted in memory for SSE replay."""

    id: int
    session_id: str
    event: EventName
    at: str = Field(default_factory=utc_now_iso)
    data: dict[str, Any] = Field(default_factory=dict)
