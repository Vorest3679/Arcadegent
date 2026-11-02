"""Tool actions: run a model's tool calls and record their results.

Shared by the main agent and workers. Execution (argument preparation,
evidence and the per-call task) lives in ``tools/execution``; this module owns
the runtime side: the batch's event hooks, worker dispatch for ``spawn_agent``
calls, the supervisor's wait policy, the evidence turn, kernel memory
bookkeeping and the domain's ``update`` lifecycle hook.

Calls execute concurrently only when the descriptor declares
``execution_mode="parallel"`` and they are adjacent in the model's batch. An
exclusive call is a barrier. Whatever the overlap, results are committed and
recorded in model call order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.agent._logging import get_logger, log_ref
from app.agent.artifacts.sink import ArtifactSink
from app.agent.context.turn_recorder import record_tool_result_turn
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.events import ToolEventHooks
from app.agent.runtime.lifecycle import LifecycleContext, LifecyclePhase
from app.agent.runtime.ports import SessionStore
from app.agent.runtime.session_state import AgentSessionState
from app.agent.runtime.supervisor import LoopSupervisor, WaitPolicy
from app.agent.subagents.subagent_builder import SubAgentBuilder, SubAgentProfile
from app.agent.tools.execution import (
    CAP_SPAWN_AGENT,
    ToolDispatcher,
    ToolLifecycleHooks,
    ToolTask,
)
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry

logger = get_logger(__name__)

if TYPE_CHECKING:
    from app.agent.llm.provider_adapter import ModelToolCall
    from app.agent.runtime.worker_runner import WorkerDispatch

LifecycleCall = Callable[[LifecycleContext], Awaitable[None]]
# (state, worker_name, task, spawnable, budget) -> pending worker dispatch.
WorkerSpawner = Callable[..., Awaitable["WorkerDispatch"]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dispatch_error_text(exc: Exception, *, limit: int = 280) -> str:
    """Client-visible error text, bounded like the kernel's ``session.failed``."""
    if not str(exc):
        return type(exc).__name__
    compact = " ".join(f"{type(exc).__name__}: {exc}".split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _tool_segments(
    tool_calls: list["ModelToolCall"],
    modes: dict[str, str],
) -> Iterator[list["ModelToolCall"]]:
    """Split a batch into concurrent runs separated by exclusive barriers."""
    segment: list["ModelToolCall"] = []
    for call in tool_calls:
        if modes.get(call.name, "exclusive") == "parallel":
            segment.append(call)
            continue
        if segment:
            yield segment
            segment = []
        yield [call]
    if segment:
        yield segment


class _SpawnAgentHook:
    """Turn a completed ``spawn_agent`` call into its worker result envelope.

    ``after`` runs inside the tool task and only starts the worker, so sibling
    workers overlap. ``commit`` runs in the parent loop in model call order:
    it commits the worker (artifacts, evidence, usage, envelope), replaces the
    tool result and then lets the event hook publish ``tool.completed``.
    """

    def __init__(
        self,
        *,
        next_hooks: ToolLifecycleHooks,
        spawn_worker: WorkerSpawner,
        state: AgentSessionState,
        spawnable: tuple[str, ...],
        budget: RunBudget | None,
    ) -> None:
        self._next = next_hooks
        self._spawn_worker = spawn_worker
        self._state = state
        self._spawnable = spawnable
        self._budget = budget

    async def before(self, task: ToolTask) -> None:
        await self._next.before(task)

    def on_update(self, task: ToolTask, partial: dict[str, Any]) -> None:
        self._next.on_update(task, partial)

    async def after(self, task: ToolTask, result: ToolExecutionResult) -> ToolExecutionResult:
        if result.status == "completed" and CAP_SPAWN_AGENT in result.capabilities:
            try:
                task.deferred = await self._spawn_worker(
                    state=self._state,
                    worker_name=str(result.output.get("worker") or "").strip(),
                    task=str(result.output.get("task") or "").strip(),
                    spawnable=self._spawnable,
                    budget=self._budget,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = _worker_dispatch_failure(result, exc)
        return await self._next.after(task, result)

    async def commit(self, task: ToolTask, result: ToolExecutionResult) -> ToolExecutionResult:
        dispatch = task.deferred
        if dispatch is not None:
            try:
                envelope = await dispatch.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A worker failure is a tool failure: the main agent receives a
                # failed result and can recover instead of losing the turn.
                # Storage/publisher failures resurface when that result is
                # recorded and announced, so real infrastructure errors still
                # fail the run. The worker's lifecycle error/unmount and its
                # evidence/usage recovery already ran inside ``finish``.
                result = _worker_dispatch_failure(result, exc)
            else:
                result = _worker_envelope_result(result, envelope)
        return await self._next.commit(task, result)


def _worker_dispatch_failure(result: ToolExecutionResult, exc: Exception) -> ToolExecutionResult:
    error_message = _dispatch_error_text(exc)
    return ToolExecutionResult(
        call_id=result.call_id,
        tool_name=result.tool_name,
        status="failed",
        output={"error": {"type": "worker_dispatch_error", "message": error_message}},
        error_message=error_message,
        capabilities=result.capabilities,
        artifacts=result.artifacts,
    )


def _worker_envelope_result(
    result: ToolExecutionResult,
    envelope: dict[str, Any],
) -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id=result.call_id,
        tool_name=result.tool_name,
        status="failed" if envelope.get("status") == "failed" else "completed",
        output=envelope,
        error_message=envelope.get("error"),
        capabilities=result.capabilities,
        artifacts=result.artifacts,
    )


class ToolActions:
    """Executes tool-call batches for one agent and records every result."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        domain: TurnDomain,
        session_store: SessionStore,
        subagent_builder: SubAgentBuilder,
        emit: LifecycleCall,
        spawn_worker: WorkerSpawner,
        artifacts: ArtifactSink | None = None,
        wait_policy: WaitPolicy | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._domain = domain
        self._artifacts = artifacts or ArtifactSink()
        self._session_store = session_store
        self._subagent_builder = subagent_builder
        self._emit = emit
        self._spawn_worker = spawn_worker
        self._wait_policy = wait_policy

    async def execute_tool_calls(
        self,
        *,
        session_id: str,
        session_state: AgentSessionState,
        tool_calls: list[Any],
        profile: SubAgentProfile,
        worker_run_id: str | None = None,
        persist: bool = True,
        step_ctx: LifecycleContext | None = None,
        budget: RunBudget | None = None,
    ) -> list[ToolExecutionResult]:
        """Run one batch of tool calls, committing results in model order.

        Adjacent ``parallel`` calls overlap; each ``exclusive`` call is a
        barrier. The event hooks publish the trace in model order once each
        call settles. Cancellation finalizes every submitted call so no event
        span or asyncio task is left open.
        """
        events = ToolEventHooks(
            session_id=session_id,
            agent_name=profile.name,
            worker_run_id=worker_run_id,
            signal=lambda result: self._domain.tool_result_signal(state=session_state, result=result),
        )
        dispatcher = ToolDispatcher(
            tool_registry=self._tool_registry,
            hooks=_SpawnAgentHook(
                next_hooks=events,
                spawn_worker=self._spawn_worker,
                state=session_state,
                spawnable=self._subagent_builder.node(profile.name).spawnable,
                budget=budget,
            ),
        )
        supervisor = LoopSupervisor(dispatcher=dispatcher, policy=self._wait_policy)
        try:
            modes = await self._tool_registry.execution_modes([call.name for call in tool_calls])
        except Exception:
            # Descriptor collection (including an MCP outage) must not fail the
            # whole step before any call gets a tool result. Falling back to
            # exclusive keeps the batch safe; each call still reports its own
            # execution failure through the registry.
            logger.warning(
                "tool.execution_modes_failed session_ref=%s tool_count=%s",
                log_ref(session_id),
                len(tool_calls),
            )
            modes = {}
        results: list[ToolExecutionResult] = []
        pending: list[ToolTask] = []
        batch_failed = False

        async def flush() -> None:
            nonlocal batch_failed
            while pending:
                task = pending[0]
                result = await dispatcher.settle(task)
                if not task.recorded:
                    task.recorded = True
                    results.append(result)
                    if not task.cancelled:
                        await self.record_tool_result(
                            state=session_state,
                            result=result,
                            agent_name=profile.name,
                            worker_run_id=worker_run_id,
                            tool_arguments=task.prepared_args,
                            persist=persist,
                            argument_evidence=task.argument_evidence,
                            step_ctx=step_ctx,
                            clear_error=not batch_failed,
                        )
                        batch_failed = batch_failed or result.status != "completed"
                pending.pop(0)
                supervisor.release(task)

        try:
            for segment in _tool_segments(tool_calls, modes):
                for call in segment:
                    task = await dispatcher.submit(
                        call,
                        session_id=session_id,
                        runtime_context=session_state.working_memory,
                        allowed_tools=profile.allowed_tools,
                        agent_name=profile.name,
                        skill_execution=session_state.skill_execution,
                    )
                    supervisor.track(task)
                    pending.append(task)
                await supervisor.wait(tuple(pending))
                await flush() # wait等完整段所有任务结束后才继续
        except BaseException as exc:
            # Any failure (storage, event publishing, a bug in a hook, or a
            # second cancellation) must not leave sibling parallel tasks
            # running. Finalize every tracked call before re-raising.
            await supervisor.cancel_all()
            if isinstance(exc, asyncio.CancelledError):
                # The completed siblings keep their evidence; synthesized
                # cancellations are not recorded (the run is ending).
                await flush()
            raise
        return results

    # Cancellation note: a sibling whose worker already finished is committed
    # normally before the cancellation propagates (artifact promotion, usage,
    # evidence and its envelope are kept exactly once, per stage 4/7). Only
    # calls whose execution task was actually cancelled skip their evidence.
    async def record_tool_result(
        self,
        *,
        state: AgentSessionState,
        result: ToolExecutionResult,
        agent_name: str,
        worker_run_id: str | None,
        tool_arguments: dict[str, Any] | None,
        persist: bool,
        argument_evidence: dict[str, Any] | None = None,
        step_ctx: LifecycleContext | None = None,
        clear_error: bool = True,
    ) -> None:
        """Record a tool result: evidence turn, artifacts, kernel memory, domain projection.

        Tool-declared artifacts win; otherwise the domain's transitional mapping
        supplies them. ``clear_error`` is False when an earlier call in the same
        batch failed, so a later success does not erase that failure's
        ``last_error``.
        """
        record_tool_result_turn(
            state=state,
            result=result,
            agent_name=agent_name,
            worker_run_id=worker_run_id,
            tool_arguments=tool_arguments,
            persist=persist,
            argument_evidence=argument_evidence,
            session_store=self._session_store if persist else None,
        )
        artifacts = result.artifacts
        if not artifacts and result.status == "completed":
            # Transitional mapping for tools that do not describe their own
            # artifacts yet. Tool-declared artifacts always take precedence.
            artifacts = self._domain.artifacts_from_result(state=state, result=result)
        if artifacts:
            self._artifacts.apply(state, artifacts)
        memory = state.working_memory
        if result.status != "completed":
            error_payload = result.output.get("error")
            memory["last_error"] = error_payload if error_payload is not None else result.error_message
            state.updated_at = _utc_now_iso()
        elif clear_error:
            memory.pop("last_error", None)
        if step_ctx is not None:
            # The domain projects the result into its memory after the kernel
            # has applied its own bookkeeping.
            step_ctx.tool_result = result
            step_ctx.phase = LifecyclePhase.UPDATE
            await self._emit(step_ctx)
