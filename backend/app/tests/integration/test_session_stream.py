"""Integration tests: chat runs through the session layer and the SSE endpoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent.llm.provider_adapter import ModelResponse
from backend.app.tests.integration._api_test_support import (
    _build_client,
    _stream_events,
    _stub_provider_adapter,
    _wait_for_session_status,
)


def _slow_provider(client) -> None:
    adapter = client.app.state.container.react_runtime._provider_adapter

    async def slow_complete(*, instructions, messages, tools, runtime_hints=None):
        await asyncio.sleep(5)
        return ModelResponse(text="too late", status="completed", protocol="responses")

    adapter.complete = slow_complete  # type: ignore[method-assign]


def test_dispatch_returns_run_and_stream_ends_on_terminal_control(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client, reply="第一句。第二句。")

    dispatched = client.post("/api/chat/sessions", json={"message": "find Gamma", "page_size": 3}).json()
    session_id, run_id = dispatched["session_id"], dispatched["run_id"]
    assert run_id.startswith("r_")
    detail = _wait_for_session_status(client, session_id, "completed")
    assert detail["current_run"] == {"run_id": run_id, "status": "completed"}

    events = _stream_events(client, session_id, run_id=run_id)
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert {event["run_id"] for event in events} == {run_id}
    assert (events[0]["kind"], events[0]["event"], events[0]["status"]) == ("control", "run.state", "running")
    assert (events[-1]["kind"], events[-1]["event"], events[-1]["status"]) == ("control", "run.state", "completed")

    tokens = [event for event in events if event["event"] == "assistant.token"]
    assert "".join(event["data"]["delta"] for event in tokens) == "第一句。第二句。"
    assert all("content" not in event["data"] for event in tokens)
    completed = next(event for event in events if event["event"] == "assistant.completed")
    assert {event["output_id"] for event in tokens} == {completed["output_id"]}


def test_stream_replays_after_last_event_id_without_duplicates(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    run_id = response["run_id"]
    all_events = _stream_events(client, response["session_id"], run_id=run_id)

    resumed = client.get(
        f"/api/stream/{response['session_id']}",
        params={"run_id": run_id},
        headers={"Last-Event-ID": "3"},
    )
    resumed_ids = [int(line[4:]) for line in resumed.text.splitlines() if line.startswith("id: ")]
    assert resumed_ids == [event["id"] for event in all_events if event["id"] > 3]

    by_query = client.get(f"/api/stream/{response['session_id']}", params={"run_id": run_id, "last_event_id": 3})
    assert by_query.text == resumed.text


def test_evicted_cursor_receives_stream_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_BUFFER_SIZE", "10")
    client = _build_client(tmp_path)
    _stub_provider_adapter(client, reply="这是一段很长的回复。" * 30)
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()

    events = _stream_events(client, response["session_id"], run_id=response["run_id"], after_id=1)
    assert events[0]["event"] == "stream.reset"
    assert events[0]["kind"] == "control"
    head = events[0]["data"]["head_id"]
    assert [event["id"] for event in events[1:]] == list(range(events[0]["id"] + 1, head + 1))
    assert events[-1]["status"] == "completed"


def test_cancel_targets_run_and_stream_reports_cancellation(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _slow_provider(client)
    dispatched = client.post("/api/chat/sessions", json={"session_id": "s_cancel1", "message": "find Gamma"}).json()
    run_id = dispatched["run_id"]

    cancelled = client.post(f"/api/chat/sessions/s_cancel1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    detail = cancelled.json()
    assert detail["status"] == "failed"
    assert detail["current_run"] == {"run_id": run_id, "status": "cancelled"}

    events = _stream_events(client, "s_cancel1", run_id=run_id)
    assert [event.get("status") for event in events if event["kind"] == "control"] == [
        "running",
        "cancelling",
        "cancelled",
    ]
    assert events[-2]["event"] == "session.failed"
    assert "上下文" in events[-2]["data"]["error"]

    # Repeating the cancel is harmless and keeps the terminal state.
    again = client.post(f"/api/chat/sessions/s_cancel1/runs/{run_id}/cancel")
    assert again.status_code == 200
    assert again.json()["current_run"]["status"] == "cancelled"


def test_cancel_of_stale_run_does_not_touch_the_new_run(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)
    first = client.post("/api/chat", json={"session_id": "s_stale1", "message": "find Gamma"}).json()

    _slow_provider(client)
    second = client.post("/api/chat/sessions", json={"session_id": "s_stale1", "message": "again"}).json()

    stale = client.post(f"/api/chat/sessions/s_stale1/runs/{first['run_id']}/cancel")
    assert stale.status_code == 409
    detail = client.get("/api/chat/sessions/s_stale1").json()
    assert detail["status"] == "running"
    assert detail["current_run"] == {"run_id": second["run_id"], "status": "running"}

    delete_running = client.delete("/api/chat/sessions/s_stale1")
    assert delete_running.status_code == 409

    unknown = client.post("/api/chat/sessions/s_stale1/runs/r_unknown/cancel")
    assert unknown.status_code == 409
    assert client.post(f"/api/chat/sessions/s_stale1/runs/{second['run_id']}/cancel").status_code == 200

    assert client.delete("/api/chat/sessions/s_stale1").status_code == 204
    assert client.app.state.container.run_manager.current("s_stale1") is None
    assert client.get("/api/stream/s_stale1", params={"run_id": second["run_id"]}).status_code == 404


def test_cancel_and_stream_require_run_id(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _slow_provider(client)
    dispatched = client.post("/api/chat/sessions", json={"session_id": "s_norun1", "message": "find Gamma"}).json()

    # The old route without a run id no longer exists.
    assert client.post("/api/chat/sessions/s_norun1/cancel").status_code == 404
    assert client.get("/api/stream/s_norun1").status_code == 422
    # The run was not touched by the rejected requests.
    detail = client.get("/api/chat/sessions/s_norun1").json()
    assert detail["current_run"] == {"run_id": dispatched["run_id"], "status": "running"}
    assert client.post(f"/api/chat/sessions/s_norun1/runs/{dispatched['run_id']}/cancel").status_code == 200


def test_stream_for_unknown_run_is_not_found(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    assert client.get("/api/stream/s_never_ran", params={"run_id": "r_missing"}).status_code == 404
