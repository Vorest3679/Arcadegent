"""Exercise the production HTTP adapter, including the second tool-call request."""

import asyncio
import json

import httpx
import pytest

from app.agent.llm.llm_config import LLMConfig
from app.agent.llm.provider_adapter import ProviderAdapter


def adapter(protocol):
    return ProviderAdapter(LLMConfig(
        api_key="synthetic-test-key", base_url="https://fixture.invalid/v1",
        model="fixture-model", timeout_seconds=1, temperature=0.2,
        max_tokens=128, api_mode=protocol,
    ))


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_native_tool_round_trip_over_http(monkeypatch, protocol):
    requests = []
    call = {"id": "call-1", "type": "function", "function": {
        "name": "db_query_tool", "arguments": '{"city_name":"上海"}',
    }}
    response_call = {"type": "function_call", "call_id": "call-1",
                     "name": "db_query_tool", "arguments": '{"city_name":"上海"}'}

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.headers["authorization"] == "Bearer synthetic-test-key"
        if protocol == "responses":
            assert request.url.path == "/v1/responses"
            body = {"id": f"r{len(requests)}", "model": "reported-model", "status": "completed",
                    "output": [response_call] if len(requests) == 1 else [],
                    "output_text": "找到了合成机厅" if len(requests) == 2 else None}
        else:
            assert request.url.path == "/v1/chat/completions"
            message = ({"role": "assistant", "content": None, "tool_calls": [call],
                        "reasoning_content": "保留工具轮次推理"} if len(requests) == 1
                       else {"role": "assistant", "content": "找到了合成机厅"})
            body = {"id": f"r{len(requests)}", "model": "reported-model", "choices": [
                {"message": message, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}]}
        return httpx.Response(200, json=body)

    install_transport(monkeypatch, handle)
    subject = adapter(protocol)

    async def run():
        history = [{"role": "user", "content": "找上海机厅"}]
        tools = [{"type": "function", "function": {"name": "db_query_tool",
                  "parameters": {"type": "object", "properties": {"city_name": {"type": "string"}}}}}]
        first = await subject.complete(instructions="test", messages=history, tools=tools)
        assert first.error is None
        assert first.tool_calls[0].arguments == {"city_name": "上海"}
        history += [{"role": "assistant", "content": "", **first.transcript},
                    {"role": "tool", "tool_call_id": first.tool_calls[0].call_id,
                     "name": "db_query_tool", "content": '{"shops":[900001]}'}]
        final = await subject.complete(instructions="test", messages=history, tools=tools)
        assert final.error is None
        assert final.text == "找到了合成机厅"
        assert final.reported_model == "reported-model"
        assert final.provider_ttft_ms is None
        assert final.stream_mode == "synthetic"
        assert final.duration_ms >= 0

    asyncio.run(run())
    assert len(requests) == 2
    if protocol == "responses":
        assert requests[1]["input"][-2:] == [response_call, {
            "type": "function_call_output", "call_id": "call-1", "output": '{"shops":[900001]}'}]
        assert "previous_response_id" not in requests[1]
    else:
        assert requests[1]["messages"][-2]["tool_calls"] == [call]
        assert requests[1]["messages"][-2]["reasoning_content"] == "保留工具轮次推理"
        assert requests[1]["messages"][-1] == {
            "role": "tool", "tool_call_id": "call-1", "content": '{"shops":[900001]}'}


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize("failure", ["401", "429", "503", "html", "empty", "array", "timeout"])
def test_provider_failure_is_structured_and_never_switches_protocol(monkeypatch, protocol, failure):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure.isdigit():
            return httpx.Response(int(failure), text="synthetic-test-key must not leak")
        return httpx.Response(200, text={"html": "<html>bad gateway</html>", "empty": "{}", "array": "[]"}[failure])

    install_transport(monkeypatch, handle)
    result = asyncio.run(adapter(protocol).complete(instructions="test", messages=[], tools=[]))
    assert result.status == "failed"
    assert result.text is None
    assert result.error
    assert "synthetic-test-key" not in str(result.error)
    assert len(requests) == 1
    assert result.protocol == protocol


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_truncation_retains_usage_and_cannot_be_success(monkeypatch, protocol):
    if protocol == "responses":
        usage = {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                 "input_tokens_details": {"cached_tokens": 60},
                 "output_tokens_details": {"reasoning_tokens": 20}}
        body = {"status": "incomplete", "output_text": "partial",
                "incomplete_details": {"reason": "max_output_tokens"}, "usage": usage}
    else:
        usage = {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                 "prompt_tokens_details": {"cached_tokens": 60},
                 "completion_tokens_details": {"reasoning_tokens": 20}}
        body = {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}], "usage": usage}
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    result = asyncio.run(adapter(protocol).complete(instructions="test", messages=[], tools=[]))
    assert result.error and result.status == "incomplete"
    assert result.raw_usage == usage
    assert result.usage == {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                            "cached_input_tokens": 60, "reasoning_tokens": 20}
