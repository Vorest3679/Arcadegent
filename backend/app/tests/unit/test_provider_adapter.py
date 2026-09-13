"""Unit tests for provider adapter payload and message normalization."""

from __future__ import annotations

import asyncio

from app.agent.llm.llm_config import LLMConfig
from app.agent.llm.provider_adapter import ModelToolCall, ProviderAdapter


def _run(awaitable):
    return asyncio.run(awaitable)


def _adapter(
    *,
    base_url: str = "https://api.example.com/v1",
    model: str = "test-model",
    api_key: str = "test-key",
    **overrides,
) -> ProviderAdapter:
    return ProviderAdapter(
        LLMConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=10.0,
            temperature=0.2,
            max_tokens=256,
            **overrides,
        )
    )


def test_chat_normalization_pairs_native_tool_calls_with_tool_messages() -> None:
    adapter = _adapter()
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "chat_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "db_query_tool", "arguments": "{}"}}
                ],
            },
        },
        {"role": "tool", "name": "db_query_tool", "content": '{"shops": []}', "tool_call_id": "call_1"},
    ]

    normalized = adapter._normalize_chat_messages(messages)

    assert normalized == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "db_query_tool", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "content": '{"shops": []}', "tool_call_id": "call_1"},
    ]


def test_chat_normalization_marks_unpaired_tool_observation_as_legacy_note() -> None:
    adapter = _adapter()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "name": "mcp__amap__maps_geo", "content": '{"shops": []}', "tool_call_id": "call_1"},
        {"role": "assistant", "content": "done"},
    ]

    normalized = adapter._normalize_chat_messages(messages)

    assert normalized == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": '[Legacy tool observation] {"shops": []}'},
        {"role": "assistant", "content": "done"},
    ]


def test_responses_normalization_emits_function_call_output_pairs() -> None:
    adapter = _adapter()
    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "responses_output": [
                {"type": "function_call", "call_id": "call_1", "name": "db_query_tool", "arguments": "{}"}
            ],
        },
        {"role": "tool", "name": "db_query_tool", "content": '{"shops": []}', "tool_call_id": "call_1"},
    ]

    normalized = adapter._normalize_responses_messages(messages)

    assert normalized == [
        {"role": "user", "content": "hello"},
        {"type": "function_call", "call_id": "call_1", "name": "db_query_tool", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"shops": []}'},
    ]


def test_build_chat_payload_without_tools_omits_tool_controls() -> None:
    adapter = _adapter()
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert payload["stream"] is False


def test_build_chat_payload_has_no_hardcoded_provider_quirks() -> None:
    adapter = _adapter(base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert "thinking" not in payload
    assert "stop" not in payload
    assert "logprobs" not in payload
    assert payload["stream"] is False


def test_extra_parameters_configure_provider_specific_fields() -> None:
    adapter = _adapter(extra_parameters={"thinking": {"type": "disabled"}})
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert payload["thinking"] == {"type": "disabled"}


def test_extra_parameters_cannot_override_protocol_controls() -> None:
    adapter = _adapter(extra_parameters={"tool_choice": "none"})
    response = _run(
        adapter.complete(
            instructions="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "summary_tool"}}],
        )
    )

    assert response.text is None
    assert response.status == "failed"
    assert response.error is not None
    assert "extra_parameters" in response.error["message"]


def test_token_limit_parameter_is_configurable() -> None:
    adapter = _adapter(token_limit_parameter="max_completion_tokens")
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 256


def test_send_temperature_false_omits_temperature() -> None:
    adapter = _adapter(send_temperature=False)
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert "temperature" not in payload


def test_disabled_provider_returns_structured_error_not_error_text() -> None:
    adapter = _adapter(api_key=" ")
    response = _run(
        adapter.complete(
            instructions="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
    )

    assert response.text is None
    assert response.tool_calls == []
    assert response.status == "failed"
    assert response.error is not None
    assert response.error["type"] == "provider_error"
    assert response.duration_ms is not None
    assert response.protocol in {"responses", "chat_completions"}


def test_parse_arguments_keeps_raw_value_and_parse_error() -> None:
    adapter = _adapter()

    args, error = adapter._parse_arguments('{"city": ')
    assert args == {}
    assert error is not None

    args, error = adapter._parse_arguments('["not-an-object"]')
    assert args == {}
    assert error is not None

    args, error = adapter._parse_arguments('{"city": "shanghai"}')
    assert args == {"city": "shanghai"}
    assert error is None


def test_tool_call_preserves_raw_arguments_and_parse_error() -> None:
    adapter = _adapter()
    call = adapter._parse_chat_tool_call(
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "db_query_tool", "arguments": '{"city": '},
        }
    )

    assert isinstance(call, ModelToolCall)
    assert call.arguments == {}
    assert call.raw_arguments == '{"city": '
    assert call.parse_error is not None


def test_deepseek_prefers_chat_completions() -> None:
    adapter = _adapter(base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    assert adapter._prefer_chat_completions() is True


def test_api_mode_overrides_protocol_detection() -> None:
    adapter = _adapter(base_url="https://api.deepseek.com/v1", model="deepseek-chat", api_mode="responses")
    assert adapter._prefer_chat_completions() is False


def test_unsupported_tool_choice_is_an_error_not_silent_rewrite() -> None:
    adapter = _adapter(tool_choice="none", supported_tool_choices=("auto", "required"))
    response = _run(
        adapter.complete(
            instructions="system",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "summary_tool"}}],
        )
    )

    assert response.status == "failed"
    assert response.error is not None
    assert "tool_choice" in response.error["message"]


def test_runtime_hints_no_longer_force_special_tool_choice() -> None:
    adapter = _adapter()
    choice = adapter._resolve_tool_choice(
        tools=[{"type": "function", "function": {"name": "summary_tool"}}],
        runtime_hints={"active_subagent": "intent_router"},
    )
    assert choice == "auto"
