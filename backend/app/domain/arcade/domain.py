"""Arcade implementation of the runtime ``TurnDomain`` port."""

from __future__ import annotations

from typing import Any

from app.agent.runtime.domain_port import BaseTurnDomain, ToolResultSignal
from app.agent.runtime.lifecycle import LifecycleContext
from app.agent.runtime.session_state import (
    AgentSessionState,
    set_working_memory_artifact,
)
from app.agent.tools.registry import ToolExecutionResult
from app.domain.arcade import observations, replies, worker_results
from app.domain.arcade.intent import IntentPolicy
from app.protocol.messages import ChatRequest, ChatResponse
from app.services.arcade_payload_mapper import ArcadePayloadMapper


class ArcadeTurnDomain(BaseTurnDomain):
    """Arcade-owned turn semantics injected into the generic ReactRuntime."""

    def __init__(
        self,
        *,
        payload_mapper: ArcadePayloadMapper | None = None,
        intent_policy: IntentPolicy | None = None,
    ) -> None:
        self.payload_mapper = payload_mapper
        self.intent_policy = intent_policy or IntentPolicy()

    # Lifecycle hooks: where the arcade domain changes its memory and intent.

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        if ctx.turn is None:
            return
        state = ctx.turn.state
        request: ChatRequest = ctx.turn.request
        self.intent_policy.classify(state, request)
        memory = observations.prepare_turn_memory(state.working_memory)

        memory["last_request"] = request.model_dump(mode="json")
        if request.location is not None:
            set_working_memory_artifact(
                memory,
                "client_location",
                request.location.model_dump(mode="json", exclude_none=True),
                turn_index=state.turn_index,
            )
        if request.shop_id is not None:
            memory["last_shop_id"] = request.shop_id
        memory["keyword"] = request.keyword or self.intent_policy.extract_keyword(request.message)

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        if ctx.state is None or ctx.tool_result is None:
            return
        observations.apply_tool_result(
            state=ctx.state,
            result=ctx.tool_result,
            intent_policy=self.intent_policy,
        )

    async def on_worker_mount(self, ctx: LifecycleContext) -> None:
        if ctx.worker is None:
            return
        worker, parent = ctx.worker.state, ctx.worker.parent_state
        worker.intent = parent.intent
        worker.working_memory.update(worker_results.worker_memory_snapshot(parent.working_memory))

    # Data-producing policies.

    def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
        return {"intent": state.intent}

    def session_event_fields(self, state: AgentSessionState) -> dict[str, Any]:
        return {"intent": state.intent}

    def tool_result_signal(self, *, state: AgentSessionState, result: ToolExecutionResult) -> ToolResultSignal:
        _ = state
        return observations.tool_result_signal(result)

    def turn_display_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        return replies.snapshot_turn_map_artifacts(state)

    def promote_worker_artifacts(
        self,
        *,
        parent_memory: dict[str, Any],
        worker_memory: dict[str, Any],
        turn_index: int,
    ) -> dict[str, Any]:
        return worker_results.promote_worker_artifacts(
            parent_memory=parent_memory,
            worker_memory=worker_memory,
            turn_index=turn_index,
        )

    def build_worker_envelope(
        self,
        *,
        worker_name: str,
        run_id: str,
        task: str,
        worker_memory: dict[str, Any],
        final_text: str | None,
        promoted_artifacts: dict[str, Any],
        failed_error: str | None,
    ) -> dict[str, Any]:
        return worker_results.build_worker_envelope(
            worker_name=worker_name,
            run_id=run_id,
            task=task,
            worker_memory=worker_memory,
            final_text=final_text,
            promoted_artifacts=promoted_artifacts,
            failed_error=failed_error,
        )

    def fallback_reply(self, state: AgentSessionState, request: ChatRequest) -> str:
        return replies.fallback_reply(state, request, intent_policy=self.intent_policy)

    async def build_response(
        self,
        *,
        session_id: str,
        state: AgentSessionState,
        final_text: str,
    ) -> ChatResponse:
        return await replies.build_response(
            payload_mapper=self.payload_mapper,
            session_id=session_id,
            state=state,
            final_text=final_text,
            intent_policy=self.intent_policy,
        )

    def turn_summary(self, state: AgentSessionState) -> str:
        return replies.turn_summary(state, intent_policy=self.intent_policy)

    # Test/transition helpers keep domain assertions close to the implementation.
    def prepare_turn_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        return observations.prepare_turn_memory(memory)

    def display_shops(self, memory: dict[str, Any]) -> list[dict[str, Any]]:
        return replies.display_shops(memory)

    def worker_memory_snapshot(self, parent_memory: dict[str, Any]) -> dict[str, Any]:
        return worker_results.worker_memory_snapshot(parent_memory)
