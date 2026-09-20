"""Transient skill snapshots and a task-local binding for tool executors."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from app.agent.skills.registry import SkillError, SkillRegistry, SkillResource, normalize_resource_path


@dataclass
class SkillExecution:
    """One user turn or worker invocation; not serialized into session storage."""

    resources: dict[tuple[str, str], SkillResource] = field(default_factory=dict)
    loaded_bytes: int = 0

    def load(self, registry: SkillRegistry, *, agent_name: str, name: str, path: str) -> dict:
        path = normalize_resource_path(path)
        if not registry.is_allowed(name, agent_name):
            raise SkillError("skill_unavailable")
        key = (name, path)
        if key not in self.resources:
            resource = registry.read_skill(name, path, agent_name=agent_name)
            if self.loaded_bytes + resource.size_bytes > registry.config.max_loaded_bytes:
                raise SkillError("skill_execution_capacity_exceeded")
            self.resources[key] = resource
            self.loaded_bytes += resource.size_bytes
        return {"name": name, "path": path, "status": "loaded"}


@dataclass(frozen=True)
class SkillInvocation:
    agent_name: str
    execution: SkillExecution


_invocation: ContextVar[SkillInvocation | None] = ContextVar("skill_invocation", default=None)


class SkillExecutionBinding:
    """Explicit context manager for a task-local skill invocation binding."""

    def __init__(self, agent_name: str, execution: SkillExecution) -> None:
        self._invocation = SkillInvocation(agent_name, execution)
        self._token: Token[SkillInvocation | None] | None = None

    def __enter__(self) -> None:
        self._token = _invocation.set(self._invocation)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        token = self._token
        self._token = None
        if token is not None:
            _invocation.reset(token)


def bind_skill_execution(agent_name: str, execution: SkillExecution) -> SkillExecutionBinding:
    """Bind trusted runtime identity; asyncio.to_thread propagates this context."""
    return SkillExecutionBinding(agent_name, execution)


def current_skill_invocation() -> SkillInvocation:
    invocation = _invocation.get()
    if invocation is None:
        raise SkillError("skill_execution_context_required")
    return invocation
