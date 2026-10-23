"""Application facade over the generic agent execution kernel.

``AgentKernel`` knows turns, tools, model calls and workers, but not
``ChatRequest`` / ``ChatResponse``. This facade maps the application request to
``TurnInput``, runs one kernel turn and returns the domain-projected response.
"""

from __future__ import annotations

from typing import cast

from app.agent.context.context_builder import ContextBuilder
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.skills.registry import SkillRegistry
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.registry import ToolRegistry
from app.infra.db.protocols import SessionStateRepository
from app.protocol.messages import ChatRequest, ChatResponse
from app.session.injector import RunPublisher


class ReactRuntime(AgentKernel):
    """Arcade chat facade: request/response mapping around the generic kernel."""

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
        super().__init__(
            context_builder=context_builder,
            subagent_builder=subagent_builder,
            tool_registry=tool_registry,
            provider_adapter=provider_adapter,
            session_store=session_store,
            max_steps=max_steps,
            skill_registry=skill_registry,
            domain=domain,
        )

    async def run_chat(self, request: ChatRequest, *, events: RunPublisher) -> ChatResponse:
        """Run one application chat turn and return its projected response."""
        turn = TurnInput(
            session_id=request.session_id,
            client_id=request.client_id,
            message=request.message,
            request=request,
            request_payload=request.model_dump(mode="json"),
        )
        result = await self.run_turn(turn, events=events)
        return cast(ChatResponse, result.response)
