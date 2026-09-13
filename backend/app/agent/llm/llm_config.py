"""LLM config resolver for ReAct runtime and provider adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings


@dataclass(frozen=True)
class LLMConfig:
    """Normalized LLM runtime configuration."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    temperature: float
    max_tokens: int
    tool_choice: str = "auto"
    parallel_tool_calls: bool = False
    prefer_chat_completions: bool = False
    profile_name: str = "default"
    profile_enabled: bool = True
    api_mode: str = "auto"
    auth_header: str = "Authorization"
    send_temperature: bool = True
    token_limit_parameter: str = "max_tokens"
    supported_tool_choices: tuple[str, ...] = ("auto", "none", "required")
    extra_parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.profile_enabled and bool(self.api_key.strip())


def _load_profile(profile_file: Path, profile_name: str) -> dict[str, Any]:
    candidate = profile_file
    if not candidate.exists() and not candidate.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        rooted = project_root / candidate
        if rooted.exists():
            candidate = rooted
    if not candidate.exists():
        return {}
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    payload = profiles.get(profile_name)
    if not isinstance(payload, dict):
        payload = profiles.get("default")
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("llm")
    if isinstance(nested, dict):
        merged = dict(payload)
        merged.update(nested)
        return merged
    return payload


def _pick_str(payload: dict[str, Any], key: str, fallback: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _pick_float(payload: dict[str, Any], key: str, fallback: float) -> float:
    value = payload.get(key)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _pick_int(payload: dict[str, Any], key: str, fallback: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _pick_bool(payload: dict[str, Any], key: str, fallback: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return fallback


def resolve_llm_config(settings: Settings) -> LLMConfig:
    """Build LLM config from app settings with defensive bounds."""
    profile = _load_profile(
        settings.agent_provider_profiles_file,
        settings.agent_provider_profile,
    )
    enabled = _pick_bool(profile, "enabled", True)
    default_settings = Settings()
    profile_base_url = _pick_str(profile, "base_url", default_settings.llm_base_url)
    profile_model = _pick_str(profile, "model", default_settings.llm_model)
    profile_timeout = _pick_float(profile, "timeout_seconds", float(default_settings.llm_timeout_seconds))
    profile_temperature = _pick_float(profile, "temperature", float(default_settings.llm_temperature))
    profile_max_tokens = _pick_int(profile, "max_tokens", int(default_settings.llm_max_tokens))

    # Allow explicit runtime/env settings to override profile defaults.
    selected_base_url = (
        settings.llm_base_url
        if settings.llm_base_url != default_settings.llm_base_url
        else profile_base_url
    )
    selected_model = (
        settings.llm_model
        if settings.llm_model != default_settings.llm_model
        else profile_model
    )
    selected_timeout = (
        float(settings.llm_timeout_seconds)
        if float(settings.llm_timeout_seconds) != float(default_settings.llm_timeout_seconds)
        else profile_timeout
    )
    selected_temperature = (
        float(settings.llm_temperature)
        if float(settings.llm_temperature) != float(default_settings.llm_temperature)
        else profile_temperature
    )
    selected_max_tokens = (
        int(settings.llm_max_tokens)
        if int(settings.llm_max_tokens) != int(default_settings.llm_max_tokens)
        else profile_max_tokens
    )

    return LLMConfig(
        api_key=settings.llm_api_key if enabled else "",
        base_url=selected_base_url,
        model=selected_model,
        timeout_seconds=max(1.0, selected_timeout),
        temperature=max(0.0, selected_temperature),
        max_tokens=max(32, selected_max_tokens),
        tool_choice=_pick_str(profile, "tool_choice", "auto"),
        parallel_tool_calls=_pick_bool(profile, "parallel_tool_calls", False),
        prefer_chat_completions=_pick_bool(profile, "prefer_chat_completions", False),
        profile_name=settings.agent_provider_profile,
        profile_enabled=enabled,
        api_mode=_pick_str(profile, "api_mode", "auto"),
        auth_header=_pick_str(profile, "auth_header", "Authorization"),
        send_temperature=_pick_bool(profile, "send_temperature", True),
        token_limit_parameter=_pick_str(profile, "token_limit_parameter", "max_tokens"),
        supported_tool_choices=tuple(profile.get("supported_tool_choices", ["auto", "none", "required"])),
        extra_parameters=dict(profile.get("extra_parameters") or {}),
    )
