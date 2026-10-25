"""Resolved agent node: profile plus kernel-facing execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.agent.subagents.subagent_builder import SubAgentProfile


@dataclass(frozen=True)
class AgentNode:
    """One executable agent node derived from a subagent profile.

    ``spawnable`` is static topology (which workers this node may dispatch);
    per-turn tightening belongs to ``StepPolicy``, not this object.
    """

    profile: "SubAgentProfile"
    scope: Literal["conversation", "worker"]
    publish_text: bool
    spawnable: tuple[str, ...] = ()
    # None keeps the provider request unchanged.
    model: str | None = None
    reasoning_effort: str | None = None
