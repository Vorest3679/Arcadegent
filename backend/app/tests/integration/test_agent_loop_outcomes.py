"""Unified agent loop (plan 4, stage 5): outcome semantics shared by main and workers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.artifacts.sink import ArtifactSink
from app.agent.runtime.budget import RunBudget
from app.session.events import RunEvent
from backend.app.tests.integration._runtime_kernel_test_support import (
    _Provider,
    _Store,
    _call,
    _run,
    _runtime,
    _text,
)


def _batch(*names: str) -> ModelResponse:
    return ModelResponse(
        text=None,
        status="completed",
        protocol="chat_completions",
        reported_model="stub",
        tool_calls=[ModelToolCall(call_id=f"call_{i}_{name}", name=name, arguments={}) for i, name in enumerate(names)],
    )


def _calls(*calls: tuple[str, dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        text=None,
        status="completed",
        protocol="chat_completions",
        reported_model="stub",
        tool_calls=[
            ModelToolCall(call_id=f"call_{i}_{name}", name=name, arguments=arguments)
            for i, (name, arguments) in enumerate(calls)
        ],
    )


class _SnapshotProvider(_Provider):
    """Keep the memory snapshot each model call actually received."""

    def __init__(self, script: list[tuple[str, ModelResponse]]) -> None:
        super().__init__(script)
        self.memories: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> ModelResponse:
        response = await super().complete(**kwargs)
        self.memories.append(deepcopy(kwargs["runtime_hints"]["memory"]))
        return response


def _worker_run(store: _Store) -> dict[str, Any]:
    return store.states["s_kernel"].working_memory["worker_runs"][-1]


def test_blank_main_text_is_not_a_reply_and_the_loop_continues(tmp_path: Path) -> None:
    provider = _Provider([("main_agent", _text("   ")), ("main_agent", _text("real answer"))])
    result, _ = _run(_runtime(tmp_path, provider, _Store()), "go")

    assert result.text == "real answer"
    assert result.reply_source == "model"
    assert result.status == "completed"
    assert provider.calls == ["main_agent", "main_agent"]


def test_only_blank_text_ends_as_step_limit_with_fallback(tmp_path: Path) -> None:
    provider = _Provider([("main_agent", _text(" "))] * 4)
    result, _ = _run(_runtime(tmp_path, provider, _Store()), "go")

    assert result.status == "failed"
    assert result.model_error == {"type": "step_limit", "message": "agent exhausted its step budget"}
    assert result.reply_source == "fallback"


def test_blank_concluding_reply_does_not_end_the_turn(tmp_path: Path) -> None:
    provider = _Provider([
        ("main_agent", _call("finish", reply="  ")),
        ("main_agent", _call("finish", reply="done")),
    ])
    result, _ = _run(_runtime(tmp_path, provider, _Store()), "go")

    assert result.text == "done"
    assert provider.calls == ["main_agent", "main_agent"]


def test_failure_then_success_in_one_batch_keeps_the_failure(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _batch("boom", "echo")),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    _run(_runtime(tmp_path, provider, store), "go")

    run = _worker_run(store)
    assert run["status"] == "failed"
    assert "boom" in run["error"]


def test_success_in_a_later_batch_still_recovers_the_worker(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _batch("boom")),
        ("helper_worker", _batch("echo")),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    _run(_runtime(tmp_path, provider, store), "go")

    assert _worker_run(store)["status"] == "completed"


def test_successful_spawn_does_not_clear_a_sibling_batch_failure(tmp_path: Path) -> None:
    store = _Store()
    provider = _SnapshotProvider([
        ("main_agent", _calls(("boom", {}), ("spawn", {"worker": "helper_worker", "task": "do it"}))),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    _run(_runtime(tmp_path, provider, store), "go")

    # The spawn succeeds, but the earlier failure in the same batch is still
    # unresolved, so the next main-agent call must see it.
    assert provider.memories[-1]["last_error"] == {"message": "boom"}
    assert store.states["s_kernel"].working_memory["last_error"] == {"message": "boom"}


def test_worker_step_limit_fails_the_worker(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        *[("helper_worker", _call("echo", n=n)) for n in range(4)],
        ("main_agent", _text("main done")),
    ])
    result, events = _run(_runtime(tmp_path, provider, store), "go")

    assert result.text == "main done"
    run = _worker_run(store)
    assert run["status"] == "failed"
    assert run["error"] == "worker exhausted its step budget"
    assert RunEvent.WORKER_FAILED in events.names()


def test_blank_worker_text_does_not_complete_the_worker(tmp_path: Path) -> None:
    store = _Store()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _text("")),
        ("helper_worker", _text("worker text")),
        ("main_agent", _text("main done")),
    ])
    _run(_runtime(tmp_path, provider, store), "go")

    run = _worker_run(store)
    assert run["status"] == "completed"
    assert run["summary"] == "worker text"


class _PromotionRecorder(ArtifactSink):
    """Record the status the worker runner hands to artifact promotion."""

    def __init__(self) -> None:
        self.statuses: list[str] = []

    def promote(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        self.statuses.append(kwargs["status"])
        return super().promote(**kwargs)


@pytest.mark.parametrize(("worker_step", "expected"), [(_batch("boom"), True), (_text("fine"), False)])
def test_promotion_is_flagged_partial_only_for_failed_workers(
    tmp_path: Path, worker_step: ModelResponse, expected: bool
) -> None:
    recorder = _PromotionRecorder()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", worker_step),
        *([("helper_worker", _text("gave up"))] if expected else []),
        ("main_agent", _text("main done")),
    ])
    kernel = _runtime(tmp_path, provider, _Store())
    kernel._artifacts = recorder
    _run(kernel, "go")

    assert recorder.statuses == ["failed" if expected else "completed"]


def test_concluding_tool_does_not_hide_a_same_batch_failure(tmp_path: Path) -> None:
    store = _Store()
    recorder = _PromotionRecorder()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _calls(("boom", {}), ("finish", {"reply": "looks done"}))),
        ("main_agent", _text("main done")),
    ])
    kernel = _runtime(tmp_path, provider, store)
    kernel._artifacts = recorder
    _run(kernel, "go")

    run = _worker_run(store)
    assert run["status"] == "failed"
    assert "boom" in run["error"]
    assert recorder.statuses == ["failed"]


def test_concluding_tool_after_a_later_success_recovers_the_worker(tmp_path: Path) -> None:
    store = _Store()
    recorder = _PromotionRecorder()
    provider = _Provider([
        ("main_agent", _call("spawn", worker="helper_worker", task="do it")),
        ("helper_worker", _batch("boom")),
        ("helper_worker", _call("finish", reply="recovered")),
        ("main_agent", _text("main done")),
    ])
    kernel = _runtime(tmp_path, provider, store)
    kernel._artifacts = recorder
    _run(kernel, "go")

    assert _worker_run(store)["status"] == "completed"
    assert recorder.statuses == ["completed"]


def test_workers_draw_from_the_turn_budget(tmp_path: Path) -> None:
    store = _Store()
    script: list[tuple[str, ModelResponse]] = []
    for _ in range(3):
        script.append(("main_agent", _call("spawn", worker="helper_worker", task="do it")))
        script.extend(("helper_worker", _call("echo", n=n)) for n in range(4))
    # Pool of 16 calls is spent after this step; the fourth worker gets none.
    script.append(("main_agent", _call("spawn", worker="helper_worker", task="do it")))
    provider = _Provider(script)
    result, _ = _run(_runtime(tmp_path, provider, store), "go")

    assert len(provider.calls) == 16
    assert result.model_error == {"type": "step_limit", "message": "agent exhausted its step budget"}
    assert _worker_run(store)["status"] == "failed"


def test_child_budget_has_its_own_cap_and_shares_the_pool() -> None:
    root = RunBudget(3, total_steps=5)
    child = root.child()
    assert [root.next(), root.next()] == [1, 2]
    assert [child.next(), child.next(), child.next()] == [1, 2, 3]
    assert child.exhausted
    assert root.exhausted  # pool of 5 is spent
    with pytest.raises(RuntimeError):
        root.next()


def test_artifact_promotion_marks_partial_artifacts_in_meta() -> None:
    from app.agent.runtime.session_state import ensure_working_memory_shape, set_working_memory_artifact

    worker = ensure_working_memory_shape({})
    set_working_memory_artifact(worker, "shops", [{"id": 1}], turn_index=1, display=True, scope="session")
    for status, expected in (("failed", "partial"), ("completed", None)):
        parent = ensure_working_memory_shape({})
        promoted = ArtifactSink().promote(
            child_memory=worker, parent_memory=parent, turn_index=1, status=status
        )
        assert "shops" in promoted
        assert parent["artifact_meta"]["shops"].get("status") == expected
