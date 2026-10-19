"""Golden behavior baseline for the ReactRuntime kernel refactor (plan 4, stage 0).

Each scenario drives ``/api/chat`` with a scripted provider and snapshots the
run's SSE events, the persisted session (turns + working memory), the HTTP
response and the session detail. Random ids, timestamps and timings are
normalized so that a pure refactor must reproduce the snapshot exactly.

Regenerate only for an intended behavior change:
``UPDATE_RUNTIME_GOLDEN=1 python -m pytest backend/app/tests/integration/test_runtime_kernel_golden.py``
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.llm.streaming import StreamDone, TextDelta
from backend.app.tests.integration._api_test_support import _build_client, _stream_events

GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "runtime_kernel"
UPDATE = os.getenv("UPDATE_RUNTIME_GOLDEN") == "1"

_ID_PATTERNS = (
    (re.compile(r"\bs_[0-9a-f]{12}\b"), "s"),
    (re.compile(r"\bwrk_[0-9a-f]{10}\b"), "wrk"),
    (re.compile(r"\bout_[0-9a-f]{12}\b"), "out"),
    (re.compile(r"\br_[0-9a-f]{12}\b"), "run"),
)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
_VOLATILE_KEYS = {"duration_ms", "provider_ttft_ms", "at", "created_at", "updated_at"}


class _Normalizer:
    """Replace random ids with stable labels in order of first appearance."""

    def __init__(self) -> None:
        self._labels: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def _label(self, raw: str, prefix: str) -> str:
        if raw not in self._labels:
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            self._labels[raw] = f"<{prefix}{self._counts[prefix]}>"
        return self._labels[raw]

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ("<volatile>" if key in _VOLATILE_KEYS and item is not None else self(item))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if isinstance(value, str):
            if _TIMESTAMP.match(value):
                return "<timestamp>"
            for pattern, prefix in _ID_PATTERNS:
                value = pattern.sub(lambda match, p=prefix: self._label(match.group(0), p), value)
        return value


def _adapter(client):
    return client.app.state.container.react_runtime._provider_adapter


def _install(client, script: Callable[[dict[str, Any]], ModelResponse], captured: list[dict[str, Any]]) -> None:
    async def fake_complete(**kwargs):
        captured.append(kwargs)
        return script(kwargs)

    _adapter(client).complete = fake_complete  # type: ignore[method-assign]


def _snapshot(client, payloads: list[dict[str, Any]]) -> dict[str, Any]:
    normalize = _Normalizer()
    session_id = payloads[-1]["session_id"]
    runs = []
    for payload in payloads:
        events = _stream_events(client, payload["session_id"], run_id=payload["run_id"]) if payload is payloads[-1] else []
        runs.append({
            "response": payload,
            "events": [
                {key: event.get(key) for key in ("kind", "event", "status", "output_id", "data") if key in event}
                for event in events
            ],
        })
    state = client.app.state.container.session_store.get_session(session_id)
    detail = client.get(f"/api/chat/sessions/{session_id}").json()
    return normalize({
        "runs": runs,
        "state": {
            "status": state.status,
            "intent": state.intent,
            "active_subagent": state.active_subagent,
            "turn_index": state.turn_index,
            "last_error": state.last_error,
            "turns": [asdict(turn) for turn in state.turns],
            "working_memory": state.working_memory,
        },
        "detail": detail,
    })


def _assert_golden(name: str, snapshot: dict[str, Any]) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    text = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if UPDATE or not path.exists():
        if not UPDATE:
            pytest.fail(f"missing golden {path.name}; run with UPDATE_RUNTIME_GOLDEN=1 on the baseline commit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert text == path.read_text(encoding="utf-8")


def _post(client, message: str, **extra: Any) -> dict[str, Any]:
    response = client.post("/api/chat", json={"client_id": "test-client", "message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _main(kwargs: dict[str, Any]) -> bool:
    return kwargs["runtime_hints"]["active_subagent"] == "main_agent"


def test_golden_direct_reply_two_turns(tmp_path: Path, chat_clock) -> None:
    client = _build_client(tmp_path, chat_clock=chat_clock)
    captured: list[dict[str, Any]] = []
    replies = iter(["第一轮回复：你好。", "第二轮回复：好的。"])
    _install(client, lambda _kw: ModelResponse(text=next(replies), status="completed",
                                               protocol="chat_completions", reported_model="stub"), captured)

    first = _post(client, "你好")
    chat_clock.advance()
    second = _post(client, "继续", session_id=first["session_id"])

    # Diagnosis group D: the previous final reply reaches the model exactly once.
    second_messages = captured[-1]["messages"]
    assert sum(1 for item in second_messages if item.get("content") == "第一轮回复：你好。") == 1
    _assert_golden("direct_reply_two_turns", _snapshot(client, [first, second]))


def test_golden_search_then_select(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("q1", "db_query_tool", {"keyword": "Gamma", "page": 1, "page_size": 3})]),
        ModelResponse(tool_calls=[ModelToolCall("sel1", "result_selection_tool", {"selected_shop_ids": [10]})]),
        ModelResponse(text="找到 Gamma Arcade。", status="completed"),
    ])
    _install(client, lambda _kw: next(steps), [])
    _assert_golden("search_then_select", _snapshot(client, [_post(client, "find Gamma", page_size=3)]))


def test_golden_search_worker_then_summary(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    main_steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("w1", "invoke_worker",
                                                {"worker": "search_worker", "task": "查找 Gamma 机厅"})]),
        ModelResponse(tool_calls=[ModelToolCall("sum1", "summary_tool", {"topic": "search", "keyword": "Gamma"})]),
        ModelResponse(text="should not be reached"),
    ])
    worker_steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("q1", "db_query_tool", {"keyword": "Gamma", "page": 1, "page_size": 3})]),
        ModelResponse(tool_calls=[ModelToolCall("sel1", "result_selection_tool", {"selected_shop_ids": [10]})]),
        ModelResponse(text="worker found Gamma"),
    ])
    _install(client, lambda kw: next(main_steps) if _main(kw) else next(worker_steps), [])
    _assert_golden("search_worker_then_summary", _snapshot(client, [_post(client, "find Gamma", page_size=3)]))


def test_golden_navigation_worker_route(tmp_path: Path) -> None:
    mcp_dir = tmp_path / "navigation_mcp"
    mcp_dir.mkdir()
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    (mcp_dir / "amap.json").write_text(json.dumps({
        "command": sys.executable,
        "args": [str(fixture_server)],
        "route_tool_name": "maps_direction_walking",
    }), encoding="utf-8")
    client = _build_client(tmp_path, mcp_servers_dir=mcp_dir)
    main_steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("w1", "invoke_worker",
                                                {"worker": "navigation_worker", "task": "从起点步行到 Gamma Arcade"})]),
        ModelResponse(text="步行路线已经规划完成。"),
    ])
    worker_steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("dest", "db_query_tool", {"shop_id": 10, "page": 1, "page_size": 1})]),
        ModelResponse(tool_calls=[ModelToolCall("geo", "geo_resolve_tool", {"province_code": "110000000000"})]),
        ModelResponse(tool_calls=[ModelToolCall("route", "route_plan_tool", {
            "provider": "amap", "mode": "walking", "origin": "116.3,39.9", "destination": "116.32,39.905",
        })]),
        ModelResponse(text="route ready"),
    ])
    _install(client, lambda kw: next(main_steps) if _main(kw) else next(worker_steps), [])
    _assert_golden("navigation_worker_route", _snapshot(client, [_post(client, "从起点走到 Gamma Arcade")]))


def test_golden_tool_parse_error_then_recover(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    steps = iter([
        ModelResponse(tool_calls=[ModelToolCall("bad", "db_query_tool", {}, raw_arguments="{",
                                                parse_error="invalid JSON")]),
        ModelResponse(text="参数有误，已恢复。", status="completed"),
    ])
    _install(client, lambda _kw: next(steps), [])
    _assert_golden("tool_parse_error_then_recover", _snapshot(client, [_post(client, "find Gamma")]))


def test_golden_model_error(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _install(client, lambda _kw: ModelResponse(error={"type": "provider_error", "message": "fixture outage"},
                                               status="failed"), [])
    _assert_golden("model_error", _snapshot(client, [_post(client, "find Gamma")]))


def test_golden_step_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    client = _build_client(tmp_path)
    counter = iter(range(100))
    _install(client, lambda _kw: ModelResponse(tool_calls=[ModelToolCall(f"ls{next(counter)}", "list_skills", {})]), [])
    _assert_golden("step_limit", _snapshot(client, [_post(client, "find Gamma")]))


def test_golden_streamed_reply(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    adapter = _adapter(client)
    adapter._config = replace(adapter._config, stream=True)

    async def fake_stream(**_kwargs):
        yield TextDelta("第一段，")
        yield TextDelta("第二段。")
        yield StreamDone(ModelResponse(text="第一段，第二段。", status="completed",
                                       stream_mode="provider", protocol="chat_completions"))

    adapter.stream = fake_stream  # type: ignore[method-assign]
    _assert_golden("streamed_reply", _snapshot(client, [_post(client, "find Gamma")]))
