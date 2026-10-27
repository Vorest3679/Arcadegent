"""Tool execution: argument preparation, evidence and one task per model call.

The dispatcher knows tool calls, the registry and the kernel capability names;
it does not import ``app.agent.runtime``. Runtime concerns (tool events, logs,
domain projection and worker dispatch) are injected through
``ToolLifecycleHooks``.

Every call runs inside its own ``asyncio.Task`` so a supervisor can overlap
calls whose descriptor declares ``execution_mode="parallel"`` (stage 7). This
module still settles calls in model order: ``execute`` awaits each task right
away.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
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
    published even when preparation fails. ``after`` may replace the result
    (worker dispatch) before events are published. ``on_update`` is reserved
    for partial tool output and is not called yet.
    """

    async def before(self, task: "ToolTask") -> None:
        """Observe a call before its arguments are prepared."""

    def on_update(self, task: "ToolTask", partial: dict[str, Any]) -> None:
        """Observe a partial result of a still-running call (reserved)."""

    async def after(self, task: "ToolTask", result: ToolExecutionResult) -> ToolExecutionResult:
        """Observe and optionally replace the settled result."""


@dataclass
class ToolTask:
    """One model tool call and its execution state."""

    call: "ModelToolCall"
    agent_name: str = ""
    prepared_args: dict[str, Any] = field(default_factory=dict)
    argument_evidence: dict[str, Any] = field(default_factory=dict)
    preparation_error: str | None = None
    capabilities: tuple[str, ...] = ()
    result: ToolExecutionResult | None = None
    task: "asyncio.Task[ToolExecutionResult] | None" = None


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
        skill_execution: SkillExecution | None = None,
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
        return await self.settle(task) # submit执行完生命钩子并返回async任务后，开始真正执行

    async def submit(
        self,
        call: "ModelToolCall",
        *,
        session_id: str,
        runtime_context: dict[str, Any],
        allowed_tools: list[str],
        agent_name: str = "",
        skill_execution: SkillExecution | None = None,
    ) -> ToolTask:
        """Start one call as a task; ``settle`` collects its result.

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
        """Await a submitted call; a cancelled batch never leaves the task behind."""
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
        return result

    async def _run(
        self,
        task: ToolTask,
        *,
        session_id: str,
        runtime_context: dict[str, Any],
        allowed_tools: list[str],
        agent_name: str,
        skill_execution: SkillExecution | None,
    ) -> ToolExecutionResult:
        await self._prepare(task, session_id=session_id, runtime_context=runtime_context)
        call = task.call
        # result will be defined in if/else, and share in _run().
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
            scope = (
                bind_skill_execution(agent_name, skill_execution)
                if skill_execution is not None
                else nullcontext()
            )
            with scope:
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
