"""Agent definitions and rosters contributed by plugins.

A roster is the set of agents one plugin contributes: their definitions, the
base prompt they share and the legacy ids that still name them. Prompt
directories are plugin resources (``PluginResources.prompts``), not part of
the roster. The generic layer never invents profiles; the host merges the
installed plugins' rosters into one.

Definitions may also come from YAML. In that case the YAML file overlays a
roster declared in code: active files update the addressed agent's prompt,
tools, model and worker topology, while ids are mapped through the caller's
alias table. YAML keys that only described the previous in-repo routing
heuristics are ignored, except for ``skill_files`` which fails loudly because
skills are now plugin resources. Unreadable or malformed files fail too.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml

AgentRole = Literal["entry", "worker"]


class AgentRosterError(ValueError):
    """An agent definition or roster is invalid.

    Messages are stable codes; they never contain file paths or file contents.
    """


def _names(values: Iterable[str], code: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise AgentRosterError(code)
    items = tuple(values)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise AgentRosterError(code)
    return items


@dataclass(frozen=True)
class AgentDefinition:
    """One executable agent: prompt, tool scope and dispatch topology."""

    name: str
    prompt: str  # a file name inside the owning plugin's prompt directories
    allowed_tools: tuple[str, ...]
    role: AgentRole = "worker"
    # Workers this agent may dispatch through ``invoke_worker``. Static
    # topology only; the kernel keeps the field name ``spawnable``.
    workers: tuple[str, ...] = ()
    model: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise AgentRosterError("agent_name_required")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise AgentRosterError(f"agent_prompt_required:{self.name}")
        if self.role not in {"entry", "worker"}:
            raise AgentRosterError(f"invalid_agent_role:{self.name}")
        object.__setattr__(self, "allowed_tools", _names(self.allowed_tools, f"invalid_allowed_tools:{self.name}"))
        object.__setattr__(self, "workers", _names(self.workers, f"invalid_workers:{self.name}"))


@dataclass(frozen=True)
class AgentRoster:
    """Agents one plugin contributes, plus their shared base prompt and legacy ids."""

    agents: tuple[AgentDefinition, ...] = ()
    base_prompt: str | None = None  # a file name inside the owning plugin's prompt directories
    # Legacy session/YAML ids mapped to this roster's own agent names. Real
    # agent names always win over aliases.
    aliases: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        agents = tuple(self.agents)
        if not all(isinstance(agent, AgentDefinition) for agent in agents):
            raise AgentRosterError("invalid_agent_definition")
        if self.base_prompt is not None and (not isinstance(self.base_prompt, str) or not self.base_prompt.strip()):
            raise AgentRosterError("invalid_base_prompt")
        aliases = dict(self.aliases)
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in aliases.items()):
            raise AgentRosterError("invalid_agent_alias")
        object.__setattr__(self, "agents", agents)
        object.__setattr__(self, "aliases", MappingProxyType(aliases))

    @classmethod
    def from_yaml(
        cls,
        definitions_dir: Path,
        *,
        aliases: Mapping[str, str] | None = None,
        base: AgentRoster | None = None,
    ) -> AgentRoster:
        """Read ``*.yaml`` definitions and overlay them on ``base`` (or build a new roster)."""
        if not definitions_dir.is_dir():
            raise AgentRosterError("missing_agent_definitions_dir")
        alias_map = dict(aliases or {})
        definitions: dict[str, AgentDefinition] = (
            {agent.name: agent for agent in base.agents} if base is not None else {}
        )
        for path in sorted(definitions_dir.glob("*.yaml")):
            payload = _read_yaml(path)
            if "skill_files" in payload:
                raise AgentRosterError("unsupported_agent_yaml_key:skill_files")
            if _read_status(payload) != "active":
                continue
            name = _definition_name(
                payload,
                aliases=alias_map,
                known=definitions,
                allow_new=base is None,
            )
            if name is None:
                continue
            definitions[name] = _overlay_definition(payload, name=name, current=definitions.get(name))

        roster = base or cls()
        return cls(
            agents=tuple(definitions.values()),
            base_prompt=roster.base_prompt,
            aliases={**roster.aliases, **alias_map},
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise AgentRosterError("invalid_agent_yaml") from None
    if not isinstance(raw, dict):
        raise AgentRosterError("invalid_agent_yaml")
    return raw


def _read_status(payload: dict[str, Any]) -> str:
    raw = payload.get("status")
    if not isinstance(raw, str):
        return "active"
    value = raw.strip().lower()
    return value if value in {"active", "planned"} else "active"


def _definition_name(
    payload: dict[str, Any],
    *,
    aliases: Mapping[str, str],
    known: Mapping[str, AgentDefinition],
    allow_new: bool,
) -> str | None:
    raw_name = payload.get("subagent_name")
    if isinstance(raw_name, str):
        normalized = raw_name.strip()
        if normalized and (allow_new or normalized in known or normalized in aliases.values()):
            return normalized
    raw_id = payload.get("id")
    if isinstance(raw_id, str):
        key = raw_id.strip()
        if key in known:
            return key
        if key in aliases:
            return aliases[key]
        if allow_new:
            return key or None
    return None


def _overlay_definition(
    payload: dict[str, Any],
    *,
    name: str,
    current: AgentDefinition | None,
) -> AgentDefinition:
    fallback_prompt = current.prompt if current is not None else f"{name}.md"
    prompt = _optional_str(payload, "prompt_file") or fallback_prompt
    overlay_tools = _allowed_tools(payload)
    mode = _allowed_tools_mode(payload)
    if current is None or (mode == "replace" and overlay_tools):
        allowed_tools = overlay_tools
    else:
        allowed_tools = tuple(dict.fromkeys((*current.allowed_tools, *overlay_tools)))
    role = _read_role(payload) or (current.role if current is not None else "worker")
    workers = _read_workers(payload)
    if workers is None:
        workers = current.workers if current is not None else ()
    return AgentDefinition(
        name=name,
        prompt=prompt,
        allowed_tools=allowed_tools,
        role=role,
        workers=workers,
        model=_optional_str(payload, "model") or (current.model if current is not None else None),
        reasoning_effort=(
            _optional_str(payload, "reasoning_effort")
            or (current.reasoning_effort if current is not None else None)
        ),
    )


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    raw = payload.get(key)
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def _read_role(payload: dict[str, Any]) -> AgentRole | None:
    raw = payload.get("role")
    if raw in {"entry", "worker"}:
        return raw  # type: ignore[return-value]
    return None


def _read_workers(payload: dict[str, Any]) -> tuple[str, ...] | None:
    if "workers" not in payload:
        return None
    raw = payload.get("workers")
    if not isinstance(raw, list):
        return ()
    return tuple(value.strip() for value in raw if isinstance(value, str) and value.strip())


def _allowed_tools_mode(payload: dict[str, Any]) -> str:
    raw = payload.get("allowed_tools_mode")
    if not isinstance(raw, str):
        return "merge"
    value = raw.strip().lower()
    return value if value in {"merge", "replace"} else "merge"


def _allowed_tools(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("allowed_tools")
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


__all__ = ["AgentDefinition", "AgentRole", "AgentRoster", "AgentRosterError"]
