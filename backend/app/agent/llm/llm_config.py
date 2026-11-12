"""LLM configuration of the provider adapter.

The agent package only knows ``LLMConfig``, provider-profile YAML files and how
a profile combines with explicit host values. Reading environment variables or
application settings is the host's job (see ``app.agent_host.llm``).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
    stream: bool = False

    @property
    def enabled(self) -> bool:
        return self.profile_enabled and bool(self.api_key.strip())


def load_provider_profile(profile_file: Path, profile_name: str) -> dict[str, Any]:
    """Load one profile from a provider-profiles YAML file.

    ``profile_file`` is used as given; the caller resolves relative paths. A
    missing or invalid file yields an empty profile, and an unknown name falls
    back to the ``default`` profile. A nested ``llm`` mapping is merged on top.
    """
    candidate = profile_file
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


LLM_EXPLICIT_FIELDS = frozenset({"base_url", "model", "timeout_seconds", "temperature", "max_tokens"})


def build_llm_config(
    profile: Mapping[str, Any],
    *,
    api_key: str,
    profile_name: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
    temperature: float,
    max_tokens: int,
    stream: bool = False,
    explicit: Collection[str] = (),
) -> LLMConfig:
    """Combine a provider profile with host values, with defensive bounds.

    For each field in ``LLM_EXPLICIT_FIELDS`` the host value wins when the field
    is listed in ``explicit``; otherwise the profile value is used and the host
    value is only the fallback. ``stream`` is on when either side enables it.
    A profile with ``enabled: false`` clears the API key.
    """
    unknown = set(explicit) - LLM_EXPLICIT_FIELDS
    if unknown:
        raise ValueError(f"unknown_llm_explicit_fields:{','.join(sorted(unknown))}")
    payload = dict(profile)
    enabled = _pick_bool(payload, "enabled", True)
    selected_base_url = base_url if "base_url" in explicit else _pick_str(payload, "base_url", base_url)
    selected_model = model if "model" in explicit else _pick_str(payload, "model", model)
    selected_timeout = (
        float(timeout_seconds)
        if "timeout_seconds" in explicit
        else _pick_float(payload, "timeout_seconds", float(timeout_seconds))
    )
    selected_temperature = (
        float(temperature)
        if "temperature" in explicit
        else _pick_float(payload, "temperature", float(temperature))
    )
    selected_max_tokens = (
        int(max_tokens) if "max_tokens" in explicit else _pick_int(payload, "max_tokens", int(max_tokens))
    )

    return LLMConfig(
        api_key=api_key if enabled else "",
        base_url=selected_base_url,
        model=selected_model,
        timeout_seconds=max(1.0, selected_timeout),
        temperature=max(0.0, selected_temperature),
        max_tokens=max(32, selected_max_tokens),
        tool_choice=_pick_str(payload, "tool_choice", "auto"),
        parallel_tool_calls=_pick_bool(payload, "parallel_tool_calls", False),
        prefer_chat_completions=_pick_bool(payload, "prefer_chat_completions", False),
        profile_name=profile_name,
        profile_enabled=enabled,
        api_mode=_pick_str(payload, "api_mode", "auto"),
        auth_header=_pick_str(payload, "auth_header", "Authorization"),
        send_temperature=_pick_bool(payload, "send_temperature", True),
        token_limit_parameter=_pick_str(payload, "token_limit_parameter", "max_tokens"),
        supported_tool_choices=tuple(payload.get("supported_tool_choices", ["auto", "none", "required"])),
        extra_parameters=dict(payload.get("extra_parameters") or {}),
        stream=stream or _pick_bool(payload, "stream", False),
    )
