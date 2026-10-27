"""Worker dispatch (plan 4, stage 4): run/commit split and cancellation cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.runtime.agent_loop import LoopOutcome, LoopSpec
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.runtime.lifecycle import AgentEnvironment, LifecycleContext
from app.agent.runtime.session_state import AgentSessionState, AgentTurn, ensure_working_memory_shape
from app.agent.runtime.worker_runner import WorkerRunner
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor
from app.agent.tools.execution import CAP_SPAWN_AGENT
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.protocol.messages import ChatRequest
from app.session.injector import CollectingPublisher
from backend.app.tests.integration.test_runtime_kernel_domain import _ContextBuilder, _Provider, _Store

_TOOLS = ("spawn", "echo", "block")


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


class _Tools:
    provider_name = "worker_runner_test"
    _capabilities = {"spawn": (CAP_SPAWN_AGENT,)}

    def __init__(self) -> None:
        self.block_started = asyncio.Event()
        self.block_cancelled = asyncio.Event()

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
        if tool_name == "block":
            self.block_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.block_cancelled.set()
                raise
        if tool_name == "spawn":
            return ProviderExecutionResult(status="completed", output=dict(raw_arguments))
        return ProviderExecutionResult(status="completed", output={"echo": raw_arguments})

    async def refresh(self) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


def _kernel(tmp_path: Path, provider: _Provider, store: _Store, tools: _Tools) -> AgentKernel:
    registry = ToolRegistry(
        providers=[tools],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
    )
    return AgentKernel(
        context_builder=_ContextBuilder(),  # type: ignore[arg-type]
        subagent_builder=_SubAgents(),  # type: ignore[arg-type]
        tool_registry=registry,
        provider_adapter=provider,  # type: ignore[arg-type]
        session_store=store,  # type: ignore[arg-type]
        max_steps=4,
    )


def _turn(message: str = "go", *, session_id: str = "s_cancel") -> TurnInput:
    request = ChatRequest(session_id=session_id, message=message)
    return TurnInput(
        session_id=session_id,
        client_id=None,
        message=message,
        request=request,
        request_payload=request.model_dump(mode="json"),
    )


def test_worker_cancelled_while_a_tool_runs_restores_the_parent(tmp_path: Path) -> None:
    async def scenario() -> _Store:
        tools = _Tools()
        store = _Store()
        provider = _Provider([
            ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
            ("helper_worker", _call("echo", value=1)),
            ("helper_worker", _call("block")),
        ])
        kernel = _kernel(tmp_path, provider, store, tools)
        run_task = asyncio.create_task(kernel.run_turn(_turn(), events=CollectingPublisher()))
        await asyncio.wait_for(tools.block_started.wait(), timeout=5)

        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        assert tools.block_cancelled.is_set()
        await asyncio.sleep(0)
        assert [
            item
            for item in asyncio.all_tasks()
            if item is not asyncio.current_task() and not item.done()
        ] == []
        return store

    store = asyncio.run(scenario())
    saved = store.states["s_cancel"]
    assert saved.active_subagent == "main_agent"
    # The session layer records the cancelled terminal state; the kernel does not.
    assert saved.status == "running"
    echo_turns = [turn for turn in saved.turns if turn.name == "echo" and turn.scope == "worker"]
    assert len(echo_turns) == 1
    assert not [turn for turn in saved.turns if turn.name == "block"]


async def _noop(ctx: LifecycleContext) -> None:
    return None


def _new_context(scope: Any, phase: Any, *, session_id: str, **kwargs: Any) -> LifecycleContext:
    return LifecycleContext(
        scope=scope,
        phase=phase,
        env=AgentEnvironment(provider=None, tools=None),
        session_id=session_id,
        **kwargs,
    )


def test_worker_runner_keeps_the_parent_clean_until_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _Store()
        parent = AgentSessionState(session_id="s_parent", turn_index=1)
        parent.working_memory = ensure_working_memory_shape({})
        store.save_session(parent)

        async def fake_loop(spec: LoopSpec) -> LoopOutcome:
            spec.state.turns.append(
                AgentTurn(
                    role="tool",
                    content="echo done",
                    agent="helper_worker",
                    name="echo",
                    call_id="c1",
                    worker_run_id=spec.worker_run_id,
                    scope="worker",
                    payload={"argument_evidence": {}},
                )
            )
            spec.state.working_memory["usage_totals"] = {"calls": 1, "input_tokens": 3}
            return LoopOutcome(status="completed", text="worker text")

        runner = WorkerRunner(
            request=ChatRequest(session_id="s_parent", message="go"),
            request_payload={"message": "go"},
            domain=BaseTurnDomain(),
            session_store=store,  # type: ignore[arg-type]
            subagent_builder=_SubAgents(),  # type: ignore[arg-type]
            skill_registry=None,
            new_context=_new_context,
            emit=_noop,
            safe_emit=_noop,
            run_loop=fake_loop,
        )
        run = runner.prepare(
            parent_state=parent,
            worker_name="helper_worker",
            task="do it",
            budget=RunBudget(3),
        )
        await runner.run(run)

        assert parent.working_memory.get("worker_runs", []) == []
        assert not [turn for turn in parent.turns if turn.scope == "worker"]
        assert parent.working_memory.get("usage_totals") is None

        envelope = runner.commit(run)

        assert envelope["status"] == "completed"
        assert len(parent.working_memory["worker_runs"]) == 1
        assert parent.working_memory["usage_totals"]["calls"] == 1
        assert [turn.name for turn in parent.turns if turn.scope == "worker"] == ["echo"]
        assert parent.active_subagent == "main_agent"

        # Committing the same run twice must not duplicate evidence or the envelope.
        assert runner.commit(run) is envelope
        assert len(parent.working_memory["worker_runs"]) == 1
        assert [turn.name for turn in parent.turns if turn.scope == "worker"] == ["echo"]

    asyncio.run(scenario())


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        text=None,
        status="completed",
        protocol="chat_completions",
        reported_model="stub",
        tool_calls=[ModelToolCall(call_id=f"call_{name}", name=name, arguments=arguments)],
    )
