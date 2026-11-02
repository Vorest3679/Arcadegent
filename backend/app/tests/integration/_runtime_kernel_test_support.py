"""Shared fakes for the runtime-kernel integration tests.

Scripted provider, stub context/subagent builders, in-memory session store and
a tiny tool provider. The kernel tests and the worker-runner tests share these
so a fixture change does not reach across test modules.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.runtime.session_state import AgentSessionState
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor
from app.agent.tools.execution import CAP_CONCLUDES_TURN, CAP_SPAWN_AGENT
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.protocol.messages import ChatRequest
from app.session.injector import CollectingPublisher

_TOOLS = ("echo", "finish", "spawn", "boom", "read_file")


@dataclass
class _Context:
    instructions: str
    messages: list[dict[str, Any]]


class _ContextBuilder:
    def build(self, *, session_state: AgentSessionState, request: Any, subagent: SubAgentProfile) -> _Context:
        return _Context(instructions=subagent.name, messages=[{"role": "user", "content": request.message}])


class _SubAgents:
    def get(self, name: str) -> SubAgentProfile:
        return SubAgentProfile(name=name, prompt_file="", allowed_tools=list(_TOOLS))  # type: ignore[arg-type]

    def node(self, name: str) -> AgentNode:
        is_main = name == "main_agent"
        return AgentNode(
            profile=self.get(name),
            scope="conversation" if is_main else "worker",
            publish_text=is_main,
            spawnable=("helper_worker",) if is_main else (),
        )


class _Store:
    def __init__(self) -> None:
        self.states: dict[str, AgentSessionState] = {}

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        return deepcopy(self.states.get(session_id) or AgentSessionState(session_id=session_id))

    def save_session(self, state: AgentSessionState) -> None:
        self.states[state.session_id] = deepcopy(state)


class _Tools:
    provider_name = "kernel_test"
    _capabilities = {"finish": (CAP_CONCLUDES_TURN,), "spawn": (CAP_SPAWN_AGENT,)}

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        return {
            name: ToolDescriptor(
                name=name,
                description=name,
                provider=self.provider_name,
                input_schema={"type": "object"},
                capabilities=self._capabilities.get(name, ()),
            )
            for name in _TOOLS
        }

    async def execute(self, *, tool_name: str, raw_arguments: dict[str, Any], validated_arguments: Any = None):
        if tool_name == "boom":
            return ProviderExecutionResult(status="failed", output={"error": {"message": "boom"}}, error_message="boom")
        if tool_name == "finish":
            return ProviderExecutionResult(status="completed", output={"reply": raw_arguments["reply"]})
        if tool_name == "spawn":
            return ProviderExecutionResult(status="completed", output=dict(raw_arguments))
        if tool_name == "read_file":
            return ProviderExecutionResult(status="completed", output={"path": raw_arguments["path"], "content": "x = 1\n"})
        return ProviderExecutionResult(status="completed", output={"echo": raw_arguments})

    async def refresh(self) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


class _Provider:
    streaming = False

    def __init__(self, script: list[tuple[str, ModelResponse]]) -> None:
        self.script = list(script)
        self.calls: list[str] = []
        self.hints: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> ModelResponse:
        agent = kwargs["runtime_hints"]["active_subagent"]
        self.calls.append(agent)
        self.hints.append(dict(kwargs["runtime_hints"]))
        expected, response = self.script.pop(0)
        assert agent == expected
        return response


def _text(text: str) -> ModelResponse:
    return ModelResponse(text=text, status="completed", protocol="chat_completions", reported_model="stub")


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        text=None,
        status="completed",
        protocol="chat_completions",
        reported_model="stub",
        tool_calls=[ModelToolCall(call_id=f"call_{name}", name=name, arguments=arguments)],
    )


def _runtime(tmp_path: Path, provider: _Provider, store: _Store, domain: BaseTurnDomain | None = None) -> AgentKernel:
    registry = ToolRegistry(
        providers=[_Tools()],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
    )
    return AgentKernel(
        context_builder=_ContextBuilder(),  # type: ignore[arg-type]
        subagent_builder=_SubAgents(),  # type: ignore[arg-type]
        tool_registry=registry,
        provider_adapter=provider,  # type: ignore[arg-type]
        session_store=store,  # type: ignore[arg-type]
        max_steps=4,
        domain=domain,
    )


def _run(kernel: AgentKernel, message: str, *, session_id: str = "s_kernel") -> tuple[Any, CollectingPublisher]:
    events = CollectingPublisher()
    request = ChatRequest(session_id=session_id, message=message)
    turn = TurnInput(
        session_id=session_id,
        client_id=None,
        message=message,
        request=request,
        request_payload=request.model_dump(mode="json"),
    )
    result = asyncio.run(kernel.run_turn(turn, events=events))
    return result, events
