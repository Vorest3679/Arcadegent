"""Integration tests: the session detail returns the recoverable process of each round."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from backend.app.tests.integration._api_test_support import _build_client, _wait_for_session_status

INTERMEDIATE = "我先帮你查一下。"
FINAL = "找到了 Gamma Arcade。"


def _two_step_provider(client, *, worker_text: str = "worker 内部总结，不应展示") -> None:
    """Main agent: text + invoke_worker, then the final reply; worker: db_query_tool, then text."""
    adapter = client.app.state.container.react_runtime._provider_adapter
    main_calls = 0
    worker_calls = 0

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        nonlocal main_calls, worker_calls
        if runtime_hints["active_subagent"] == "main_agent":
            main_calls += 1
            if main_calls == 1:
                return ModelResponse(
                    text=INTERMEDIATE,
                    tool_calls=[ModelToolCall(
                        "dispatch", "invoke_worker", {"worker": "search_worker", "task": "find Gamma"},
                    )],
                )
            return ModelResponse(text=FINAL)
        worker_calls += 1
        if worker_calls == 1:
            return ModelResponse(
                text="worker 中间文字，不应展示",
                tool_calls=[ModelToolCall(
                    "query", "db_query_tool", {"shop_name": "Gamma Arcade", "page": 1, "page_size": 3},
                )],
            )
        return ModelResponse(text=worker_text)

    adapter.complete = fake_complete  # type: ignore[method-assign]


def _run_round(client, session_id: str, message: str) -> dict:
    dispatched = client.post(
        "/api/chat/sessions", json={"session_id": session_id, "message": message, "page_size": 3},
    )
    assert dispatched.status_code == 202
    return _wait_for_session_status(client, session_id, "completed")


def test_detail_returns_intermediate_text_and_tool_steps_on_the_user_turn(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _two_step_provider(client)

    detail = _run_round(client, "s_steps1", "find Gamma")

    assert [turn["role"] for turn in detail["turns"]] == ["user", "assistant"]
    assert detail["turn_count"] == 2
    user, assistant = detail["turns"]
    assert assistant["content"] == FINAL
    assert assistant.get("steps", []) == []

    steps = user["steps"]
    texts = [step for step in steps if step["kind"] == "text"]
    tools = [step for step in steps if step["kind"] == "tool"]
    # Only the main agent's intermediate text; the final reply and worker text are not steps.
    assert [(step["content"], step["agent"]) for step in texts] == [(INTERMEDIATE, "main_agent")]
    assert steps[0]["kind"] == "text"
    assert {(step["name"], step["agent"], step["status"]) for step in tools} == {
        ("invoke_worker", "main_agent", "completed"),
        ("db_query_tool", "search_worker", "completed"),
    }
    assert all(step["created_at"] for step in steps)


def test_steps_do_not_leak_model_evidence_or_tool_payloads(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _two_step_provider(client)

    detail = _run_round(client, "s_steps2", "find Gamma")

    allowed = {
        "text": {"kind", "agent", "content", "created_at"},
        "tool": {"kind", "call_id", "name", "agent", "status", "created_at"},
    }
    for step in detail["turns"][0]["steps"]:
        assert set(step) <= allowed[step["kind"]], step
    raw = json.dumps(detail, ensure_ascii=False)
    for forbidden in ("transcript", "raw_arguments", "raw_usage", "argument_evidence", "worker 中间文字"):
        assert forbidden not in raw


def test_steps_are_attached_to_the_round_that_produced_them(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _two_step_provider(client)
    _run_round(client, "s_steps3", "find Gamma")
    _two_step_provider(client)  # reset call counters for the second round
    detail = _run_round(client, "s_steps3", "再查一次")

    users = [turn for turn in detail["turns"] if turn["role"] == "user"]
    assert len(users) == 2
    for user in users:
        assert [step["content"] for step in user["steps"] if step["kind"] == "text"] == [INTERMEDIATE]
        tool_names = [step["name"] for step in user["steps"] if step["kind"] == "tool"]
        assert tool_names.count("invoke_worker") == 1


def test_round_without_tools_has_no_steps(tmp_path: Path) -> None:
    from backend.app.tests.integration._api_test_support import _stub_provider_adapter

    client = _build_client(tmp_path)
    _stub_provider_adapter(client, reply="直接回答。")

    detail = _run_round(client, "s_steps4", "你好")

    assert detail["turns"][0].get("steps", []) == []
