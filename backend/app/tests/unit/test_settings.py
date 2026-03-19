"""Unit tests for environment-driven settings, including LLM/AMap config."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def test_settings_reads_llm_and_amap_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.4")
    monkeypatch.setenv("LLM_MAX_TOKENS", "321")
    monkeypatch.setenv("AGENT_MAX_STEPS", "7")
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "18")
    monkeypatch.setenv("AGENT_NODES_DEFINITIONS_DIR", "custom/defs")
    monkeypatch.setenv("AGENT_TOOL_POLICY_FILE", "custom/policy.yaml")
    monkeypatch.setenv("AGENT_SUBAGENT_YAML_OVERLAY_ENABLED", "false")
    monkeypatch.setenv("AGENT_PROVIDER_PROFILES_FILE", "custom/provider_profiles.yaml")
    monkeypatch.setenv("AGENT_PROVIDER_PROFILE", "rule_based")
    monkeypatch.setenv("MCP_AMAP_ENABLED", "true")
    monkeypatch.setenv("MCP_AMAP_BASE_URL", "https://mcp.amap.com/mcp")
    monkeypatch.setenv("MCP_AMAP_API_KEY", "mcp-key")
    monkeypatch.setenv("MCP_AMAP_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("MCP_AMAP_ROUTE_TOOL_NAME", "maps_direction_walking")
    monkeypatch.setenv("AMAP_API_KEY", "amap-key")
    monkeypatch.setenv("AMAP_BASE_URL", "https://restapi.amap.com")
    monkeypatch.setenv("AMAP_TIMEOUT_SECONDS", "6")

    settings = Settings.from_env()

    assert settings.llm_api_key == "test-key"
    assert settings.llm_base_url == "https://api.example.com/v1"
    assert settings.llm_model == "gpt-test"
    assert settings.llm_timeout_seconds == 9
    assert settings.llm_temperature == 0.4
    assert settings.llm_max_tokens == 321
    assert settings.agent_max_steps == 7
    assert settings.agent_context_window == 18
    assert settings.agent_nodes_definitions_dir == Path("custom/defs")
    assert settings.agent_tool_policy_file == Path("custom/policy.yaml")
    assert settings.agent_subagent_yaml_overlay_enabled is False
    assert settings.agent_provider_profiles_file == Path("custom/provider_profiles.yaml")
    assert settings.agent_provider_profile == "rule_based"
    assert settings.mcp_amap_enabled is True
    assert settings.mcp_amap_base_url == "https://mcp.amap.com/mcp"
    assert settings.mcp_amap_api_key == "mcp-key"
    assert settings.mcp_amap_timeout_seconds == 12
    assert settings.mcp_amap_route_tool_name == "maps_direction_walking"
    assert settings.amap_api_key == "amap-key"
    assert settings.amap_base_url == "https://restapi.amap.com"
    assert settings.amap_timeout_seconds == 6

def test_settings_does_not_use_openai_env_names(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-openai")

    settings = Settings.from_env()

    assert settings.llm_api_key == ""
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-4o-mini"


def test_settings_defaults_include_extended_agent_step_budget(monkeypatch) -> None:
    _ = monkeypatch
    assert Settings.agent_max_steps >= 8
