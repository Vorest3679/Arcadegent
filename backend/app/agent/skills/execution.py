"""Transient skill snapshots and a task-local binding for tool executors."""

from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from app.agent.skills.registry import SkillError, SkillRegistry, SkillResource, normalize_resource_path


@dataclass
class SkillExecution:
    """One user turn or worker invocation; not serialized into session storage.

    ``load`` runs from builtin tools dispatched through ``asyncio.to_thread``,
    so parallel ``read_skill`` calls share this object across threads. The lock
    covers the check-and-add of ``resources``/``loaded_bytes`` (including the
    read that feeds it), keeping the loaded-byte cap and deduplication exact.
    """

    resources: dict[tuple[str, str], SkillResource] = field(default_factory=dict)
    loaded_bytes: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def load(self, registry: SkillRegistry, *, agent_name: str, name: str, path: str) -> dict:
        path = normalize_resource_path(path)
        if not registry.is_allowed(name, agent_name):
            raise SkillError("skill_unavailable")
        key = (name, path)
        with self._lock:
            if key not in self.resources:
                resource = registry.read_skill(name, path, agent_name=agent_name)
                if self.loaded_bytes + resource.size_bytes > registry.config.max_loaded_bytes:
                    raise SkillError("skill_execution_capacity_exceeded")
                self.resources[key] = resource
                self.loaded_bytes += resource.size_bytes
        return {"name": name, "path": path, "status": "loaded"}

    def __deepcopy__(self, memo: dict[int, Any]) -> "SkillExecution":
        # Session snapshots clone the runtime state; the lock is per-instance
        # and must not be copied. Resources are inert snapshots, so they can be.
        clone = SkillExecution(
            resources=deepcopy(self.resources, memo),
            loaded_bytes=self.loaded_bytes,
        )
        memo[id(self)] = clone
        return clone


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
