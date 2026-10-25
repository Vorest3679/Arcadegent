"""Tool actions: run a model's tool calls and record their results.

Shared by the main agent and workers. For each call it prepares arguments,
executes through the registry, hands ``spawn_agent`` results to a worker
spawner, publishes the tool events, writes the evidence turn and kernel
bookkeeping, then lets the domain project the result through the step's
``update`` lifecycle hook.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.agent.context.turn_recorder import record_tool_result_turn
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.events import publish as publish_event
from app.agent.runtime.lifecycle import LifecycleContext, LifecyclePhase
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.session_state import AgentSessionState
from app.agent.skills.execution import bind_skill_execution
from app.agent.subagents.subagent_builder import SubAgentBuilder, SubAgentProfile
from app.agent.tools.base import CAP_SPAWN_AGENT
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.db.protocols import SessionStateRepository
from app.infra.observability.logger import get_logger, log_ref
from app.session.events import RunEvent

logger = get_logger(__name__)

LifecycleCall = Callable[[LifecycleContext], Awaitable[None]]
# (state, worker_name, task, spawnable, budget) -> worker result envelope.
WorkerSpawner = Callable[..., Awaitable[dict[str, Any]]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        """Run one batch of tool calls in order and return their final results."""
        results: list[ToolExecutionResult] = []
        batch_failed = False
        for call in tool_calls:
            # The trace starts when the model proposes the tool, so argument
            # preparation failures are also captured as a complete tool span.
            publish_event(
                RunEvent.TOOL_STARTED,
                {
                    "tool": call.name,
                    "call_id": call.call_id,
                    "active_subagent": profile.name,
                    "worker_run_id": worker_run_id,
                },
            )
            preparation_error: str | None = None
            try:
                if call.parse_error:
                    raise ValueError(f"invalid tool arguments: {call.parse_error}")
                prepared_args, hydrated_fields = await self._tool_registry.prepare_arguments(
                    tool_name=call.name,
                    raw_arguments=call.arguments,
                    runtime_context=session_state.working_memory,
                )
            except Exception as exc:
                prepared_args, hydrated_fields = dict(call.arguments), []
                preparation_error = f"{type(exc).__name__}: {exc}"
            argument_evidence = {
                "raw_arguments": deepcopy(call.raw_arguments),
                "parsed_arguments": deepcopy(call.arguments),
                "parse_error": call.parse_error,
                "prepared_arguments": deepcopy(prepared_args),
                "hydrated_fields": list(hydrated_fields),
                "preparation_error": preparation_error,
            }
            if preparation_error is not None:
                logger.warning(
                    "tool.prepare_failed session_ref=%s tool_ref=%s call_ref=%s agent_ref=%s",
                    log_ref(session_id),
                    log_ref(call.name),
                    log_ref(call.call_id),
                    log_ref(profile.name),
                )
                result = ToolExecutionResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status="failed",
                    output={
                        "error": {
                            "type": "argument_preparation_error",
                            "message": preparation_error,
                        }
                    },
                    error_message=preparation_error,
                )
                await self.record_tool_result(
                    session_id=session_id,
                    state=session_state,
                    result=result,
                    agent_name=profile.name,
                    worker_run_id=worker_run_id,
                    tool_arguments=prepared_args,
                    argument_evidence=argument_evidence,
                    persist=persist,
                    step_ctx=step_ctx,
                    clear_error=False,
                )
                results.append(result)
                batch_failed = True
                continue
            logger.info(
                "tool.call session_ref=%s tool_ref=%s call_ref=%s agent_ref=%s arg_count=%s",
                log_ref(session_id),
                log_ref(call.name),
                log_ref(call.call_id),
                log_ref(profile.name),
                len(prepared_args),
            )
            if hydrated_fields:
                logger.debug(
                    "tool.call.hydrated session_ref=%s tool_ref=%s call_ref=%s field_count=%s",
                    log_ref(session_id),
                    log_ref(call.name),
                    log_ref(call.call_id),
                    len(hydrated_fields),
                )
            with bind_skill_execution(profile.name, session_state.skill_execution):
                result = await self._tool_registry.execute(
                    call_id=call.call_id,
                    tool_name=call.name,
                    raw_arguments=prepared_args,
                    allowed_tools=profile.allowed_tools,
                )
            if result.status == "completed" and CAP_SPAWN_AGENT in result.capabilities:
                envelope = await self._spawn_worker(
                    state=session_state,
                    worker_name=str(result.output.get("worker") or "").strip(),
                    task=str(result.output.get("task") or "").strip(),
                    spawnable=self._subagent_builder.node(profile.name).spawnable,
                    budget=budget,
                )
                result = ToolExecutionResult(
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    status="failed" if envelope.get("status") == "failed" else "completed",
                    output=envelope,
                    error_message=envelope.get("error"),
                    capabilities=result.capabilities,
                )
            await self.record_tool_result(
                session_id=session_id,
                state=session_state,
                result=result,
                agent_name=profile.name,
                worker_run_id=worker_run_id,
                tool_arguments=prepared_args,
                argument_evidence=argument_evidence,
                persist=persist,
                step_ctx=step_ctx,
                clear_error=not batch_failed,
            )
            results.append(result)
            batch_failed = batch_failed or result.status != "completed"
        return results

    async def record_tool_result(
        self,
        *,
        session_id: str,
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
        """Record a tool result: events, evidence turn, kernel memory, then domain projection.

        ``clear_error`` is False when an earlier call in the same batch failed,
        so a later success does not erase that failure's ``last_error``.
        """
        if result.status == "completed":
            signal = self._domain.tool_result_signal(state=state, result=result)
            for event_name, event_data in signal.events:
                publish_event(event_name, event_data)
            payload: dict[str, Any] = {
                "tool": result.tool_name,
                "call_id": result.call_id,
                "active_subagent": agent_name,
                "worker_run_id": worker_run_id,
            }
            payload.update(signal.completed_fields)
            publish_event(RunEvent.TOOL_COMPLETED, payload)
            logger.info(
                "tool.completed session_ref=%s tool_ref=%s agent_ref=%s",
                log_ref(session_id),
                log_ref(result.tool_name),
                log_ref(agent_name),
            )
        else:
            error_message = result.error_message or "tool execution failed"
            publish_event(
                RunEvent.TOOL_FAILED,
                {
                    "tool": result.tool_name,
                    "call_id": result.call_id,
                    "error": error_message,
                    "active_subagent": agent_name,
                    "worker_run_id": worker_run_id,
                },
            )
            logger.warning(
                "tool.failed session_ref=%s tool_ref=%s agent_ref=%s",
                log_ref(session_id),
                log_ref(result.tool_name),
                log_ref(agent_name),
            )

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
