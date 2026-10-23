"""Scope-aware lifecycle hooks for the agent execution kernel.

The kernel owns the lifecycle transitions (run/turn/step/worker); domains
observe and customize them through explicit, no-op-by-default hooks. The
context is a small, scoped view over services and state, not a back door to
kernel invariants.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from app.agent.runtime.session_state import KERNEL_MEMORY_KEYS, AgentSessionState
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


class LifecycleScope(StrEnum):
    RUN = "run"
    TURN = "turn"
    STEP = "step"
    WORKER = "worker"


class LifecyclePhase(StrEnum):
    CREATE = "create"
    MOUNT = "mount"
    UPDATE = "update"
    BEFORE_UNMOUNT = "before_unmount"
    UNMOUNTED = "unmounted"
    ERROR = "error"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(value)
    if isinstance(value, list):
        return tuple(value)
    return value


class LifecycleViolation(RuntimeError):
    """A lifecycle hook tried to change kernel-owned state."""


# The only state attribute a domain hook may assign (domain metadata until the
# working-memory split moves it into the domain namespace).
_DOMAIN_WRITABLE_ATTRS = frozenset({"intent"})


class DomainState(Protocol):
    """State surface domain code may rely on inside lifecycle hooks.

    Satisfied by ``StateView`` (what hooks receive) and by ``AgentSessionState``
    (what unit tests pass to domain helpers directly).
    """

    @property
    def session_id(self) -> str: ...

    @property
    def turn_index(self) -> int: ...

    @property
    def working_memory(self) -> dict[str, Any]: ...

    intent: str


class StateView:
    """Live view of an ``AgentSessionState`` handed to lifecycle hooks.

    Attributes always reflect the current state. A writable view returns the
    live ``working_memory`` so hooks can update domain memory (kernel keys are
    checked by the dispatcher) and allows assigning ``intent`` only; other
    attributes cannot be assigned and return detached snapshots (lists come
    back as tuples), so their nested objects cannot mutate kernel state. A read-only
    view (the parent of a worker) returns a copy of ``working_memory`` and
    rejects every assignment.
    """

    __slots__ = ("_state", "_writable")

    def __init__(self, state: AgentSessionState, *, writable: bool = True) -> None:
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_writable", writable)

    @property
    def session_id(self) -> str:
        return self._state.session_id

    @property
    def turn_index(self) -> int:
        return self._state.turn_index

    @property
    def working_memory(self) -> dict[str, Any]:
        memory = self._state.working_memory
        return memory if self._writable else deepcopy(memory)

    @property
    def intent(self) -> str:
        return self._state.intent

    @intent.setter
    def intent(self, value: str) -> None:
        if not self._writable:
            raise AttributeError("lifecycle hooks cannot assign state.intent on a read-only view")
        self._state.intent = value

    def __getattr__(self, name: str) -> Any:
        return _freeze(deepcopy(getattr(self._state, name)))

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _DOMAIN_WRITABLE_ATTRS:
            object.__setattr__(self, name, value)
            return
        raise AttributeError(f"lifecycle hooks cannot assign state.{name}")


@dataclass(frozen=True)
class AgentEnvironment:
    """Long-lived services visible to lifecycle hooks; no session store access."""

    provider: Any
    tools: Any
    skill_registry: Any | None = None


@dataclass
class StepPolicy:
    """Per-step policy a ``step`` create hook may adjust before the model call."""

    runtime_hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunScope:
    session_id: str
    state: StateView


@dataclass
class TurnScope:
    session_id: str
    turn_index: int
    state: StateView
    request: Any
    final_text: str | None = None


@dataclass
class StepScope:
    index: int
    policy: StepPolicy
    state: StateView


@dataclass
class WorkerScope:
    worker_name: str
    run_id: str
    state: StateView
    parent_state: StateView


@dataclass
class LifecycleContext:
    """Scoped context passed to lifecycle hooks.

    Hooks that change state are where a domain applies its memory and intent:
    ``on_turn_mount`` (turn start), ``on_step_update`` (each tool result) and
    ``on_worker_mount`` (seed a worker). ``ctx.state`` / ``ctx.memory`` point
    at the innermost scope's state; kernel-owned memory keys and terminal
    state stay out of reach. Data-producing policies (hints, response,
    worker envelope) remain typed ``TurnDomain`` functions.

    Only the fields relevant to ``scope`` are populated; ``response`` is the
    projected response at ``before_unmount``; ``error``/``reason`` describe an
    error or cancellation path.
    """

    scope: LifecycleScope
    phase: LifecyclePhase
    env: AgentEnvironment
    session_id: str
    turn_index: int | None = None
    worker_run_id: str | None = None
    run: RunScope | None = None
    turn: TurnScope | None = None
    step: StepScope | None = None
    worker: WorkerScope | None = None
    events: Any = None
    logger: Any = None
    response: Any = None
    error: BaseException | None = None
    reason: str | None = None
    tool_result: Any = None
    # Kernel bookkeeping: before_unmount runs at most once per turn.
    before_unmount_done: bool = field(default=False, repr=False)

    @property
    def state(self) -> StateView | None:
        """State of the innermost scope (worker > step > turn > run)."""
        for scope in (self.worker, self.step, self.turn, self.run):
            if scope is not None:
                return scope.state
        return None

    @property
    def memory(self) -> Mapping[str, Any]:
        """Working memory of ``ctx.state``; kernel-owned keys must not change."""
        state = self.state
        return state.working_memory if state is not None else MappingProxyType({})

    def _guarded_states(self) -> list[AgentSessionState]:
        states: list[AgentSessionState] = []
        for scope in (self.run, self.turn, self.step, self.worker):
            view = getattr(scope, "state", None)
            if isinstance(view, StateView) and view._writable and not any(view._state is s for s in states):
                states.append(view._state)
        return states


class BaseLifecycleHooks:
    """No-op lifecycle defaults; override only the hooks a domain needs."""

    async def on_run_create(self, ctx: LifecycleContext) -> None:
        return None

    async def on_run_mount(self, ctx: LifecycleContext) -> None:
        return None

    async def on_run_unmounted(self, ctx: LifecycleContext) -> None:
        return None

    async def on_turn_create(self, ctx: LifecycleContext) -> None:
        return None

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        return None

    async def on_turn_before_unmount(self, ctx: LifecycleContext) -> None:
        return None

    async def on_turn_unmounted(self, ctx: LifecycleContext) -> None:
        return None

    async def on_step_start(self, ctx: LifecycleContext) -> None:
        return None

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        return None

    async def on_step_end(self, ctx: LifecycleContext) -> None:
        return None

    async def on_worker_mount(self, ctx: LifecycleContext) -> None:
        return None

    async def on_worker_unmounted(self, ctx: LifecycleContext) -> None:
        return None

    async def on_error(self, ctx: LifecycleContext) -> None:
        return None


_HOOK_NAMES: dict[tuple[LifecycleScope, LifecyclePhase], str] = {
    (LifecycleScope.RUN, LifecyclePhase.CREATE): "on_run_create",
    (LifecycleScope.RUN, LifecyclePhase.MOUNT): "on_run_mount",
    (LifecycleScope.RUN, LifecyclePhase.UNMOUNTED): "on_run_unmounted",
    (LifecycleScope.TURN, LifecyclePhase.CREATE): "on_turn_create",
    (LifecycleScope.TURN, LifecyclePhase.MOUNT): "on_turn_mount",
    (LifecycleScope.TURN, LifecyclePhase.BEFORE_UNMOUNT): "on_turn_before_unmount",
    (LifecycleScope.TURN, LifecyclePhase.UNMOUNTED): "on_turn_unmounted",
    (LifecycleScope.STEP, LifecyclePhase.CREATE): "on_step_start",
    (LifecycleScope.STEP, LifecyclePhase.UPDATE): "on_step_update",
    (LifecycleScope.STEP, LifecyclePhase.UNMOUNTED): "on_step_end",
    (LifecycleScope.WORKER, LifecyclePhase.MOUNT): "on_worker_mount",
    (LifecycleScope.WORKER, LifecyclePhase.UNMOUNTED): "on_worker_unmounted",
    (LifecycleScope.RUN, LifecyclePhase.ERROR): "on_error",
    (LifecycleScope.TURN, LifecyclePhase.ERROR): "on_error",
    (LifecycleScope.STEP, LifecyclePhase.ERROR): "on_error",
    (LifecycleScope.WORKER, LifecyclePhase.ERROR): "on_error",
}


def _is_default_hook(hooks: Any, name: str) -> bool:
    return getattr(type(hooks), name, None) is getattr(BaseLifecycleHooks, name, None)


def _kernel_memory(state: AgentSessionState) -> dict[str, Any]:
    memory = state.working_memory
    return {key: deepcopy(memory[key]) for key in KERNEL_MEMORY_KEYS if key in memory}


class LifecycleDispatcher:
    """Map ``(scope, phase)`` to a domain hook and await it when needed."""

    def __init__(self, hooks: Any) -> None:
        self._hooks = hooks

    @staticmethod
    def _restore_kernel_memory(state: AgentSessionState, before: dict[str, Any]) -> None:
        memory = state.working_memory
        for key in KERNEL_MEMORY_KEYS:
            memory.pop(key, None)
        memory.update(before)

    async def emit(self, ctx: LifecycleContext) -> None:
        name = _HOOK_NAMES.get((ctx.scope, ctx.phase))
        if name is None:
            return
        hook = getattr(self._hooks, name, None)
        if hook is None or _is_default_hook(self._hooks, name):
            return
        snapshots = [(state, _kernel_memory(state)) for state in ctx._guarded_states()]
        try:
            result = hook(ctx)
            if inspect.isawaitable(result):
                await result
        except BaseException:
            # A hook that mutates kernel state and then raises must not leave
            # the mutation behind for the error path to persist.
            for state, before in snapshots:
                self._restore_kernel_memory(state, before)
            raise
        for state, before in snapshots:
            if _kernel_memory(state) != before:
                # Restore before failing so the violation is never persisted.
                self._restore_kernel_memory(state, before)
                raise LifecycleViolation(f"{name} changed kernel-owned working memory")

    async def emit_safe(self, ctx: LifecycleContext) -> None:
        """Emit a terminal hook without masking the turn's primary outcome."""
        try:
            await self.emit(ctx)
        except Exception:
            logger.exception(
                "lifecycle.hook_failed scope=%s phase=%s",
                ctx.scope.value,
                ctx.phase.value,
            )
