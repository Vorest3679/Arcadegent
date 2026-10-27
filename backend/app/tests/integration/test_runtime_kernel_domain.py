"""Kernel contract tests (plan 4, stages 2.5–2.7): the runtime must run without the arcade domain.

The runtime is wired with stub context/subagent builders, a scripted provider,
an in-memory store and a tiny tool provider. ``BaseTurnDomain`` alone must keep
the kernel invariants; a minimal non-arcade domain only overrides what it needs.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.runtime.domain_port import BaseTurnDomain, TurnReply
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.runtime.lifecycle import (
    AgentEnvironment,
    LifecycleContext,
    LifecyclePhase,
    LifecycleScope,
    LifecycleViolation,
    RunScope,
    StateView,
    TurnScope,
)
from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor
from app.agent.tools.execution import CAP_CONCLUDES_TURN, CAP_SPAWN_AGENT
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.protocol.messages import ChatRequest
from app.session.events import RunEvent
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


def test_base_domain_runs_two_turns_without_stale_reply(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("finish", reply="first reply")),
        ("main_agent", _call("echo", value=1)),
        ("main_agent", _text("second reply")),
    ])
    runtime = _runtime(tmp_path, provider, store)

    first, _ = _run(runtime, "one")
    second, events = _run(runtime, "two")

    assert first.response == TurnReply(session_id="s_kernel", reply="first reply")
    # The concluded reply of turn 1 must not end turn 2 right after its first tool call.
    assert second.response == TurnReply(session_id="s_kernel", reply="second reply")
    assert provider.calls == ["main_agent"] * 3
    assert events.names()[-1] == RunEvent.ASSISTANT_COMPLETED
    assert store.states["s_kernel"].working_memory["reply"] == "second reply"


def test_base_domain_marks_worker_failed_after_tool_failure(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _call("boom")),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    runtime = _runtime(tmp_path, provider, store)

    response, events = _run(runtime, "go")

    assert response.text == "main done"
    run = store.states["s_kernel"].working_memory["worker_runs"][-1]
    assert run["worker"] == "helper_worker"
    assert run["status"] == "failed"
    assert RunEvent.WORKER_FAILED in events.names()
    assert store.states["s_kernel"].active_subagent == "main_agent"


def test_unknown_worker_is_rejected_by_kernel(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="ghost_worker", task="do it")),
        ("main_agent", _text("handled")),
    ])
    runtime = _runtime(tmp_path, provider, store)

    response, events = _run(runtime, "go")

    assert response.text == "handled"
    assert RunEvent.WORKER_STARTED not in events.names()
    run_turns = [turn for turn in store.states["s_kernel"].turns if turn.name == "spawn"]
    assert run_turns and run_turns[-1].payload["status"] == "failed"


class _CodingDomain(BaseTurnDomain):
    """A third-party domain only overrides the hooks it cares about."""

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        result = ctx.tool_result
        if result.status == "completed" and result.tool_name == "read_file":
            files = ctx.memory.setdefault("files_read", {})
            files[result.output["path"]] = len(result.output["content"])

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> dict[str, Any]:
        return {"session_id": session_id, "reply": final_text, "files": sorted(state.working_memory.get("files_read", {}))}


def test_minimal_coding_domain_overrides_only_what_it_needs(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("read_file", path="app/main.py")),
        ("main_agent", _text("main.py sets x")),
    ])
    runtime = _runtime(tmp_path, provider, store, domain=_CodingDomain())

    response, events = _run(runtime, "what does main.py do?")

    assert response.response == {"session_id": "s_kernel", "reply": "main.py sets x", "files": ["app/main.py"]}
    started = next(data for name, data, _ in events.events if name == RunEvent.SESSION_STARTED)
    assert "intent" not in started


class _RecordingDomain(BaseTurnDomain):
    """Record lifecycle order; hooks are notifications, so no ``super()`` calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def on_run_create(self, ctx: LifecycleContext) -> None:
        self.calls.append("run.create")

    async def on_run_mount(self, ctx: LifecycleContext) -> None:
        self.calls.append("run.mount")

    async def on_run_unmounted(self, ctx: LifecycleContext) -> None:
        self.calls.append("run.unmounted")

    async def on_turn_create(self, ctx: LifecycleContext) -> None:
        self.calls.append("turn.create")

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        self.calls.append("turn.mount")

    async def on_turn_before_unmount(self, ctx: LifecycleContext) -> None:
        self.calls.append("turn.before_unmount")

    async def on_turn_unmounted(self, ctx: LifecycleContext) -> None:
        self.calls.append("turn.unmounted")

    async def on_step_start(self, ctx: LifecycleContext) -> None:
        self.calls.append("step.create")
        if ctx.step is not None:
            ctx.step.policy.runtime_hints["lifecycle_step"] = "active"

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        self.calls.append("step.update")

    async def on_step_end(self, ctx: LifecycleContext) -> None:
        self.calls.append("step.unmounted")

    async def on_worker_mount(self, ctx: LifecycleContext) -> None:
        self.calls.append("worker.mount")

    async def on_worker_unmounted(self, ctx: LifecycleContext) -> None:
        self.calls.append("worker.unmounted")

    async def on_error(self, ctx: LifecycleContext) -> None:
        self.calls.append(f"{ctx.scope.value}.error")


