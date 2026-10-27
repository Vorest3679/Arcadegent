"""Generic agent execution kernel: main/worker loops, events and session state.

The kernel knows turns, tools, model calls and workers, but not the application
request/response DTOs. ``TurnInput`` carries the opaque application request
through context building and domain hooks; ``TurnResult`` carries the final
state and the domain-projected response back to the application facade.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.context.context_builder import ContextBuilder
from app.agent.context.turn_recorder import append_turn
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.runtime.actions import ToolActions
from app.agent.runtime.agent_loop import STATUS_COMPLETED, AgentLoop, LoopOutcome, LoopSpec
from app.agent.runtime.domain_port import BaseTurnDomain, TurnDomain
from app.agent.runtime.events import (
    bind_run_events,
    current_run_events,
    emit_agent_changed,
    publish as publish_event,
    publish_assistant_text,
    reset_run_events,
)
from app.agent.runtime.lifecycle import (
    AgentEnvironment,
    LifecycleContext,
    LifecycleDispatcher,
    LifecyclePhase,
    LifecycleScope,
    RunScope,
    StateView,
    TurnScope,
)
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.worker_runner import WorkerRunner
from app.agent.skills.execution import SkillExecution
from app.agent.skills.registry import SkillRegistry
from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    SessionOwnershipError,
    ensure_working_memory_shape,
    reset_turn_scratch,
)
from app.infra.db.protocols import SessionStateRepository
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.registry import ToolRegistry
from app.infra.observability.logger import get_logger, log_exception_frames, log_ref
from app.session.events import RunEvent
from app.session.injector import RunPublisher

logger = get_logger(__name__)

# A turn's total model calls across the main loop and all workers, as a
# multiple of the per-loop step cap.
_TOTAL_BUDGET_FACTOR = 4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short(text: str | None, *, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def _new_output_id() -> str:
    return f"out_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class TurnInput:
    """One kernel turn: generic fields plus the opaque application request."""

    session_id: str | None
    client_id: str | None
    message: str
    request: Any
    request_payload: dict[str, Any]


@dataclass(frozen=True)
class TurnResult:
    """Kernel outcome handed back to the application facade."""

    session_id: str
    text: str
    status: str
    reply_source: str
    model_error: dict[str, Any] | None
    output_id: str | None
    state: AgentSessionState
    response: Any


class AgentKernel:
    """Main-agent/worker execution kernel with an injected turn domain."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        subagent_builder: SubAgentBuilder,
        tool_registry: ToolRegistry,
        provider_adapter: ProviderAdapter,
        session_store: SessionStateRepository,
        max_steps: int,
        skill_registry: SkillRegistry | None = None,
        domain: TurnDomain | None = None,
    ) -> None:
        self._context_builder = context_builder
        self._subagent_builder = subagent_builder
        self._tool_registry = tool_registry
        self._provider_adapter = provider_adapter
        self._session_store = session_store
        self._max_steps = max(2, max_steps)
        self._skill_registry = skill_registry
        self._domain: TurnDomain = domain or BaseTurnDomain()
        self._lifecycle = LifecycleDispatcher(self._domain)

    def _new_loop(self, turn: TurnInput) -> AgentLoop:
        # Built per run from the current attributes, so replaced collaborators
        # (tests, evaluation wiring) take effect.
        worker_runner = WorkerRunner(
            request=turn.request,
            request_payload=turn.request_payload,
            domain=self._domain,
            session_store=self._session_store,
            subagent_builder=self._subagent_builder,
            skill_registry=self._skill_registry,
            new_context=self._lifecycle_context,
            emit=self._emit,
            safe_emit=self._safe_emit,
            run_loop=lambda spec: self._new_loop(turn).run(spec),
        )

        async def spawn_worker(
            *,
            state: AgentSessionState,
            worker_name: str,
            task: str,
            spawnable: tuple[str, ...],
            budget: RunBudget | None,
        ) -> dict[str, Any]:
            return await worker_runner.spawn(
                state=state,
                worker_name=worker_name,
                task=task,
                spawnable=spawnable,
                budget=(budget or self._new_budget()).child(),
            )

        actions = ToolActions(
            tool_registry=self._tool_registry,
            domain=self._domain,
            session_store=self._session_store,
            subagent_builder=self._subagent_builder,
            emit=self._emit,
            spawn_worker=spawn_worker,
        )
        return AgentLoop(
            context_builder=self._context_builder,
            tool_registry=self._tool_registry,
            provider_adapter=self._provider_adapter,
            domain=self._domain,
            actions=actions,
            new_context=self._lifecycle_context,
            emit=self._emit,
            finish_step=self._finish_step,
        )

    def _new_budget(self) -> RunBudget:
        return RunBudget(self._max_steps, total_steps=self._max_steps * _TOTAL_BUDGET_FACTOR)

    def _environment(self) -> AgentEnvironment:
        return AgentEnvironment(
            provider=self._provider_adapter,
            tools=self._tool_registry,
            skill_registry=self._skill_registry,
        )

    def _lifecycle_context(
        self,
        scope: LifecycleScope,
        phase: LifecyclePhase,
        *,
        session_id: str,
        **kwargs: Any,
    ) -> LifecycleContext:
        return LifecycleContext(
            scope=scope,
            phase=phase,
            env=self._environment(),
            session_id=session_id,
            events=current_run_events(),
            logger=logger,
            **kwargs,
        )

    async def _emit(self, ctx: LifecycleContext) -> None:
        await self._lifecycle.emit(ctx)

    async def _safe_emit(self, ctx: LifecycleContext) -> None:
        await self._lifecycle.emit_safe(ctx)

    async def _finish_step(self, ctx: LifecycleContext) -> None:
        ctx.phase = LifecyclePhase.UNMOUNTED
        await self._safe_emit(ctx)

    async def run_turn(self, turn: TurnInput, *, events: RunPublisher) -> TurnResult:
        """Execute one turn, binding the run publisher for nested agent calls."""
        token = bind_run_events(events)
        try:
            return await self._run_turn(turn)
        finally:
            reset_run_events(token)

    async def _run_turn(self, turn: TurnInput) -> TurnResult:
        session_id = turn.session_id or f"s_{uuid4().hex[:12]}"
        state = self._session_store.get_or_create_session(session_id)
        self._bind_client_scope(state, turn.client_id)
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        self._session_store.save_session(state)
        run_ctx = self._lifecycle_context(
            LifecycleScope.RUN,
            LifecyclePhase.CREATE,
            session_id=session_id,
            run=RunScope(session_id=session_id, state=StateView(state)),
        )
        try:
            await self._emit(run_ctx)
            run_ctx.phase = LifecyclePhase.MOUNT
            await self._emit(run_ctx)
            result = await self._run_turn_session(turn=turn, session_id=session_id, state=state)
            run_ctx.reason = "completed"
            return result
        except asyncio.CancelledError as exc:
            # The session layer's terminal hook records the cancelled state
            # after the task has stopped; keep the saved context untouched.
            run_ctx.error = exc
            run_ctx.reason = "cancelled"
            run_ctx.phase = LifecyclePhase.ERROR
            await self._safe_emit(run_ctx)
            raise
        except Exception as exc:
            run_ctx.error = exc
            run_ctx.reason = "failed"
            run_ctx.phase = LifecyclePhase.ERROR
            await self._safe_emit(run_ctx)
            error_message = _short(f"{type(exc).__name__}: {exc}", limit=280) if str(exc) else type(exc).__name__
            state.status = "failed"
            state.last_error = error_message
            state.working_memory["last_error"] = {"message": error_message}
            state.updated_at = _utc_now_iso()
            self._session_store.save_session(state)
            publish_event(
                RunEvent.SESSION_FAILED,
                {
                    "error": error_message,
                    "active_subagent": state.active_subagent,
                },
            )
            logger.error(
                "chat.failed session_ref=%s exception_type=%s app_frames=%s",
                log_ref(session_id),
                type(exc).__name__,
                log_exception_frames(exc),
            )
            raise
        finally:
            run_ctx.phase = LifecyclePhase.UNMOUNTED
            await self._safe_emit(run_ctx)
            state.skill_execution = SkillExecution()

    async def _run_turn_session(
        self,
        *,
        turn: TurnInput,
        session_id: str,
        state: AgentSessionState,
    ) -> TurnResult:
        state.turn_index += 1
        state.skill_execution = SkillExecution()
        state.active_subagent = "main_agent"
        state.working_memory = reset_turn_scratch(state.working_memory)
        turn_scope = TurnScope(
            session_id=session_id,
            turn_index=state.turn_index,
            state=StateView(state),
            request=turn.request,
        )
        ctx = self._lifecycle_context(
            LifecycleScope.TURN,
            LifecyclePhase.CREATE,
            session_id=session_id,
            turn=turn_scope,
            turn_index=state.turn_index,
        )
        try:
            await self._emit(ctx)
            ctx.phase = LifecyclePhase.MOUNT
            await self._emit(ctx)
            return await self._run_turn_session_body(
                turn=turn,
                session_id=session_id,
                state=state,
                ctx=ctx,
            )
        except asyncio.CancelledError as exc:
            ctx.error = exc
            ctx.reason = "cancelled"
            await self._emit_turn_error(ctx)
            raise
        except Exception as exc:
            ctx.error = exc
            ctx.reason = "failed"
            await self._emit_turn_error(ctx)
            raise
        finally:
            ctx.phase = LifecyclePhase.UNMOUNTED
            await self._safe_emit(ctx)

    async def _emit_turn_error(self, ctx: LifecycleContext) -> None:
        ctx.phase = LifecyclePhase.ERROR
        await self._safe_emit(ctx)
        if not ctx.before_unmount_done:
            ctx.before_unmount_done = True
            ctx.phase = LifecyclePhase.BEFORE_UNMOUNT
            await self._safe_emit(ctx)

    async def _run_turn_session_body(
        self,
        *,
        turn: TurnInput,
        session_id: str,
        state: AgentSessionState,
        ctx: LifecycleContext,
    ) -> TurnResult:
        request_payload = turn.request_payload
        logger.info(
            "chat.start session_ref=%s turn_index=%s domain=%s message_chars=%s",
            log_ref(session_id),
            state.turn_index,
            self._domain.turn_summary(state),
            len(turn.message),
        )

        # Persist the user turn and session.started before the first cancellable
        # discovery operation, so cancellation during refresh cannot lose input.
        append_turn(
            state,
            AgentTurn(
                role="user",
                content=turn.message,
                agent="main_agent",
                scope="conversation",
                payload=request_payload,
            ),
            session_store=self._session_store,
        )
        publish_event(
            RunEvent.SESSION_STARTED,
            {
                **self._domain.session_event_fields(state),
                "model": "react-runtime",
                "active_subagent": state.active_subagent,
            },
        )
        emit_agent_changed(
            to_agent=state.active_subagent,
            reason="session.started",
        )

        if self._skill_registry is not None:
            await asyncio.to_thread(self._skill_registry.refresh)
        outcome = await self._run_main_agent(
            turn=turn,
            session_id=session_id,
            state=state,
            ctx=ctx,
            budget=self._new_budget(),
        )
        final_text = outcome.text
        output_id = outcome.output_id
        model_error = None if outcome.status == STATUS_COMPLETED else outcome.error

        reply_source = "model"
        if not final_text:
            logger.warning(
                "chat.fallback session_ref=%s reason=empty_model_output has_last_error=%s",
                log_ref(session_id),
                bool(state.working_memory.get("last_error")),
            )
            final_text = self._domain.fallback_reply(state, turn.request)
            reply_source = "fallback"
            output_id = None

        if output_id is None:
            # The reply was not published as model output (summary tool, fallback):
            # show it as a new output so earlier outputs stay sealed.
            output_id = _new_output_id()
            publish_assistant_text(final_text, output_id=output_id, stream_mode="synthetic")
        append_turn(
            state,
            AgentTurn(
                role="assistant",
                content=final_text,
                agent="main_agent",
                scope="conversation",
                payload={
                    "final": True,
                    "reply_source": reply_source,
                    "map_artifacts": self._domain.turn_display_snapshot(state),
                },
            ),
            session_store=self._session_store,
        )
        response = await self._domain.build_response(
            session_id=session_id,
            state=state,
            final_text=final_text,
        )
        if ctx.turn is not None:
            ctx.turn.final_text = final_text
        ctx.response = response
        ctx.before_unmount_done = True
        ctx.phase = LifecyclePhase.BEFORE_UNMOUNT
        await self._emit(ctx)
        state.active_subagent = "main_agent"
        state.working_memory["reply"] = final_text
        state.updated_at = _utc_now_iso()
        if model_error is not None:
            state.status = "failed"
            state.last_error = _short(
                str(model_error.get("message") or "model call failed"),
                limit=280,
            )
        else:
            state.status = "completed"
            state.last_error = None
        self._session_store.save_session(state)
        publish_event(
            RunEvent.SESSION_FAILED if model_error else RunEvent.ASSISTANT_COMPLETED,
            {
                "reply": final_text,
                "active_subagent": state.active_subagent,
                "reply_source": reply_source,
                "model_error": model_error,
                "error": state.last_error,
            },
            output_id=output_id,
        )
        logger.info(
            "chat.done session_ref=%s domain=%s reply_chars=%s",
            log_ref(session_id),
            self._domain.turn_summary(state),
            len(final_text),
        )
        return TurnResult(
            session_id=session_id,
            text=final_text,
            status=state.status,
            reply_source=reply_source,
            model_error=model_error,
            output_id=output_id,
            state=state,
            response=response,
        )

    async def _run_main_agent(
        self,
        *,
        turn: TurnInput,
        session_id: str,
        state: AgentSessionState,
        ctx: LifecycleContext,
        budget: RunBudget,
    ) -> LoopOutcome:
        node = self._subagent_builder.node("main_agent")
        spec = LoopSpec(
            session_id=session_id,
            state=state,
            node=node,
            request=turn.request,
            request_payload=turn.request_payload,
            budget=budget,
            scope={"turn": ctx.turn},
        )

        return await self._new_loop(turn).run(spec)

    def _bind_client_scope(self, state: AgentSessionState, client_id: str | None) -> None:
        """Attach a browser-owned client id to new sessions and reject cross-client writes."""
        if client_id is None:
            return
        if state.client_id is not None and state.client_id != client_id:
            raise SessionOwnershipError(state.session_id)
        if state.client_id is None:
            state.client_id = client_id
