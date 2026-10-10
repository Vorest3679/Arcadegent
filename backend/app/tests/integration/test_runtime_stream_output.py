"""Integration tests: main-agent model text is published live per output_id (plan 3B)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.llm.streaming import StreamDone, TextDelta
from backend.app.tests.integration._api_test_support import (
    _build_client,
    _stream_events,
    _wait_for_session_status,
)


def _adapter(client):
    return client.app.state.container.react_runtime._provider_adapter


def _enable_stream(client, stream: bool = True) -> None:
    adapter = _adapter(client)
    adapter._config = replace(adapter._config, stream=stream)


def _done(text=None, tool_calls=()):
    return StreamDone(ModelResponse(text=text, tool_calls=list(tool_calls), status="completed",
                                    stream_mode="provider", protocol="chat_completions"))


async def _wait_until(flag: threading.Event) -> None:
    while not flag.is_set():
        await asyncio.sleep(0.01)


@contextmanager
def _live_server(app):
    """Serve the app over a real socket so SSE frames are observed as they are flushed."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


def test_sse_client_receives_first_delta_before_provider_finishes(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _enable_stream(client)
    gate = threading.Event()

    async def gated_stream(**kwargs):
        yield TextDelta("第一段，")
        await _wait_until(gate)
        yield TextDelta("第二段。")
        yield _done("第一段，第二段。")

    _adapter(client).stream = gated_stream  # type: ignore[method-assign]

    with _live_server(client.app) as base, httpx.Client(base_url=base, timeout=5) as http:
        dispatched = http.post("/api/chat/sessions", json={"message": "find Gamma"}).json()
        session_id, run_id = dispatched["session_id"], dispatched["run_id"]
        events = []
        with http.stream("GET", f"/api/stream/{session_id}", params={"run_id": run_id}) as response:
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if event["event"] == "assistant.token" and not gate.is_set():
                    # The provider is still blocked: this frame is a real increment.
                    assert event["data"] == {"delta": "第一段，", "active_subagent": "main_agent",
                                             "stream_mode": "provider"}
                    gate.set()
                if event["kind"] == "control" and event.get("status") in {"completed", "failed", "cancelled"}:
                    break
    gate.set()

    assert gate.is_set()
    tokens = [event for event in events if event["event"] == "assistant.token"]
    assert [event["data"]["delta"] for event in tokens] == ["第一段，", "第二段。"]
    completed = next(event for event in events if event["event"] == "assistant.completed")
    assert completed["data"]["reply"] == "第一段，第二段。"
    assert {event["output_id"] for event in tokens} == {completed["output_id"]}
    assert events[-1]["status"] == "completed"


@pytest.mark.parametrize("stream", [True, False])
def test_each_main_model_call_is_its_own_output_and_worker_text_stays_private(tmp_path: Path, stream: bool) -> None:
    client = _build_client(tmp_path)
    _enable_stream(client, stream)
    dispatch = ModelToolCall("dispatch", "invoke_worker", {"worker": "search_worker", "task": "find Gamma"})

    def main_turn(messages):
        if any(message.get("role") == "tool" for message in messages):
            return ["找到了 ", "Gamma。"], []
        return ["我先", "查一下。"], [dispatch]

    async def fake_stream(*, messages, **kwargs):
        assert kwargs["runtime_hints"]["active_subagent"] == "main_agent"
        parts, calls = main_turn(messages)
        for part in parts:
            yield TextDelta(part)
        yield _done("".join(parts), calls)

    async def fake_complete(*, messages, runtime_hints=None, **kwargs):
        if runtime_hints["active_subagent"] != "main_agent":
            return ModelResponse(text="WORKER_PRIVATE_TEXT", status="completed")
        parts, calls = main_turn(messages)
        return ModelResponse(text="".join(parts), tool_calls=calls, status="completed")

    adapter = _adapter(client)
    adapter.stream = fake_stream  # type: ignore[method-assign]
    adapter.complete = fake_complete  # type: ignore[method-assign]

    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = _stream_events(client, response["session_id"], run_id=response["run_id"])

    names = [event["event"] for event in events]
    tokens = [event for event in events if event["event"] == "assistant.token"]
    outputs = list(dict.fromkeys(event["output_id"] for event in tokens))
    assert len(outputs) == 2
    texts = ["".join(e["data"]["delta"] for e in tokens if e["output_id"] == output) for output in outputs]
    assert texts == ["我先查一下。", "找到了 Gamma。"]
    # The intermediate reply is out before the worker runs and before the second call starts.
    first_output_end = max(i for i, e in enumerate(events) if e.get("output_id") == outputs[0])
    assert first_output_end < names.index("worker.started")
    assert all("WORKER_PRIVATE_TEXT" not in e["data"]["delta"] for e in tokens)
    assert {e["data"]["stream_mode"] for e in tokens} == {"provider" if stream else "synthetic"}
    completed = next(event for event in events if event["event"] == "assistant.completed")
    assert completed["output_id"] == outputs[1] and completed["data"]["reply"] == "找到了 Gamma。"


def test_summary_reply_is_published_as_new_output(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _enable_stream(client)
    summary = ModelToolCall("sum", "summary_tool", {"topic": "search", "keyword": "Gamma", "total": 0, "shops": []})

    async def fake_stream(**kwargs):
        yield TextDelta("整理中")
        yield _done("整理中", [summary])

    _adapter(client).stream = fake_stream  # type: ignore[method-assign]
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = _stream_events(client, response["session_id"], run_id=response["run_id"])
    tokens = [event for event in events if event["event"] == "assistant.token"]
    completed = next(event for event in events if event["event"] == "assistant.completed")
    assert tokens[0]["output_id"] != completed["output_id"]
    assert [e["data"]["delta"] for e in tokens if e["output_id"] == completed["output_id"]] == [completed["data"]["reply"]]


def test_tool_arguments_split_across_provider_chunks_execute_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(tmp_path)
    adapter = _adapter(client)
    adapter._config = replace(adapter._config, stream=True, api_key="synthetic-test-key", api_mode="chat_completions")
    requests = []

    def chunk(**delta):
        return "data: " + json.dumps({"id": "c", "choices": [{"delta": delta, "finish_reason": None}]}, ensure_ascii=False)

    def handle(request):
        requests.append(json.loads(request.content))
        assert requests[-1]["stream"] is True
        if len(requests) == 1:
            lines = [
                chunk(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                                   "function": {"name": "db_query_tool", "arguments": '{"keyword": "Ga'}}]),
                chunk(tool_calls=[{"index": 0, "function": {"arguments": 'mma", "page": 1, '}}]),
                chunk(tool_calls=[{"index": 0, "function": {"arguments": '"page_size": 3}'}}]),
                'data: {"id": "c", "choices": [{"delta": {}, "finish_reason": "tool_calls"}]}',
            ]
        else:
            lines = [chunk(content="found "), chunk(content="Gamma"),
                     'data: {"id": "c", "choices": [{"delta": {}, "finish_reason": "stop"}]}']
        return httpx.Response(200, content=("\n\n".join(lines + ["data: [DONE]"]) + "\n\n").encode())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))

    payload = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3}).json()
    assert payload["reply"] == "found Gamma"
    assert payload["shops"] and payload["shops"][0]["source_id"] == 10
    events = _stream_events(client, payload["session_id"], run_id=payload["run_id"])
    started = [event for event in events if event["event"] == "tool.started"]
    assert [(e["data"]["tool"], e["data"]["call_id"]) for e in started] == [("db_query_tool", "call_1")]
    state = client.app.state.container.session_store.get_session(payload["session_id"])
    evidence = next(turn.payload["argument_evidence"] for turn in state.turns if turn.role == "tool")
    assert evidence["parsed_arguments"] == {"keyword": "Gamma", "page": 1, "page_size": 3}
    model_turn = next(turn.payload["model"] for turn in state.turns if turn.payload.get("model"))
    assert model_turn["stream_mode"] == "provider"


def test_cancel_while_streaming_closes_provider_stream(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _enable_stream(client)
    reached, closed = threading.Event(), threading.Event()

    async def blocked_stream(**kwargs):
        try:
            yield TextDelta("半截")
            reached.set()
            await asyncio.Event().wait()
            yield _done("never")
        finally:
            closed.set()

    _adapter(client).stream = blocked_stream  # type: ignore[method-assign]
    dispatched = client.post("/api/chat/sessions", json={"session_id": "s_stream_cancel", "message": "find Gamma"}).json()
    assert reached.wait(3)

    cancelled = client.post(f"/api/chat/sessions/s_stream_cancel/runs/{dispatched['run_id']}/cancel")
    assert cancelled.status_code == 200
    assert closed.is_set()
    assert cancelled.json()["current_run"]["status"] == "cancelled"

    events = _stream_events(client, "s_stream_cancel", run_id=dispatched["run_id"])
    assert [e["data"]["delta"] for e in events if e["event"] == "assistant.token"] == ["半截"]
    assert not any(e["event"] == "assistant.completed" for e in events)
    assert events[-1]["status"] == "cancelled"
    _wait_for_session_status(client, "s_stream_cancel", "failed")