class _RaisingProvider:
    streaming = False

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def complete(self, **kwargs: Any) -> ModelResponse:
        raise self.error


def test_lifecycle_order_text_turn(tmp_path: Path) -> None:
    domain = _RecordingDomain()
    provider = _Provider([("main_agent", _text("done"))])
    runtime = _runtime(tmp_path, provider, _Store(), domain)

    result, _events = _run(runtime, "hello")

    assert result.text == "done"
    assert provider.hints[-1]["lifecycle_step"] == "active"
    assert domain.calls == [
        "run.create",
        "run.mount",
        "turn.create",
        "turn.mount",
        "step.create",
        "step.unmounted",
        "turn.before_unmount",
        "turn.unmounted",
        "run.unmounted",
    ]


def test_lifecycle_tool_update_and_worker_scope(tmp_path: Path) -> None:
    domain = _RecordingDomain()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="work")),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    runtime = _runtime(tmp_path, provider, _Store(), domain)

    result, _events = _run(runtime, "go")

    assert result.text == "main done"
    assert domain.calls.count("worker.mount") == 1
    assert domain.calls.count("worker.unmounted") == 1
    assert "step.update" in domain.calls


def test_lifecycle_error_unmounts_turn_and_run_once(tmp_path: Path) -> None:
    domain = _RecordingDomain()
    runtime = _runtime(tmp_path, _RaisingProvider(RuntimeError("boom")), _Store(), domain)

    with pytest.raises(RuntimeError, match="boom"):
        _run(runtime, "explode")

    assert domain.calls.count("step.unmounted") == 1
    assert domain.calls.count("turn.error") == 1
    assert domain.calls.count("turn.before_unmount") == 1
    assert domain.calls.count("turn.unmounted") == 1
    assert domain.calls.count("run.error") == 1
    assert domain.calls.count("run.unmounted") == 1


def test_lifecycle_cancellation_unmounts_turn_and_run_once(tmp_path: Path) -> None:
    domain = _RecordingDomain()
    runtime = _runtime(tmp_path, _RaisingProvider(asyncio.CancelledError()), _Store(), domain)

    with pytest.raises(asyncio.CancelledError):
        _run(runtime, "cancel")

    assert domain.calls.count("step.unmounted") == 1
    assert domain.calls.count("turn.error") == 1
    assert domain.calls.count("turn.unmounted") == 1
    assert domain.calls.count("run.error") == 1
    assert domain.calls.count("run.unmounted") == 1


class _WorkerRaisingProvider:
    streaming = False

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def complete(self, **kwargs: Any) -> ModelResponse:
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            return _call("spawn", worker="helper_worker", task="work")
        raise self.error


