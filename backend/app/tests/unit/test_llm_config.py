"""Unit tests for provider-profile aware LLM config resolution."""

from __future__ import annotations

from pathlib import Path

from app.agent.llm.llm_config import resolve_llm_config
from app.core.config import Settings


def test_llm_config_uses_default_provider_profile_values() -> None:
    settings = Settings(
        llm_api_key="test-key",
        agent_provider_profiles_file=Path("app/agent/nodes/profiles/provider_profiles.yaml"),
        agent_provider_profile="default",
    )

    config = resolve_llm_config(settings)

    assert config.api_key == "test-key"
    assert config.model == "gpt-4o-mini"
    assert config.timeout_seconds == 20.0
    assert config.temperature == 0.2
    assert config.max_tokens == 500
    assert config.tool_choice == "auto"
    assert config.parallel_tool_calls is False
    assert config.prefer_chat_completions is False


def test_llm_config_can_disable_provider_via_profile() -> None:
    settings = Settings(
        llm_api_key="test-key",
        agent_provider_profiles_file=Path("app/agent/nodes/profiles/provider_profiles.yaml"),
        agent_provider_profile="local",
    )

    config = resolve_llm_config(settings)

    assert config.api_key == ""
    assert config.enabled is False
    assert config.timeout_seconds == 3.0


def test_llm_config_prefers_explicit_settings_over_profile_defaults() -> None:
    settings = Settings(
        llm_api_key="test-key",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-chat",
        agent_provider_profiles_file=Path("app/agent/nodes/profiles/provider_profiles.yaml"),
        agent_provider_profile="default",
    )

    config = resolve_llm_config(settings)

    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-chat"


def test_llm_stream_defaults_off_and_follows_settings() -> None:
    profiles = Path("app/agent/nodes/profiles/provider_profiles.yaml")
    assert resolve_llm_config(Settings(llm_api_key="k", agent_provider_profiles_file=profiles)).stream is False
    enabled = Settings(llm_api_key="k", llm_stream=True, agent_provider_profiles_file=profiles)
    assert resolve_llm_config(enabled).stream is True
