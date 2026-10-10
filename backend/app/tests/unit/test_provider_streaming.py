"""Provider streaming: SSE chunk parsing, tool-call assembly, truncation and cancellation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.agent.llm.llm_config import LLMConfig
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.llm.streaming import StreamDone, TextDelta, ToolCallDelta

TOOLS = [{"type": "function", "function": {"name": "db_query_tool", "parameters": {"type": "object"}}}]


def make_adapter(protocol="chat_completions", **overrides):
    config = LLMConfig(
        api_key="synthetic-test-key", base_url="https://fixture.invalid/v1", model="fixture-model",
        timeout_seconds=1, temperature=0.2, max_tokens=128, api_mode=protocol, stream=True,
    )
    return ProviderAdapter(replace(config, **overrides))


REAL_CLIENT = httpx.AsyncClient


def install(monkeypatch, handler):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def sse(*items):
    out = []
    for item in items:
        out.append(item if isinstance(item, str) else "data: " + json.dumps(item, ensure_ascii=False))
    return ("\n\n".join(out) + "\n\n").encode()


def respond(body: bytes, chunk=7):
    async def gen():
        for i in range(0, len(body), chunk):
            yield body[i:i + chunk]
    return lambda request: httpx.Response(200, content=gen())


def delta(**fields):
    return {"id": "c1", "model": "m", "choices": [{"delta": fields, "finish_reason": None}]}


def finish(reason="stop"):
    return {"id": "c1", "choices": [{"delta": {}, "finish_reason": reason}]}


async def collect(adapter, tools=()):
    events = []
    async for event in adapter.stream(instructions="sys", messages=[{"role": "user", "content": "hi"}], tools=list(tools)):
        events.append(event)
    return events


def test_text_across_chunks_matches_non_stream(monkeypatch):
    body = sse(delta(content="你"), delta(content="好，"), delta(content="世界"), finish(),
               {"id": "c1", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}, "data: [DONE]")
    install(monkeypatch, respond(body))
    adapter = make_adapter()
    events = asyncio.run(collect(adapter))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["你", "好，", "世界"]
    final = events[-1]
    assert isinstance(final, StreamDone)
    response = final.response
    assert (response.text, response.status, response.stream_mode) == ("你好，世界", "completed", "provider")
    assert response.usage["total_tokens"] == 5 and response.provider_ttft_ms is not None
    assert response.transcript["chat_message"] == {"role": "assistant", "content": "你好，世界"}

    # complete() with stream on returns the same shape as the non-stream path.
    complete = asyncio.run(adapter.complete(instructions="sys", messages=[{"role": "user", "content": "hi"}], tools=[]))
    assert complete.text == response.text and complete.stream_mode == "provider" and complete.protocol == "chat_completions"

    def non_stream(request):
        return httpx.Response(200, json={"id": "c1", "model": "m", "usage": {"total_tokens": 5},
                                         "choices": [{"message": {"role": "assistant", "content": "你好，世界"}, "finish_reason": "stop"}]})
    install(monkeypatch, non_stream)
    plain = asyncio.run(make_adapter(stream=False).complete(instructions="sys", messages=[{"role": "user", "content": "hi"}], tools=[]))
    assert plain.text == complete.text and plain.transcript == complete.transcript and plain.stream_mode == "synthetic"


def test_tool_arguments_split_and_interleaved(monkeypatch):
    def call(index, **fn):
        return delta(tool_calls=[{"index": index, "function": fn}])
    first = delta(tool_calls=[{"index": 0, "id": "call-a", "type": "function", "function": {"name": "db_query_tool", "arguments": ""}}])
    second = delta(tool_calls=[{"index": 1, "id": "call-b", "type": "function", "function": {"name": "db_query_tool", "arguments": ""}}])
    body = sse(first, call(0, arguments='{"city'), second, call(1, arguments='{"n": 1'), call(0, arguments='":"上海"}'),
               call(1, arguments="}"), finish("tool_calls"), "data: [DONE]")
    install(monkeypatch, respond(body))
    events = asyncio.run(collect(make_adapter(), TOOLS))
    assert all(isinstance(e, (ToolCallDelta, StreamDone)) for e in events)
    # Nothing is parsed while fragments are still arriving: only the final event carries tool calls.
    assert all(not hasattr(e, "response") for e in events[:-1])
    response = events[-1].response
    assert [(c.call_id, c.arguments, c.parse_error) for c in response.tool_calls] == [
        ("call-a", {"city": "上海"}, None), ("call-b", {"n": 1}, None)]
    assert response.transcript["chat_message"]["tool_calls"][0]["function"]["arguments"] == '{"city":"上海"}'


def test_stream_without_completion_marks_incomplete_and_keeps_text(monkeypatch):
    install(monkeypatch, respond(sse(delta(content="部分"), delta(content="回复"))))
    response = asyncio.run(collect(make_adapter()))[-1].response
    assert response.status == "incomplete" and response.text == "部分回复"
    assert response.error["type"] == "stream_incomplete"


def test_finish_reason_without_done_is_accepted(monkeypatch):
    install(monkeypatch, respond(sse(delta(content="ok"), finish())))
    response = asyncio.run(collect(make_adapter()))[-1].response
    assert response.status == "completed" and response.text == "ok" and response.usage["total_tokens"] is None


def test_invalid_lines_skipped_and_error_event_ends_with_error(monkeypatch):
    body = sse(delta(content="a"), "data: {not json", ": keep-alive", "event: ping", delta(content="b"),
               {"error": {"code": "overloaded", "message": "secret prompt echo"}}, delta(content="never"))
    install(monkeypatch, respond(body))
    response = asyncio.run(collect(make_adapter()))[-1].response
    assert response.text == "ab" and response.status == "incomplete"
    assert response.error["type"] == "stream_error" and "secret" not in response.error["message"]


def test_http_error_and_transport_error_become_error_response(monkeypatch):
    install(monkeypatch, lambda request: httpx.Response(429, content=b"slow down"))
    response = asyncio.run(collect(make_adapter()))[-1].response
    assert response.status == "failed" and response.error["message"] == "http_error status=429"

    def boom(request):
        raise httpx.ConnectError("no route")
    install(monkeypatch, boom)
    response = asyncio.run(collect(make_adapter()))[-1].response
    assert response.status == "failed" and response.error["type"] == "url_error"


def test_required_tool_choice_without_calls_fails(monkeypatch):
    install(monkeypatch, respond(sse(delta(content="text"), finish(), "data: [DONE]")))
    response = asyncio.run(collect(make_adapter(tool_choice="required"), TOOLS))[-1].response
    assert response.status == "failed" and "required" in response.error["message"]


def test_responses_api_event_sequence(monkeypatch):
    item = {"type": "function_call", "call_id": "call-1", "name": "db_query_tool", "arguments": ""}
    done_item = {**item, "arguments": '{"a":1}'}
    message = {"type": "message", "content": [{"type": "output_text", "text": "答案"}]}
    completed = {"id": "resp-1", "model": "rm", "status": "completed",
                 "output": [done_item, message], "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}
    body = sse(
        {"type": "response.created", "response": {"id": "resp-1"}},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"a"'},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": ":1}"},
        {"type": "response.output_item.done", "output_index": 0, "item": done_item},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "答"},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "案"},
        {"type": "response.completed", "response": completed},
    )
    install(monkeypatch, respond(body))
    events = asyncio.run(collect(make_adapter("responses"), TOOLS))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["答", "案"]
    response = events[-1].response
    assert response.text == "答案" and response.response_id == "resp-1" and response.status == "completed"
    assert [(c.call_id, c.arguments) for c in response.tool_calls] == [("call-1", {"a": 1})]
    assert response.transcript["responses_output"] == [done_item, message]
    assert response.usage["total_tokens"] == 6 and response.protocol == "responses"


def test_responses_failed_event_and_truncation(monkeypatch):
    install(monkeypatch, respond(sse({"type": "response.output_text.delta", "delta": "半截"})))
    response = asyncio.run(collect(make_adapter("responses")))[-1].response
    assert response.status == "incomplete" and response.text == "半截"

    failed = {"type": "response.failed", "response": {"id": "r", "status": "failed", "error": {"code": "x"}, "output": []}}
    install(monkeypatch, respond(sse(failed)))
    response = asyncio.run(collect(make_adapter("responses")))[-1].response
    assert response.status == "failed" and response.error is not None


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
def test_cancelling_consumer_closes_upstream(monkeypatch, protocol):
    state = {"closed": False}
    first = sse(delta(content="x") if protocol == "chat_completions" else {"type": "response.output_text.delta", "delta": "x"})

    async def gated():
        try:
            yield first
            await asyncio.Event().wait()  # upstream never sends more
        finally:
            state["closed"] = True

    install(monkeypatch, lambda request: httpx.Response(200, content=gated()))

    async def run():
        got_first = asyncio.Event()

        async def consume():
            async for event in make_adapter(protocol).stream(instructions="s", messages=[], tools=[]):
                if isinstance(event, TextDelta):
                    got_first.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(got_first.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert state["closed"] is True


def test_extra_parameters_cannot_override_stream():
    adapter = make_adapter(extra_parameters={"stream": False})
    events = asyncio.run(collect(adapter))
    assert events[-1].response.error["message"] == "extra_parameters cannot override protocol or tool controls"
    for protocol, builder in (("chat", adapter._build_chat_request), ("responses", adapter._build_responses_request)):
        clean = make_adapter()
        build = clean._build_chat_request if protocol == "chat" else clean._build_responses_request
        _, payload = build(instructions="s", messages=[], tools=[], tool_choice="none", stream=True)
        assert payload["stream"] is True
    _, chat = make_adapter()._build_chat_request(instructions="s", messages=[], tools=[], tool_choice="none", stream=True)
    assert chat["stream_options"] == {"include_usage": True}
    _, off = make_adapter(stream=False)._build_chat_request(instructions="s", messages=[], tools=[], tool_choice="none")
    assert off["stream"] is False and "stream_options" not in off


def test_responses_truncated_after_empty_message_item_keeps_text(monkeypatch):
    empty_message = {"type": "message", "id": "msg_1", "role": "assistant", "status": "in_progress", "content": []}
    body = sse(
        {"type": "response.created", "response": {"id": "resp-1"}},
        {"type": "response.output_item.added", "output_index": 0, "item": empty_message},
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "已收到"},
        {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "的文字"},
    )
    install(monkeypatch, respond(body))
    response = asyncio.run(collect(make_adapter("responses")))[-1].response
    assert response.text == "已收到的文字"
    assert response.status == "incomplete" and response.error["type"] == "stream_incomplete"
    assert response.transcript["responses_output"][0]["content"] == [{"type": "output_text", "text": "已收到的文字"}]
