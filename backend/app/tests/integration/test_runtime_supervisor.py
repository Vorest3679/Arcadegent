"""Supervisor and concurrency (plan 4, stage 7).

Parallel calls overlap, exclusive calls are barriers, results commit in model
order, and cancellation closes every submitted call without leaving tasks.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.runtime.session_state import AgentSessionState
from app.agent.runtime.supervisor import LoopSupervisor, WaitPolicy
from app.agent.skills.execution import SkillExecution
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor
from app.agent.tools.execution import CAP_SPAWN_AGENT, ToolDispatcher, ToolTask
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.protocol.messages import ChatRequest
from app.session.events import RunEvent
from app.session.injector import CollectingPublisher
from backend.app.tests.integration._runtime_kernel_test_support import (
    _ContextBuilder,
    _Store,
    _text,
)

_TOOL_NAMES = ("echo", "slow", "fast", "block", "exclusive", "spawn")
_MODES = {"slow": "parallel", "fast": "parallel", "block": "parallel", "spawn": "parallel"}


class _Tools:
    provider_name = "supervisor_test"
    _capabilities = {"spawn": (CAP_SPAWN_AGENT,)}

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.running = 0
        self.max_running = 0
        self.cancelled: set[str] = set()
        self.block_started = asyncio.Event()

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        return {
            name: ToolDescriptor(
                name=name,
                description=name,
                provider=self.provider_name,
                input_schema={"type": "object"},
                capabilities=self._capabilities.get(name, ()),
                execution_mode=_MODES.get(name, "exclusive"),  # type: ignore[arg-type]
            )
            for name in _TOOL_NAMES
        }

    async def execute(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        validated_arguments: Any = None,
    ) -> ProviderExecutionResult:
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.events.append(("start", tool_name))
        try:
            if tool_name == "slow":
                await asyncio.sleep(0.15)
            elif tool_name == "fast":
                await asyncio.sleep(0.02)
            elif tool_name == "exclusive":
                await asyncio.sleep(0.05)
            elif tool_name == "block":
                self.block_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.add(tool_name)
                    raise
        finally:
            self.running -= 1
        self.events.append(("end", tool_name))
        return ProviderExecutionResult(status="completed", output={"tool": tool_name, **raw_arguments})

    async def refresh(self) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


class _FakeSubAgents:
    def get(self, name: str) -> SubAgentProfile:
        return SubAgentProfile(
            name=name,
            prompt_file="",
            allowed_tools=list(_TOOL_NAMES),
        )  # type: ignore[arg-type]

    def node(self, name: str) -> AgentNode:
        is_main = name == "main_agent"
        return AgentNode(
            profile=self.get(name),
            scope="conversation" if is_main else "worker",
            publish_text=is_main,
            spawnable=("search_worker", "navigation_worker") if is_main else (),
        )


class _BatchProvider:
    """Scripted provider without the sequential-agent assertion of the shared stub."""

    streaming = False

    def __init__(self, steps: list[ModelResponse]) -> None:
        self._steps = list(steps)
        self.calls: list[str] = []

    async def complete(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs["runtime_hints"]["active_subagent"])
        return self._steps.pop(0)


class _SleepProvider:
    """Worker calls sleep so overlap is observable; main is scripted."""

    streaming = False

    def __init__(self, main_steps: list[ModelResponse]) -> None:
        self._main_steps = list(main_steps)
        self.calls: list[str] = []
        self.running_workers = 0
        self.max_workers = 0
        self.windows: list[tuple[str, float, float]] = []

    async def complete(self, **kwargs: Any) -> ModelResponse:
        agent = kwargs["runtime_hints"]["active_subagent"]
        self.calls.append(agent)
        if agent == "main_agent":
            return self._main_steps.pop(0)
        self.running_workers += 1
        self.max_workers = max(self.max_workers, self.running_workers)
        started = time.monotonic()
        try:
            await asyncio.sleep(0.15)
            return ModelResponse(
                text=f"{agent} done",
                status="completed",
                protocol="chat_completions",
                reported_model="stub",
            )
        finally:
            self.running_workers -= 1
            self.windows.append((agent, started, time.monotonic()))


def _kernel(
    tmp_path: Path,
    *,
    provider: Any,
    tools: _Tools,
) -> AgentKernel:
    return AgentKernel(
        context_builder=_ContextBuilder(),  # type: ignore[arg-type]
        subagent_builder=_FakeSubAgents(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(
            providers=[tools],
            permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
        ),
        provider_adapter=provider,  # type: ignore[arg-type]
        session_store=_Store(),  # type: ignore[arg-type]
        max_steps=4,
    )


def _turn(message: str = "go", *, session_id: str = "s_supervisor") -> TurnInput:
    request = ChatRequest(session_id=session_id, message=message)
    return TurnInput(
        session_id=session_id,
        client_id=None,
        message=message,
        request=request,
        request_payload=request.model_dump(mode="json"),
    )


def test_parallel_calls_overlap_and_turns_stay_in_model_order(tmp_path: Path) -> None:
    tools = _Tools()
    provider = _BatchProvider([
        ModelResponse(
            tool_calls=[
                ModelToolCall("c1", "slow", {}),
                ModelToolCall("c2", "fast", {}),
            ]
        ),
        _text("done"),
    ])
    kernel = _kernel(tmp_path, provider=provider, tools=tools)
    events = CollectingPublisher()

    result = asyncio.run(kernel.run_turn(_turn(), events=events))

    assert result.text == "done"
    assert tools.max_running == 2
    # Fast finished first but commits/turns follow the model's call order.
    assert tools.events.index(("end", "fast")) < tools.events.index(("end", "slow"))
    completed = [data["tool"] for name, data, _output in events.events if name == RunEvent.TOOL_COMPLETED]
    assert completed == ["slow", "fast"]
    assert [turn.name for turn in result.state.turns if turn.role == "tool"] == ["slow", "fast"]


def test_exclusive_call_is_a_barrier(tmp_path: Path) -> None:
    tools = _Tools()
    provider = _BatchProvider([
        ModelResponse(
            tool_calls=[
                ModelToolCall("c1", "slow", {}),
                ModelToolCall("c2", "exclusive", {}),
                ModelToolCall("c3", "fast", {}),
            ]
        ),
        _text("done"),
    ])
    kernel = _kernel(tmp_path, provider=provider, tools=tools)

    asyncio.run(kernel.run_turn(_turn(), events=CollectingPublisher()))

    assert tools.max_running == 1
    assert tools.events == [
        ("start", "slow"),
        ("end", "slow"),
        ("start", "exclusive"),
        ("end", "exclusive"),
        ("start", "fast"),
        ("end", "fast"),
    ]


def test_cancelling_a_parallel_batch_closes_spans_and_keeps_finished_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[AgentSessionState, CollectingPublisher]:
        tools = _Tools()
        provider = _BatchProvider([
            ModelResponse(
                tool_calls=[
                    ModelToolCall("c1", "block", {}),
                    ModelToolCall("c2", "fast", {}),
                ]
            ),
        ])
        kernel = _kernel(tmp_path, provider=provider, tools=tools)
        events = CollectingPublisher()
        run = asyncio.create_task(kernel.run_turn(_turn(), events=events))
        await asyncio.wait_for(tools.block_started.wait(), timeout=5)
        await asyncio.sleep(0.05)  # the fast sibling finishes before the cancel
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        await asyncio.sleep(0)
        assert [
            item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()
        ] == []
        store = kernel._session_store
        assert isinstance(store, _Store)
        return store.states["s_supervisor"], events

    state, events = asyncio.run(scenario())

    assert state.active_subagent == "main_agent"
    tool_turns = [turn for turn in state.turns if turn.role == "tool"]
    assert [turn.name for turn in tool_turns] == ["fast"]
    failures = [data for name, data, _output in events.events if name == RunEvent.TOOL_FAILED]
    assert [item["tool"] for item in failures] == ["block"]
    assert "cancelled" in failures[0]["error"]
    # The finished sibling's span closed normally and is not reported as failed.
    assert [data for name, data, _output in events.events if name == RunEvent.TOOL_COMPLETED] == [
        {"tool": "fast", "call_id": "c2", "active_subagent": "main_agent", "worker_run_id": None}
    ]


class _PassthroughHooks:
    async def before(self, task: ToolTask) -> None:
        return None

    def on_update(self, task: ToolTask, partial: dict[str, Any]) -> None:
        return None

    async def after(self, task: ToolTask, result: Any) -> Any:
        return result

    async def commit(self, task: ToolTask, result: Any) -> Any:
        return result


def test_first_or_interval_returns_done_and_pending_and_cancels_cleanly(tmp_path: Path) -> None:
    async def scenario() -> None:
        tools = _Tools()
        registry = ToolRegistry(
            providers=[tools],
            permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
        )
        dispatcher = ToolDispatcher(tool_registry=registry, hooks=_PassthroughHooks())
        supervisor = LoopSupervisor(
            dispatcher=dispatcher,
            policy=WaitPolicy(mode="first_or_interval", interval_s=1.0),
        )
        slow = await dispatcher.submit(
            ModelToolCall("c1", "block", {}),
            session_id="s_test",
            runtime_context={},
            allowed_tools=["block"],
            skill_execution=SkillExecution(),
        )
        fast = await dispatcher.submit(
            ModelToolCall("c2", "fast", {}),
            session_id="s_test",
            runtime_context={},
            allowed_tools=["fast"],
            skill_execution=SkillExecution(),
        )
        supervisor.track(slow, fast)

        settled = await supervisor.wait([slow, fast])

        assert settled.done == (fast,)
        assert settled.pending == (slow,)
        assert supervisor.tracked == (slow,)
        await dispatcher.settle(fast)
        assert fast.finalized and fast.result is not None and fast.result.status == "completed"

        await supervisor.cancel_all()

        assert tools.cancelled == {"block"}
        assert slow.cancelled and slow.finalized
        assert slow.result is not None and slow.result.status == "failed"
        await asyncio.sleep(0)
        assert [
            item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()
        ] == []

    asyncio.run(scenario())


class _StuckCommitHooks(_PassthroughHooks):
    """The first commit hangs so the caller of ``cancel_all`` can be cancelled mid-finalize."""

    def __init__(self) -> None:
        self.commit_entered = asyncio.Event()

    async def commit(self, task: ToolTask, result: Any) -> Any:
        self.commit_entered.set()
        await asyncio.Event().wait()
        return result


def test_second_cancellation_during_cancel_all_still_cancels_later_siblings(tmp_path: Path) -> None:
    async def scenario() -> None:
        tools = _Tools()
        registry = ToolRegistry(
            providers=[tools],
            permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
        )
        hooks = _StuckCommitHooks()
        dispatcher = ToolDispatcher(tool_registry=registry, hooks=hooks)
        supervisor = LoopSupervisor(dispatcher=dispatcher)
        first, second = [
            await dispatcher.submit(
                ModelToolCall(call_id, "block", {}),
                session_id="s_test",
                runtime_context={},
                allowed_tools=["block"],
                skill_execution=SkillExecution(),
            )
            for call_id in ("c1", "c2")
        ]
        supervisor.track(first, second)
        await asyncio.wait_for(tools.block_started.wait(), timeout=5)

        canceller = asyncio.create_task(supervisor.cancel_all())
        await asyncio.wait_for(hooks.commit_entered.wait(), timeout=5)
        canceller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await canceller

        # The outer cancellation propagated while finalizing ``first``; the
        # later sibling must not be left running in the background.
        assert second.task is not None
        await asyncio.wait({second.task}, timeout=1)
        assert second.task.done() and second.task.cancelled()
        assert supervisor.tracked == (second,)

    asyncio.run(scenario())


def test_non_cancel_error_finalizes_sibling_parallel_calls(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> _Tools:
        tools = _Tools()

        async def boom_wait(self, tasks):  # type: ignore[no-untyped-def]
            await asyncio.wait_for(tools.block_started.wait(), timeout=5)
            raise RuntimeError("supervisor boom")

        provider = _BatchProvider([
            ModelResponse(
                tool_calls=[
                    ModelToolCall("c1", "block", {}),
                    ModelToolCall("c2", "fast", {}),
                ]
            ),
        ])
        kernel = _kernel(tmp_path, provider=provider, tools=tools)
        monkeypatch.setattr(LoopSupervisor, "wait", boom_wait)
        with pytest.raises(RuntimeError, match="supervisor boom"):
            await kernel.run_turn(_turn(), events=CollectingPublisher())
        await asyncio.sleep(0)
        assert [
            item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()
        ] == []
        return tools

    tools = asyncio.run(scenario())

    # The non-cancellation failure still cancelled both running siblings.
    assert tools.cancelled == {"block"}
    assert tools.max_running >= 1


def test_execution_mode_collection_failure_falls_back_to_exclusive(tmp_path: Path) -> None:
    tools = _Tools()
    provider = _BatchProvider([
        ModelResponse(tool_calls=[ModelToolCall("c1", "echo", {})]),
        _text("done"),
    ])
    kernel = _kernel(tmp_path, provider=provider, tools=tools)

    async def broken_modes(_names):
        raise ValueError("descriptor source unavailable")

    kernel._tool_registry.execution_modes = broken_modes  # type: ignore[method-assign]
    result = asyncio.run(kernel.run_turn(_turn(), events=CollectingPublisher()))

    assert result.text == "done"
    assert [turn.name for turn in result.state.turns if turn.role == "tool"] == ["echo"]


def test_parallel_workers_overlap_and_restore_the_parent_agent(tmp_path: Path) -> None:
    async def scenario() -> tuple[AgentSessionState, list[str], CollectingPublisher, _SleepProvider]:
        tools = _Tools()
        provider = _SleepProvider([
            ModelResponse(
                tool_calls=[
                    ModelToolCall("c1", "spawn", {"worker": "search_worker", "task": "search"}),
                    ModelToolCall("c2", "spawn", {"worker": "navigation_worker", "task": "navigate"}),
                ]
            ),
            _text("main done"),
        ])
        kernel = _kernel(tmp_path, provider=provider, tools=tools)
        events = CollectingPublisher()
        result = await kernel.run_turn(_turn(), events=events)
        run_workers = [run["worker"] for run in result.state.working_memory["worker_runs"]]
        return result.state, run_workers, events, provider

    state, run_workers, events, provider = asyncio.run(scenario())

    assert provider.max_workers == 2
    # The two worker model calls overlap: a sequential pair would not.
    assert len(provider.windows) == 2
    slowest_start = max(window[1] for window in provider.windows)
    fastest_end = min(window[2] for window in provider.windows)
    assert slowest_start < fastest_end
    assert run_workers == ["search_worker", "navigation_worker"]
    assert state.active_subagent == "main_agent"
    worker_model_turns = [
        turn
        for turn in state.turns
        if turn.scope == "worker"
        and turn.role == "assistant"
        and isinstance(turn.payload.get("model"), dict)
    ]
    assert [turn.agent for turn in worker_model_turns] == ["search_worker", "navigation_worker"]
    started = [data for name, data, _output in events.events if name == RunEvent.WORKER_STARTED]
    assert len(started) == 2
    assert len({item["run_id"] for item in started}) == 2
    completed = [data for name, data, _output in events.events if name == RunEvent.WORKER_COMPLETED]
    assert [item["worker"] for item in completed] == ["search_worker", "navigation_worker"]
