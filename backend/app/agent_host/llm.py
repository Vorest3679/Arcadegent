"""Map application settings to the agent package's ``LLMConfig``."""

from __future__ import annotations

from pathlib import Path

from app.agent.llm.llm_config import LLMConfig, build_llm_config, load_provider_profile
from app.core.config import Settings

# parents[2] is ``backend/``: relative profile paths are written against it.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _resolve_profile_file(path: Path) -> Path:
    """Resolve a relative profile path against ``backend/``, never the CWD.

    Checking ``Path.exists()`` on a relative path would make the loaded profile
    depend on the process working directory and could silently pick a same-named
    file from elsewhere. Relative paths therefore always start at ``backend/``.
    """
    if path.is_absolute():
        return path
    rooted = _BACKEND_ROOT / path
    return rooted if rooted.exists() else path


def resolve_llm_config(settings: Settings) -> LLMConfig:
    """Build the LLM config from settings and the selected provider profile.

    A setting overrides the profile only when it differs from the ``Settings``
    default, so an unset environment variable never masks a profile value.
    """
    profile = load_provider_profile(
        _resolve_profile_file(settings.agent_provider_profiles_file),
        settings.agent_provider_profile,
    )
    defaults = Settings()
    explicit = {
        name
        for name, value, default in (
            ("base_url", settings.llm_base_url, defaults.llm_base_url),
            ("model", settings.llm_model, defaults.llm_model),
            ("timeout_seconds", float(settings.llm_timeout_seconds), float(defaults.llm_timeout_seconds)),
            ("temperature", float(settings.llm_temperature), float(defaults.llm_temperature)),
            ("max_tokens", int(settings.llm_max_tokens), int(defaults.llm_max_tokens)),
        )
        if value != default
    }
    return build_llm_config(
        profile,
        api_key=settings.llm_api_key,
        profile_name=settings.agent_provider_profile,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=float(settings.llm_timeout_seconds),
        temperature=float(settings.llm_temperature),
        max_tokens=int(settings.llm_max_tokens),
        stream=settings.llm_stream,
        explicit=explicit,
    )