def test_lifecycle_worker_exception_unmounts_worker_once(tmp_path: Path) -> None:
    domain = _RecordingDomain()
    store = _Store()
    runtime = _runtime(tmp_path, _WorkerRaisingProvider(RuntimeError("worker boom")), store, domain)

    with pytest.raises(RuntimeError, match="worker boom"):
        _run(runtime, "go")

    assert domain.calls.count("worker.mount") == 1
    assert domain.calls.count("worker.error") == 1
    assert domain.calls.count("worker.unmounted") == 1
    assert domain.calls.count("turn.unmounted") == 1
    assert domain.calls.count("run.unmounted") == 1
    assert store.states["s_kernel"].active_subagent == "main_agent"


class _WritableHooksDomain(BaseTurnDomain):
    """Hooks change domain memory and intent; kernel state stays out of reach."""

    def __init__(self) -> None:
        self.applied: list[tuple[str, str]] = []
        self.observed: dict[str, Any] = {}

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> dict[str, Any]:
        return {"reply": final_text}

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        ctx.memory["prepared"] = True
        ctx.state.intent = "coding"

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        self.applied.append((ctx.tool_result.tool_name, "worker" if ctx.worker else "main"))
        ctx.memory["last_tool"] = ctx.tool_result.tool_name

    async def on_worker_mount(self, ctx: LifecycleContext) -> None:
        ctx.memory["seeded_from"] = ctx.worker.parent_state.intent
        # The parent is read-only: its memory is a copy and its intent is not assignable.
        ctx.worker.parent_state.working_memory["leak"] = True
        with pytest.raises(AttributeError):
            ctx.worker.parent_state.intent = "hijack"

    async def on_worker_unmounted(self, ctx: LifecycleContext) -> None:
        self.observed["worker_memory"] = (ctx.memory.get("seeded_from"), ctx.memory.get("last_tool"))
        with pytest.raises(AttributeError):
            ctx.worker.state.status = "failed"
        with pytest.raises(AttributeError):
            ctx.worker.state.working_memory = {}
        self.observed["env_has_store"] = hasattr(ctx.env, "session_store")

    async def on_turn_before_unmount(self, ctx: LifecycleContext) -> None:
        self.observed["response"] = ctx.response


def test_hooks_write_domain_memory_but_not_kernel_state(tmp_path: Path) -> None:
    domain = _WritableHooksDomain()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="work")),
        ("helper_worker", _call("echo", value=1)),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    store = _Store()

    result, _events = _run(_runtime(tmp_path, provider, store, domain), "go")

    assert result.response == {"reply": "main done"}
    # on_step_update writes the state that ran the tool: worker first, then main.
    assert domain.applied == [("echo", "worker"), ("spawn", "main")]
    assert domain.observed == {
        "worker_memory": ("coding", "echo"),
        "env_has_store": False,
        "response": {"reply": "main done"},
    }
    saved = store.states["s_kernel"]
    assert saved.status == "completed"
    assert saved.intent == "coding"
    assert saved.working_memory["prepared"] is True
    assert saved.working_memory["last_tool"] == "spawn"
    assert "leak" not in saved.working_memory


class _ReservedKeyDomain(BaseTurnDomain):
    async def on_step_update(self, ctx: LifecycleContext) -> None:
        ctx.memory["reply"] = "hijacked"


