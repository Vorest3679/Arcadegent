"""Tool execution: argument preparation, evidence and one task per model call.

The dispatcher knows tool calls, the registry and the kernel capability names;
it does not import ``app.agent.runtime``. Runtime concerns (tool events, logs,
domain projection and worker dispatch) are injected through
``ToolLifecycleHooks``.

Every call runs inside its own ``asyncio.Task``. The runtime supervisor may
overlap calls whose descriptor declares ``execution_mode="parallel"`` (stage 7).
Finalization happens in two phases: ``after`` runs inside the call's task (it may
start a worker concurrently), ``commit`` runs in the loop coroutine in model
call order (it commits workers, publishes events and records evidence).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.agent.skills.execution import SkillExecution, bind_skill_execution
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.observability.logger import get_logger, log_ref

if TYPE_CHECKING:
    from app.agent.llm.provider_adapter import ModelToolCall

logger = get_logger(__name__)

# Kernel capabilities declared by tool descriptors. ``spawn_agent`` returns
# ``{"worker": str, "task": str}`` and the kernel runs that worker;
# ``concludes_turn`` returns ``{"reply": str}`` and a non-blank reply ends the
# turn. The last two are reserved and have no runtime behavior yet.
CAP_SPAWN_AGENT = "spawn_agent"
CAP_CONCLUDES_TURN = "concludes_turn"
CAP_BACKGROUND = "background"
CAP_PROGRAMMATIC = "programmatic"


class ToolLifecycleHooks(Protocol):
    """Runtime-provided hooks around one tool call.

    ``before`` runs before argument preparation, so ``tool.started`` is
    published even when preparation fails. ``after`` runs inside the call's
    task and may replace the result (it starts a worker without committing it).
    ``commit`` runs when the loop settles the call in model order and may
    replace the result again (worker commit, domain projection, events).
    ``on_update`` is reserved for partial tool output and is not called yet.
    """

    async def before(self, task: "ToolTask") -> None:
        """Observe a call before its arguments are prepared."""

    def on_update(self, task: "ToolTask", partial: dict[str, Any]) -> None:
        """Observe a partial result of a still-running call (reserved)."""

    async def after(self, task: "ToolTask", result: ToolExecutionResult) -> ToolExecutionResult:
        """Finalize the call inside its task; no loop-ordered writes here."""

    async def commit(self, task: "ToolTask", result: ToolExecutionResult) -> ToolExecutionResult:
        """Commit the call in the loop coroutine, then publish its events."""


@dataclass
class ToolTask:
    """One model tool call and its execution state."""

    call: "ModelToolCall"
    agent_name: str = ""
    prepared_args: dict[str, Any] = field(default_factory=dict)
    argument_evidence: dict[str, Any] = field(default_factory=dict)
    preparation_error: str | None = None
    # Filled from the result for an executed call. A call that failed argument
    # preparation never ran, so it carries no capabilities.
    capabilities: tuple[str, ...] = ()
    result: ToolExecutionResult | None = None
    task: "asyncio.Task[ToolExecutionResult] | None" = None
    # Runtime-owned opaque payload set by ``after`` (for ``spawn_agent`` this is
    # a pending worker dispatch) and consumed by ``commit``.
    deferred: Any = None
    # True when the call was finalized (``commit`` ran) and its result is final.
    finalized: bool = False
    # True when the result was synthesized because the call was cancelled.
    cancelled: bool = False
    # Set by the batch recorder once its evidence turn was handled.
    recorded: bool = False


class ToolDispatcher:
    """Prepare, execute and settle model tool calls through the registry."""

    def __init__(self, *, tool_registry: ToolRegistry, hooks: ToolLifecycleHooks) -> None:
        self._tool_registry = tool_registry
        self._hooks = hooks

    async def execute(
        self,
        call: "ModelToolCall",
        *,
        session_id: str,
        runtime_context: dict[str, Any],
        allowed_tools: list[str],
        agent_name: str = "",
        skill_execution: SkillExecution,
    ) -> ToolExecutionResult:
        """Run one call to completion and return its final result."""
        task = await self.submit(
            call,
            session_id=session_id,
            runtime_context=runtime_context,
            allowed_tools=allowed_tools,
            agent_name=agent_name,
            skill_execution=skill_execution,
        )
        return await self.settle(task)

    async def submit(
        self,
        call: "ModelToolCall",
        *,
        session_id: str,
        runtime_context: dict[str, Any],
        allowed_tools: list[str],
        agent_name: str = "",
        skill_execution: SkillExecution,
    ) -> ToolTask:
        """Start one call as a task; ``settle`` finalizes it in model order.

        ``before`` runs here and not inside the task so the ``tool.started``
        event is ordered with the model's call list even when several calls
        overlap later.
        """
        task = ToolTask(call=call, agent_name=agent_name)
        await self._hooks.before(task)
        task.task = asyncio.create_task(
            self._run(
                task,
                session_id=session_id,
                runtime_context=runtime_context,
                allowed_tools=allowed_tools,
                agent_name=agent_name,
                skill_execution=skill_execution,
            )
        )
        return task

    async def settle(self, task: ToolTask) -> ToolExecutionResult:
        """Await a submitted call, then run its commit phase exactly once.

        Calling ``settle`` again after a commit (or after ``cancel``) returns the
        finalized result without touching the task. A cancelled batch never
        leaves the task behind.
        """
        if task.finalized and task.result is not None:
            return task.result
        run = task.task
        if run is None:
            raise RuntimeError("tool task was not submitted")
        try:
            result = await run
        except asyncio.CancelledError:
            run.cancel()
            with suppress(asyncio.CancelledError):
                await run
            raise
        task.result = result
        return await self._finalize(task, result)

    async def cancel(self, task: ToolTask, *, reason: str = "cancelled") -> ToolExecutionResult:
        """Cancel a call and finalize it so its event span always closes.

        A call that already finished is committed normally; an unfinished or
        cancelled call gets a synthesized failed result with error type
        ``cancelled``. A task that finished with a real exception keeps that
        exception as a ``tool_execution_error`` instead of pretending it was
        cancelled. Only the child task's own cancellation/exception is
        suppressed; a cancellation of the caller is always re-raised.
        """
        if task.finalized and task.result is not None:
            return task.result
        run = task.task
        if run is not None and not run.done():
            run.cancel()
        if run is not None:
            current = asyncio.current_task()
            cancelling_before = current.cancelling() if current is not None else 0
            try:
                await run
            except asyncio.CancelledError:
                # A second outer cancellation must not be mistaken for the
                # child's own cancellation and swallowed.
                if current is not None and current.cancelling() > cancelling_before:
                    raise
            except Exception:
                # The real error is re-read from ``run.result()`` below and
                # reported as a tool failure, not as a cancellation.
                pass
        if task.finalized and task.result is not None:
            return task.result
        result: ToolExecutionResult | None = None
        if run is not None and run.done() and not run.cancelled():
            try:
                result = run.result()
            except Exception as exc:
                result = _execution_failure_result(task, exc)
        if result is None:
            task.cancelled = True
            result = _cancelled_result(task, reason=reason)
        task.result = result
        return await self._finalize(task, result)

    async def _finalize(self, task: ToolTask, result: ToolExecutionResult) -> ToolExecutionResult:
        if task.finalized and task.result is not None:
            return task.result
        final = await self._hooks.commit(task, result)
        task.result = final
        task.finalized = True
        return final

    async def _run(
        self,
        task: ToolTask,
        *,
        session_id: str,
        runtime_context: dict[str, Any],
        allowed_tools: list[str],
        agent_name: str,
        skill_execution: SkillExecution,
    ) -> ToolExecutionResult:
        await self._prepare(task, session_id=session_id, runtime_context=runtime_context)
        call = task.call
        if task.preparation_error is not None:
            result = ToolExecutionResult(
                call_id=call.call_id,
                tool_name=call.name,
                status="failed",
                output={
                    "error": {
                        "type": "argument_preparation_error",
                        "message": task.preparation_error,
                    }
                },
                error_message=task.preparation_error,
            )
        else:
            with bind_skill_execution(agent_name, skill_execution):
                result = await self._tool_registry.execute(
                    call_id=call.call_id,
                    tool_name=call.name,
                    raw_arguments=task.prepared_args,
                    allowed_tools=allowed_tools,
                )
            task.capabilities = result.capabilities
        return await self._hooks.after(task, result)

    async def _prepare(
        self,
        task: ToolTask,
        *,
        session_id: str,
        runtime_context: dict[str, Any],
    ) -> None:
        """Prepare arguments, validate and build the evidence attached to the tool turn."""
        call = task.call
        hydrated: list[str] = []
        try:
            if call.parse_error:
                raise ValueError(f"invalid tool arguments: {call.parse_error}")
            task.prepared_args, hydrated = await self._tool_registry.prepare_arguments(
                tool_name=call.name,
                raw_arguments=call.arguments,
                runtime_context=runtime_context,
            )
        except Exception as exc:
            task.prepared_args = dict(call.arguments)
            task.preparation_error = f"{type(exc).__name__}: {exc}"
        task.argument_evidence = {
            "raw_arguments": deepcopy(call.raw_arguments),
            "parsed_arguments": deepcopy(call.arguments),
            "parse_error": call.parse_error,
            "prepared_arguments": deepcopy(task.prepared_args),
            "hydrated_fields": list(hydrated),
            "preparation_error": task.preparation_error,
        }
        if task.preparation_error is not None:
            logger.warning(
                "tool.prepare_failed session_ref=%s tool_ref=%s call_ref=%s agent_ref=%s",
                log_ref(session_id),
                log_ref(call.name),
                log_ref(call.call_id),
                log_ref(task.agent_name),
            )
            return
        logger.info(
            "tool.call session_ref=%s tool_ref=%s call_ref=%s agent_ref=%s arg_count=%s",
            log_ref(session_id),
            log_ref(call.name),
            log_ref(call.call_id),
            log_ref(task.agent_name),
            len(task.prepared_args),
        )
        if hydrated:
            logger.debug(
                "tool.call.hydrated session_ref=%s tool_ref=%s call_ref=%s field_count=%s",
                log_ref(session_id),
                log_ref(call.name),
                log_ref(call.call_id),
                len(hydrated),
            )


def _cancelled_result(task: ToolTask, *, reason: str) -> ToolExecutionResult:
    """Synthetic result for a call whose execution task was cancelled."""
    message = f"tool call cancelled ({reason})"
    return ToolExecutionResult(
        call_id=task.call.call_id,
        tool_name=task.call.name,
        status="failed",
        output={"error": {"type": "cancelled", "message": message}},
        error_message=message,
    )


def _execution_failure_result(task: ToolTask, exc: Exception) -> ToolExecutionResult:
    """Failed result preserving an exception raised by the call's own task."""
    message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return ToolExecutionResult(
        call_id=task.call.call_id,
        tool_name=task.call.name,
        status="failed",
        output={"error": {"type": "tool_execution_error", "message": message}},
        error_message=message,
    )
