"""Tool execution (plan 4, stage 4): per-call tasks, hooks and execution modes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelToolCall
from app.agent.skills.execution import SkillExecution
from app.agent.tools.base import ProviderExecutionResult, ToolArgumentPreparationError, ToolDescriptor
from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.execution import ToolDispatcher, ToolTask
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry


class _Tools:
    provider_name = "execution_test"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        return {
            name: ToolDescriptor(
                name=name,
                description=name,
                provider=self.provider_name,
                input_schema={"type": "object"},
                execution_mode="parallel" if name == "parallel_echo" else "exclusive",
            )
            for name in ("echo", "parallel_echo", "slow", "fail")
        }

    async def execute(self, *, tool_name: str, raw_arguments: dict[str, Any], validated_arguments: Any = None):
        if tool_name == "slow":
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if tool_name == "fail":
            return ProviderExecutionResult(
                status="failed",
                output={"error": {"message": "nope"}},
                error_message="nope",
            )
        return ProviderExecutionResult(status="completed", output={"echo": raw_arguments})

    async def refresh(self) -> None:
        return None

    def health(self) -> dict[str, Any]:
        return {}


class _RecordingHooks:
    def __init__(self) -> None:
        self.before_tasks: list[ToolTask] = []
        self.after_tasks: list[ToolTask] = []
        self.after_results: list[Any] = []
        self.commit_tasks: list[ToolTask] = []
        self.commit_results: list[Any] = []

    async def before(self, task: ToolTask) -> None:
        self.before_tasks.append(task)

    def on_update(self, task: ToolTask, partial: dict[str, Any]) -> None:
        raise AssertionError("no partial tool output is published yet")

    async def after(self, task: ToolTask, result: Any) -> Any:
        self.after_tasks.append(task)
        self.after_results.append(result)
        return result

    async def commit(self, task: ToolTask, result: Any) -> Any:
        self.commit_tasks.append(task)
        self.commit_results.append(result)
        return result


def _registry(tmp_path: Path, provider: Any | None = None) -> ToolRegistry:
    return ToolRegistry(
        providers=[provider or _Tools()],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
    )


def _call(name: str, **arguments: Any) -> ModelToolCall:
    return ModelToolCall(call_id=f"call_{name}", name=name, arguments=arguments)


def test_dispatcher_settles_each_call_as_a_task_and_keeps_evidence(tmp_path: Path) -> None:
    hooks = _RecordingHooks()
    dispatcher = ToolDispatcher(tool_registry=_registry(tmp_path), hooks=hooks)

    result = asyncio.run(
        dispatcher.execute(
            _call("echo", value=1),
            session_id="s_test",
            runtime_context={"k": 1},
            allowed_tools=["echo"],
            agent_name="main_agent",
            skill_execution=SkillExecution(),
        )
    )

    assert result.status == "completed"
    assert result.output == {"echo": {"value": 1}}
    task = hooks.before_tasks[0]
    assert task is hooks.after_tasks[0]
    assert task is hooks.commit_tasks[0]
    assert hooks.after_results == [result]
    assert hooks.commit_results == [result]
    assert isinstance(task.task, asyncio.Task) and task.task.done()
    assert task.agent_name == "main_agent"
    assert task.finalized
    assert task.prepared_args == {"value": 1}
    assert task.argument_evidence["prepared_arguments"] == {"value": 1}
    assert task.argument_evidence["preparation_error"] is None


def test_dispatcher_reports_preparation_failure_as_a_failed_result(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def broken(**kwargs: Any) -> None:
        raise ValueError("bad args")

    registry.prepare_arguments = broken  # type: ignore[method-assign]
    hooks = _RecordingHooks()
    dispatcher = ToolDispatcher(tool_registry=registry, hooks=hooks)

    result = asyncio.run(
        dispatcher.execute(
            _call("echo", value=1),
            session_id="s_test",
            runtime_context={},
            allowed_tools=["echo"],
            agent_name="main_agent",
            skill_execution=SkillExecution(),
        )
    )

    task = hooks.before_tasks[0]
    assert result.status == "failed"
    assert result.output["error"]["type"] == "argument_preparation_error"
    assert task.preparation_error == "ValueError: bad args"
    # Preparation failures keep the parsed arguments as the attempted payload.
    assert task.argument_evidence["prepared_arguments"] == {"value": 1}
    assert task.argument_evidence["preparation_error"] == "ValueError: bad args"


def test_prepare_failure_logs_the_safe_reason(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    registry = _registry(tmp_path)

    async def broken(**kwargs: Any) -> None:
        raise ToolArgumentPreparationError("no current-turn search candidates are available")

    registry.prepare_arguments = broken  # type: ignore[method-assign]
    dispatcher = ToolDispatcher(tool_registry=registry, hooks=_RecordingHooks())

    with caplog.at_level("WARNING", logger="app.agent.tools.execution"):
        result = asyncio.run(
            dispatcher.execute(
                _call("echo", value=1),
                session_id="s_test",
                runtime_context={},
                allowed_tools=["echo"],
                agent_name="main_agent",
                skill_execution=SkillExecution(),
            )
        )

    assert result.status == "failed"
    messages = [record.getMessage() for record in caplog.records if record.name == "app.agent.tools.execution"]
    assert any(
        "tool.prepare_failed" in message
        and "error_type=ToolArgumentPreparationError" in message
        and "error_message=no current-turn search candidates are available" in message
        for message in messages
    )


def test_prepare_failure_does_not_log_unexpected_error_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    registry = _registry(tmp_path)

    async def broken(**kwargs: Any) -> None:
        raise RuntimeError("upstream key=secret-value")

    registry.prepare_arguments = broken  # type: ignore[method-assign]
    dispatcher = ToolDispatcher(tool_registry=registry, hooks=_RecordingHooks())

    with caplog.at_level("WARNING", logger="app.agent.tools.execution"):
        asyncio.run(
            dispatcher.execute(
                _call("echo", value=1),
                session_id="s_test",
                runtime_context={},
                allowed_tools=["echo"],
                agent_name="main_agent",
                skill_execution=SkillExecution(),
            )
        )

    messages = [record.getMessage() for record in caplog.records if record.name == "app.agent.tools.execution"]
    assert messages
    assert all("secret-value" not in message for message in messages)
    assert any("error_type=RuntimeError" in message and "error_message=ref:" in message for message in messages)


def test_cancelling_a_settled_call_cancels_the_running_tool_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = _Tools()
        dispatcher = ToolDispatcher(tool_registry=_registry(tmp_path, provider), hooks=_RecordingHooks())
        task = await dispatcher.submit(
            _call("slow"),
            session_id="s_test",
            runtime_context={},
            allowed_tools=["slow"],
            agent_name="main_agent",
            skill_execution=SkillExecution(),
        )
        settle = asyncio.create_task(dispatcher.settle(task))
        await asyncio.wait_for(provider.started.wait(), timeout=5)

        settle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await settle

        assert provider.cancelled.is_set()
        assert task.task is not None and task.task.done()
        await asyncio.sleep(0)
        assert [item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()] == []

    asyncio.run(scenario())


class _AfterRaisesHooks(_RecordingHooks):
    async def after(self, task: ToolTask, result: Any) -> Any:
        raise RuntimeError("after boom")


def test_cancel_preserves_a_real_task_error_instead_of_cancelling(tmp_path: Path) -> None:
    async def scenario() -> None:
        dispatcher = ToolDispatcher(
            tool_registry=_registry(tmp_path),
            hooks=_AfterRaisesHooks(),
        )
        task = await dispatcher.submit(
            _call("echo", value=1),
            session_id="s_test",
            runtime_context={},
            allowed_tools=["echo"],
            agent_name="main_agent",
            skill_execution=SkillExecution(),
        )
        while task.task is not None and not task.task.done():
            await asyncio.sleep(0)

        result = await dispatcher.cancel(task)

        assert result.status == "failed"
        assert result.output["error"]["type"] == "tool_execution_error"
        assert "after boom" in (result.error_message or "")
        assert task.cancelled is False
        assert task.finalized

    asyncio.run(scenario())


def test_registry_reports_declared_execution_mode(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def modes() -> tuple[str, str, str]:
        return (
            await registry.execution_mode("echo"),
            await registry.execution_mode("parallel_echo"),
            await registry.execution_mode("missing_tool"),
        )

    assert asyncio.run(modes()) == ("exclusive", "parallel", "exclusive")


def test_builtin_tools_declare_parallel_only_for_the_safe_reads() -> None:
    tools = asyncio.run(BuiltinToolProvider().get_tools())

    # Stage 7 prerequisites are in place: skill snapshots are locked and worker
    # slots keep the most recent running worker, so these three can overlap.
    assert {name for name, tool in tools.items() if tool.execution_mode == "parallel"} == {
        "invoke_worker",
        "list_skills",
        "read_skill",
    }
    assert tools["db_query_tool"].execution_mode == "exclusive"
    assert tools["summary_tool"].execution_mode == "exclusive"


def test_builtin_provider_reads_a_declared_parallel_mode(tmp_path: Path) -> None:
    tool = {
        "name": "list_skills",
        "description": "list skills",
        "executor": "app.agent.tools.builtin.executors.skills:list_skills",
        "input_schema": {"type": "object"},
        "execution_mode": "parallel",
    }
    (tmp_path / "list_skills.json").write_text(json.dumps(tool), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"tools": ["list_skills.json"]}), encoding="utf-8")
    provider = BuiltinToolProvider(manifest_path=tmp_path / "manifest.json")
    registry = ToolRegistry(
        providers=[provider],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
    )

    assert asyncio.run(provider.get_tools())["list_skills"].execution_mode == "parallel"
    assert asyncio.run(registry.execution_mode("list_skills")) == "parallel"


def test_tool_descriptor_defaults_to_exclusive() -> None:
    descriptor = ToolDescriptor(name="x", description="", provider="p", input_schema={"type": "object"})
    assert descriptor.execution_mode == "exclusive"


def test_invalid_builtin_execution_mode_is_rejected(tmp_path: Path) -> None:
    tool = {
        "name": "list_skills",
        "description": "list skills",
        "executor": "app.agent.tools.builtin.executors.skills:list_skills",
        "input_schema": {"type": "object"},
        "execution_mode": "sometimes",
    }
    (tmp_path / "list_skills.json").write_text(json.dumps(tool), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"tools": ["list_skills.json"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_builtin_tool_execution_mode"):
        BuiltinToolProvider(manifest_path=tmp_path / "manifest.json")
