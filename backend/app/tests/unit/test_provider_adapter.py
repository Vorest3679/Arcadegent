"""Unit tests for provider adapter payload and message normalization."""

from __future__ import annotations

from app.agent.llm.llm_config import LLMConfig
from app.agent.llm.provider_adapter import ProviderAdapter


def _adapter(*, base_url: str = "https://api.example.com/v1", model: str = "test-model") -> ProviderAdapter:
    return ProviderAdapter(
        LLMConfig(
            api_key="test-key",
            base_url=base_url,
            model=model,
            timeout_seconds=10.0,
            temperature=0.2,
            max_tokens=256,
        )
    )


def test_chat_message_normalization_preserves_tool_observations_as_assistant_notes() -> None:
    adapter = _adapter()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "name": "mcp__amap__maps_geo", "content": '{"shops": []}', "tool_call_id": "call_1"},
        {"role": "assistant", "content": "done"},
    ]

    normalized = adapter._normalize_chat_messages(messages)

    assert normalized == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": '[Tool result: mcp__amap__maps_geo] {"shops":[]}'} ,
        {"role": "assistant", "content": "done"},
    ]


def test_build_chat_payload_without_tools_uses_tool_choice_none() -> None:
    adapter = _adapter()
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert payload["tools"] is None
    assert payload["tool_choice"] == "none"
    assert payload["response_format"] == {"type": "text"}


def test_build_chat_payload_for_deepseek_includes_thinking_fields() -> None:
    adapter = _adapter(base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    payload = adapter._build_chat_payload(
        instructions="system",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        tool_choice="none",
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    assert payload["stream_options"] is None
    assert payload["logprobs"] is False


def test_deepseek_prefers_chat_completions() -> None:
    adapter = _adapter(base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    assert adapter._prefer_chat_completions() is True


def test_runtime_hints_no_longer_force_special_tool_choice() -> None:
    adapter = _adapter()
    choice = adapter._resolve_tool_choice(
        tools=[{"type": "function", "function": {"name": "summary_tool"}}],
        runtime_hints={"active_subagent": "intent_router"},
    )
    assert choice == "auto"
