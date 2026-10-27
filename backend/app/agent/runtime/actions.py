"""Tool actions: run a model's tool calls and record their results.

Shared by the main agent and workers. Execution (argument preparation,
evidence and the per-call task) lives in ``tools/execution``; this module owns
the runtime side: the batch's event hooks, worker dispatch for ``spawn_agent``
calls, the evidence turn, kernel memory bookkeeping and the domain's ``update``
lifecycle hook.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.agent.context.turn_recorder import record_tool_result_turn
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.events import ToolEventHooks
from app.agent.runtime.lifecycle import LifecycleContext, LifecyclePhase
from app.agent.runtime.session_state import AgentSessionState
from app.agent.subagents.subagent_builder import SubAgentBuilder, SubAgentProfile
from app.agent.tools.execution import (
    CAP_SPAWN_AGENT,
    ToolDispatcher,
    ToolLifecycleHooks,
    ToolTask,
)
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.db.protocols import SessionStateRepository

LifecycleCall = Callable[[LifecycleContext], Awaitable[None]]
# (state, worker_name, task, spawnable, budget) -> worker result envelope.
WorkerSpawner = Callable[..., Awaitable[dict[str, Any]]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _SpawnAgentHook:
    """Turn a completed ``spawn_agent`` call into its worker result envelope.

    The worker runs before the event hook publishes ``tool.completed``, so the
    ``worker.*`` events stay inside the tool call's span and the tool event
    carries the envelope, exactly as when the kernel dispatched inline.
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
            envelope = await self._spawn_worker(
                state=self._state,
                worker_name=str(result.output.get("worker") or "").strip(),
                task=str(result.output.get("task") or "").strip(),
                spawnable=self._spawnable,
                budget=self._budget,
            )
            result = ToolExecutionResult(
                call_id=result.call_id,
                tool_name=result.tool_name,
                status="failed" if envelope.get("status") == "failed" else "completed",
                output=envelope,
                error_message=envelope.get("error"),
                capabilities=result.capabilities,
            )
        return await self._next.after(task, result)


class ToolActions:
    """Executes tool-call batches for one agent and records every result."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        domain: TurnDomain,
        session_store: SessionStateRepository,
        subagent_builder: SubAgentBuilder,
        emit: LifecycleCall,
        spawn_worker: WorkerSpawner,
    ) -> None:
        self._tool_registry = tool_registry
        self._domain = domain
        self._session_store = session_store
        self._subagent_builder = subagent_builder
        self._emit = emit
        self._spawn_worker = spawn_worker

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
        """Run one batch of tool calls in model order and record their results."""
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
        results: list[ToolExecutionResult] = []
        batch_failed = False
        for call in tool_calls:
            task = await dispatcher.submit(
                call,
                session_id=session_id,
                runtime_context=session_state.working_memory,
                allowed_tools=profile.allowed_tools,
                agent_name=profile.name,
                skill_execution=session_state.skill_execution,
            )
            result = await dispatcher.settle(task)
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
            results.append(result)
            batch_failed = batch_failed or result.status != "completed"
        return results

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
        """Record a tool result: evidence turn, kernel memory, then domain projection.

        ``clear_error`` is False when an earlier call in the same batch failed,
        so a later success does not erase that failure's ``last_error``.
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
