"""One ReAct loop shared by the main agent and workers.

The loop builds context, calls the model, runs the proposed tools and decides
how it ends. The main agent and a worker differ only in ``LoopSpec``: which
state they mutate, whether model text is published to the run, and whether an
unresolved tool error fails the loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.agent.context.context_builder import ContextBuilder
from app.agent.context.turn_recorder import record_model_call
from app.agent.llm.provider_adapter import ModelResponse, ProviderAdapter, collect_stream
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.actions import ToolActions
from app.agent.runtime.events import publish_assistant_text
from app.agent.runtime.lifecycle import (
    LifecycleContext,
    LifecyclePhase,
    LifecycleScope,
    StateView,
    StepPolicy,
    StepScope,
)
from app.agent.runtime.outcome import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STEP_LIMIT,
    LoopOutcome,
    RunBudget,
)
from app.agent.runtime.session_state import AgentSessionState
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.base import CAP_CONCLUDES_TURN
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.observability.logger import get_logger, log_ref

logger = get_logger(__name__)

ContextFactory = Callable[..., LifecycleContext]
LifecycleCall = Callable[[LifecycleContext], Awaitable[None]]


def _new_output_id() -> str:
    return f"out_{uuid4().hex[:12]}"


@dataclass
class LoopSpec:
    """Everything that distinguishes one loop run from another."""

    session_id: str
    state: AgentSessionState
    profile: SubAgentProfile
    request: Any
    request_payload: dict[str, Any]
    budget: RunBudget
    # Lifecycle scope fields for step contexts (``turn=`` or ``worker=``/``worker_run_id=``).
    scope: dict[str, Any] = field(default_factory=dict)
    worker_run_id: str | None = None
    # Main agent: stream/publish model text and mark the final reply evidence.
    publish_text: bool = True
    # Persist tool evidence into the session store as it happens (main agent only;
    # a worker's evidence is committed to the parent when it finishes).
    persist: bool = True
    # Worker: a tool error still unresolved at the end fails the loop.
    fail_on_tool_error: bool = False

    @property
    def kind(self) -> str:
        return "worker" if self.worker_run_id else "chat"

    @property
    def label(self) -> str:
        return "worker" if self.worker_run_id else "agent"


class AgentLoop:
    """Runs the step loop for a ``LoopSpec`` and returns a ``LoopOutcome``."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        tool_registry: ToolRegistry,
        provider_adapter: ProviderAdapter,
        domain: TurnDomain,
        actions: ToolActions,
        new_context: ContextFactory,
        emit: LifecycleCall,
        finish_step: LifecycleCall,
    ) -> None:
        self._context_builder = context_builder
        self._tool_registry = tool_registry
        self._provider_adapter = provider_adapter
        self._domain = domain
        self._actions = actions
        self._new_context = new_context
        self._emit = emit
        self._finish_step = finish_step

    async def run(self, spec: LoopSpec) -> LoopOutcome:
        state = spec.state
        profile = spec.profile
        outcome = LoopOutcome(status=STATUS_STEP_LIMIT)
        final_text: str | None = None
        output_id: str | None = None
        model_error: dict[str, Any] | None = None
        pending_tool_error: str | None = None

        while not spec.budget.exhausted:
            step = spec.budget.next()
            step_ctx = self._new_context(
                LifecycleScope.STEP,
                LifecyclePhase.CREATE,
                session_id=spec.session_id,
                turn_index=state.turn_index,
                step=StepScope(index=step, policy=StepPolicy(), state=StateView(state)),
                **spec.scope,
            )
            try:
                await self._emit(step_ctx)
                state.active_subagent = profile.name
                context = self._context_builder.build(
                    session_state=state,
                    request=spec.request,
                    subagent=profile,
                )
                self._log_context(spec, step, len(context.messages))
                step_output_id = _new_output_id()
                hints: dict[str, Any] = {
                    **self._domain.runtime_hints(state),
                    **(step_ctx.step.policy.runtime_hints if step_ctx.step is not None else {}),
                    "active_subagent": profile.name,
                }
                if spec.worker_run_id:
                    hints["worker_run_id"] = spec.worker_run_id
                hints["request"] = spec.request_payload
                hints["memory"] = state.working_memory
                response, published = await self._call_model(
                    spec,
                    output_id=step_output_id,
                    instructions=context.instructions,
                    messages=context.messages,
                    tools=await self._tool_registry.tool_definitions(allowed_tools=profile.allowed_tools),
                    runtime_hints=hints,
                )
                text = (response.text or "").strip()
                # A text response without tool calls becomes the final reply, which
                # the main agent persists as its own assistant turn. Mark the
                # evidence so the context projection does not replay it twice.
                final_reply = (
                    spec.publish_text and response.error is None and not response.tool_calls and bool(text)
                )
                record_model_call(
                    state=state,
                    response=response,
                    agent_name=profile.name,
                    step=step,
                    worker_run_id=spec.worker_run_id,
                    final_reply=final_reply,
                )
                if response.response_id:
                    state.previous_response_id = response.response_id
                self._log_step(spec, step, response)

                if response.error is not None:
                    model_error = dict(response.error)
                    state.working_memory["last_error"] = {
                        "message": model_error.get("message"),
                        "type": model_error.get("type"),
                        "source": "model",
                    }
                    logger.warning(
                        "%s.model_error session_ref=%s step=%s error_ref=%s",
                        spec.kind,
                        log_ref(spec.session_id),
                        step,
                        log_ref(str(model_error.get("type") or "unknown")),
                    )
                    break

                if response.tool_calls:
                    results = await self._actions.execute_tool_calls(
                        session_id=spec.session_id,
                        session_state=state,
                        tool_calls=response.tool_calls,
                        profile=profile,
                        worker_run_id=spec.worker_run_id,
                        persist=spec.persist,
                        step_ctx=step_ctx,
                        budget=spec.budget,
                    )
                    outcome.tool_errors.extend(
                        result.error_message or "tool execution failed"
                        for result in results
                        if result.status != "completed"
                    )
                    if spec.fail_on_tool_error:
                        # Sync this batch before a concluding tool can end the loop:
                        # a same-batch failure must still fail, while a clean batch
                        # recovers an error left by an earlier batch.
                        pending_tool_error = self._batch_tool_error(results)
                    reply = self._concluding_reply(results)
                    if reply is not None:
                        final_text = reply
                        break
                    continue

                if text:
                    final_text = text
                    output_id = step_output_id if published else None
                    break
                # Blank text without tool calls is not an answer: try another step.
            finally:
                await self._finish_step(step_ctx)

        outcome.text = final_text
        outcome.output_id = output_id
        if model_error is not None:
            outcome.status = STATUS_FAILED
            outcome.error = model_error
        elif final_text is None:
            outcome.status = STATUS_STEP_LIMIT
            outcome.error = {
                "type": "step_limit",
                "message": f"{spec.label} exhausted its step budget",
            }
            outcome.pending_tool_error = pending_tool_error
        elif pending_tool_error is not None:
            outcome.status = STATUS_FAILED
            outcome.error = {"type": "tool_error", "message": pending_tool_error}
        else:
            outcome.status = STATUS_COMPLETED
        return outcome

    @staticmethod
    def _batch_tool_error(results: list[ToolExecutionResult]) -> str | None:
        """Last failed result in a batch; a later success never erases an earlier failure."""
        error: str | None = None
        for result in results:
            if result.status != "completed":
                error = result.error_message or "tool execution failed"
        return error

    @staticmethod
    def _concluding_reply(results: list[ToolExecutionResult]) -> str | None:
        """Reply of the last completed ``concludes_turn`` tool in the batch, if non-blank."""
        reply: str | None = None
        for result in results:
            if result.status != "completed" or CAP_CONCLUDES_TURN not in result.capabilities:
                continue
            candidate = result.output.get("reply")
            if isinstance(candidate, str) and candidate.strip():
                reply = candidate.strip()
        return reply

    async def _call_model(self, spec: LoopSpec, *, output_id: str, **request: Any) -> tuple[ModelResponse, bool]:
        """Run one model call; the main agent publishes its text as one output.

        Returns the response and whether its text was published under ``output_id``.
        Cancelling the run closes the provider stream through ``collect_stream``.
        """
        adapter = self._provider_adapter
        if not spec.publish_text:
            return await adapter.complete(**request), False
        if not adapter.streaming:
            response = await adapter.complete(**request)
            text = (response.text or "").strip()
            if response.error is not None or not text:
                return response, False
            publish_assistant_text(text, output_id=output_id, stream_mode="synthetic")
            return response, True
        published = False

        def publish(text: str) -> None:
            nonlocal published
            publish_assistant_text(text, output_id=output_id, stream_mode="provider")
            published = True

        final = await collect_stream(adapter.stream(**request), on_text=publish)
        return final, published

    def _log_context(self, spec: LoopSpec, step: int, message_count: int) -> None:
        if spec.worker_run_id:
            logger.debug(
                "worker.context session_ref=%s worker_ref=%s run_ref=%s step=%s allowed_tool_count=%s message_count=%s",
                log_ref(spec.session_id),
                log_ref(spec.profile.name),
                log_ref(spec.worker_run_id),
                step,
                len(spec.profile.allowed_tools),
                message_count,
            )
            return
        logger.debug(
            "chat.context session_ref=%s step=%s subagent_ref=%s allowed_tool_count=%s message_count=%s",
            log_ref(spec.session_id),
            step,
            log_ref(spec.state.active_subagent),
            len(spec.profile.allowed_tools),
            message_count,
        )

    def _log_step(self, spec: LoopSpec, step: int, response: ModelResponse) -> None:
        if spec.worker_run_id:
            logger.info(
                "worker.step session_ref=%s worker_ref=%s run_ref=%s step=%s tool_calls=%s has_text=%s",
                log_ref(spec.session_id),
                log_ref(spec.profile.name),
                log_ref(spec.worker_run_id),
                step,
                len(response.tool_calls),
                bool(response.text),
            )
            return
        logger.info(
            "chat.step session_ref=%s step=%s subagent_ref=%s tool_calls=%s has_text=%s",
            log_ref(spec.session_id),
            step,
            log_ref(spec.state.active_subagent),
            len(response.tool_calls),
            bool(response.text),
        )
