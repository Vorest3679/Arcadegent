"""Unit tests for provider-profile aware LLM config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.llm.llm_config import build_llm_config, load_provider_profile
from app.agent_host.llm import resolve_llm_config
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


def _host_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "api_key": "k",
        "profile_name": "custom",
        "base_url": "https://host.example/v1",
        "model": "host-model",
        "timeout_seconds": 10.0,
        "temperature": 0.5,
        "max_tokens": 800,
    }
    values.update(overrides)
    return values


def test_build_llm_config_prefers_profile_unless_field_is_explicit() -> None:
    profile = {"model": "profile-model", "base_url": "https://profile.example/v1", "max_tokens": 300}

    from_profile = build_llm_config(profile, **_host_values())  # type: ignore[arg-type]
    explicit = build_llm_config(profile, **_host_values(), explicit={"model"})  # type: ignore[arg-type]

    assert (from_profile.model, from_profile.base_url, from_profile.max_tokens) == (
        "profile-model", "https://profile.example/v1", 300)
    assert (explicit.model, explicit.base_url) == ("host-model", "https://profile.example/v1")
    assert from_profile.timeout_seconds == 10.0  # host value is the fallback


def test_build_llm_config_bounds_disable_and_stream() -> None:
    config = build_llm_config(
        {"enabled": False, "timeout_seconds": 0, "temperature": -1, "max_tokens": 1, "stream": "true"},
        **_host_values(),  # type: ignore[arg-type]
    )

    assert config.api_key == "" and config.enabled is False
    assert (config.timeout_seconds, config.temperature, config.max_tokens) == (1.0, 0.0, 32)
    assert config.stream is True


def test_build_llm_config_rejects_unknown_explicit_fields() -> None:
    with pytest.raises(ValueError, match="unknown_llm_explicit_fields:api_key"):
        build_llm_config({}, **_host_values(), explicit={"api_key"})  # type: ignore[arg-type]


def test_resolve_llm_config_ignores_a_same_named_file_in_the_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    decoy = tmp_path / "app" / "agent" / "nodes" / "profiles" / "provider_profiles.yaml"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("profiles:\n  default:\n    model: decoy-model\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = resolve_llm_config(
        Settings(
            llm_api_key="test-key",
            agent_provider_profiles_file=Path("app/agent/nodes/profiles/provider_profiles.yaml"),
        )
    )

    # Relative profile paths resolve against backend/, not the process CWD.
    assert config.model == "gpt-4o-mini"


def test_load_provider_profile_uses_the_given_path_only(tmp_path: Path, monkeypatch) -> None:
    profiles = tmp_path / "profiles.yaml"
    profiles.write_text(
        "profiles:\n  default:\n    model: m-default\n  fast:\n    model: m-fast\n    llm:\n      model: m-nested\n",
        encoding="utf-8",
    )

    assert load_provider_profile(profiles, "fast")["model"] == "m-nested"
    assert load_provider_profile(profiles, "missing")["model"] == "m-default"
    monkeypatch.chdir(tmp_path)
    # No project-root guessing: a relative path that does not exist is just empty.
    assert load_provider_profile(Path("app/agent/nodes/profiles/provider_profiles.yaml"), "default") == {}