def test_hook_writing_kernel_memory_is_rejected_and_restored(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([("main_agent", _call("echo", value=1))])
    runtime = _runtime(tmp_path, provider, store, _ReservedKeyDomain())

    with pytest.raises(LifecycleViolation):
        _run(runtime, "go")

    saved = store.states["s_kernel"]
    assert saved.status == "failed"
    assert saved.working_memory.get("reply") != "hijacked"


class _WorkerCancelProvider:
    streaming = False

    def __init__(self) -> None:
        self.worker_calls = 0

    async def complete(self, **kwargs: Any) -> ModelResponse:
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            return _call("spawn", worker="helper_worker", task="work")
        self.worker_calls += 1
        if self.worker_calls == 1:
            return _call("echo", value=1)
        raise asyncio.CancelledError()


def test_worker_cancellation_keeps_finished_evidence_once(tmp_path: Path) -> None:
    store = _Store()
    runtime = _runtime(tmp_path, _WorkerCancelProvider(), store)

    with pytest.raises(asyncio.CancelledError):
        _run(runtime, "go")

    saved = store.states["s_kernel"]
    worker_tool_turns = [turn for turn in saved.turns if turn.name == "echo" and turn.scope == "worker"]
    assert len(worker_tool_turns) == 1
    assert saved.active_subagent == "main_agent"
    # The session layer records the cancelled terminal state; the kernel does not.
    assert saved.status == "running"


def test_guard_tracks_equal_but_distinct_states_separately() -> None:
    first, second = AgentSessionState(session_id="s_same"), AgentSessionState(session_id="s_same")
    assert first == second and first is not second
    ctx = LifecycleContext(
        scope=LifecycleScope.TURN,
        phase=LifecyclePhase.MOUNT,
        env=AgentEnvironment(provider=None, tools=None),
        session_id="s_same",
        run=RunScope(session_id="s_same", state=StateView(first)),
        turn=TurnScope(session_id="s_same", turn_index=0, state=StateView(second), request=None),
    )

    guarded = ctx._guarded_states()

    assert len(guarded) == 2
    assert guarded[0] is first and guarded[1] is second


class _RaisingMutationDomain(BaseTurnDomain):
    async def on_step_update(self, ctx: LifecycleContext) -> None:
        ctx.memory["reply"] = "hijacked"
        raise RuntimeError("hook boom")


def test_hook_exception_does_not_leave_kernel_memory_mutated(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([("main_agent", _call("echo", value=1))])
    runtime = _runtime(tmp_path, provider, store, _RaisingMutationDomain())

    with pytest.raises(RuntimeError, match="hook boom"):
        _run(runtime, "go")

    saved = store.states["s_kernel"]
    assert saved.status == "failed"
    assert saved.working_memory.get("reply") != "hijacked"


@pytest.mark.parametrize("writable", [False, True])
def test_state_view_does_not_expose_mutable_kernel_objects(writable: bool) -> None:
    state = AgentSessionState(
        session_id="s_view",
        turns=[AgentTurn(role="user", content="original", payload={"nested": {"value": 1}})],
    )
    view = StateView(state, writable=writable)

    view.turns[0].content = "changed"
    view.turns[0].payload["nested"]["value"] = 2
    view.skill_execution.loaded_bytes = 100

    assert state.turns[0].content == "original"
    assert state.turns[0].payload == {"nested": {"value": 1}}
    assert state.skill_execution.loaded_bytes == 0
    # Reads still reflect later kernel changes rather than a stale snapshot.
    state.turns[0].content = "updated by kernel"
    assert view.turns[0].content == "updated by kernel"


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("boundary", ["start", "context"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_step_cleanup_covers_start_and_context_failures(
    tmp_path: Path, worker: bool, boundary: str, error_type: type[BaseException],
) -> None:
    error = error_type("step interrupted")

    class FailingDomain(_RecordingDomain):
        async def on_step_start(self, ctx: LifecycleContext) -> None:
            await super().on_step_start(ctx)
            if boundary == "start" and bool(ctx.worker) == worker:
                raise error

    class FailingContext(_ContextBuilder):
        def build(self, **kwargs: Any) -> _Context:
            if boundary == "context" and (kwargs["subagent"].name == "helper_worker") == worker:
                raise error
            return super().build(**kwargs)

    domain = FailingDomain()
    script = [("main_agent", _call("spawn", worker="helper_worker", task="work"))] if worker else []
    runtime = _runtime(tmp_path, _Provider(script), _Store(), domain)
    runtime._context_builder = FailingContext()  # type: ignore[assignment]

    with pytest.raises(error_type) as caught:
        _run(runtime, "go")

    assert caught.value is error
    assert domain.calls.count("step.create") == (2 if worker else 1)
    assert domain.calls.count("step.unmounted") == domain.calls.count("step.create")
    assert domain.calls.count("turn.unmounted") == 1
    assert domain.calls.count("run.unmounted") == 1
    assert domain.calls.count("worker.unmounted") == (1 if worker else 0)
