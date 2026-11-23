"""Agent profile registry used by the ReAct runtime.

The registry holds only declarations supplied by plugins: it never invents a
profile for a known business or assumes that one agent may spawn all others.
The single ``role="entry"`` agent runs user turns; every worker it may dispatch
is listed explicitly in its ``workers`` field.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.roster import AgentDefinition


class SubAgentBuilder:
    """Resolve agent profiles by name and expose kernel-facing executors."""

    def __init__(
        self,
        profiles: Iterable[AgentDefinition],
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._profiles: dict[str, AgentDefinition] = {}
        for profile in profiles:
            if not isinstance(profile, AgentDefinition):
                raise TypeError("invalid_agent_definition")
            if profile.name in self._profiles:
                raise ValueError(f"duplicate_agent:{profile.name}")
            self._profiles[profile.name] = profile
        entries = [profile for profile in self._profiles.values() if profile.role == "entry"]
        if len(entries) != 1:
            raise ValueError("entry_agent_required" if not entries else "multiple_entry_agents")
        self._entry = entries[0]
        for profile in self._profiles.values():
            for worker in profile.workers:
                target = self._profiles.get(worker)
                if target is None or target.role != "worker":
                    raise ValueError(f"unknown_worker:{profile.name}:{worker}")
        self._aliases = dict(aliases or {})
        for alias, target in self._aliases.items():
            if alias in self._profiles:
                raise ValueError(f"alias_shadows_agent:{alias}")
            if target not in self._profiles:
                raise ValueError(f"unknown_alias_target:{alias}:{target}")

    def get(self, name: str) -> AgentDefinition:
        """Resolve a real name first, then a legacy alias, else the entry agent."""
        profile = self._profiles.get(name)
        if profile is not None:
            return profile
        target = self._aliases.get(name)
        return self._profiles[target] if target is not None else self._entry

    @property
    def entry(self) -> AgentDefinition:
        return self._entry

    @property
    def entry_name(self) -> str:
        return self._entry.name

    def worker_names(self) -> tuple[str, ...]:
        """Workers the entry agent may spawn."""
        return self._entry.workers

    def node(self, name: str) -> AgentNode:
        """Resolve a profile into the kernel-facing node policy."""
        profile = self.get(name)
        is_entry = profile.role == "entry"
        return AgentNode(
            profile=profile,
            scope="conversation" if is_entry else "worker",
            publish_text=is_entry,
            spawnable=profile.workers,
            model=profile.model,
            reasoning_effort=profile.reasoning_effort,
        )


__all__ = ["SubAgentBuilder"]
