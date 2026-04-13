"""Configuration layer: load runtime settings from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_WINDOWS_ABS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute_path_like(path_like: str) -> bool:
    if _WINDOWS_ABS_PATH_RE.match(path_like):
        return True
    return Path(path_like).is_absolute()


def _resolve_path(path_like: str) -> Path:
    candidate = Path(path_like)
    if _is_absolute_path_like(path_like):
        return candidate
    if candidate.exists():
        return candidate
    project_root = Path(__file__).resolve().parents[3]
    rooted = project_root / candidate
    if rooted.exists():
        return rooted
    return candidate


def _resolve_project_path(path_like: str) -> Path:
    candidate = Path(path_like)
    if _is_absolute_path_like(path_like):
        return candidate
    project_root = Path(__file__).resolve().parents[3]
    return project_root / candidate


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv_if_exists() -> None:
    """加载项目根目录下的 .env 文件（如果存在），将其中的键值对设置到环境变量中，供后续配置加载使用。"""
    project_root = Path(__file__).resolve().parents[3]
    dotenv_path = project_root / ".env"
    if not dotenv_path.exists():
        return
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, parsed)


@dataclass(frozen=True)
class Settings:
    """Immutable application settings used across API/runtime layers."""

    app_name: str = "Arcadegent Agent API"
    app_version: str = "0.1.0"
    env: str = "dev"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    data_jsonl_path: Path = Path("data/raw/bemanicn/shops_detail.jsonl")
    chat_session_store_path: Path = Path("data/runtime/chat_sessions.json")
    replay_buffer_size: int = 200
    sse_keepalive_seconds: float = 1.0
    sse_max_wait_seconds: int = 20
    enable_provider_fallback: bool = True
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 20.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 500
    agent_max_steps: int = 20
    agent_context_window: int = 24
    agent_nodes_definitions_dir: Path = Path("app/agent/nodes/definitions")
    agent_tool_policy_file: Path = Path("app/agent/nodes/profiles/tool_policies.yaml")
    agent_subagent_yaml_overlay_enabled: bool = True
    agent_provider_profiles_file: Path = Path("app/agent/nodes/profiles/provider_profiles.yaml")
    agent_provider_profile: str = "default"
    mcp_default_timeout_seconds: float = 10.0
    mcp_servers_dir: Path = Path("backend/app/agent/tools/mcp/servers")
    amap_api_key: str = ""
    amap_base_url: str = "https://restapi.amap.com"
    amap_timeout_seconds: float = 8.0
    arcade_geo_cache_path: Path = Path("data/runtime/arcade_geo_cache.json")
    arcade_geo_sync_limit: int = 8
    arcade_geo_max_workers: int = 4
    arcade_geo_request_timeout_seconds: float = 1.2

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from process env with deterministic defaults."""
        _load_dotenv_if_exists()
        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name),
            app_version=os.getenv("APP_VERSION", cls.app_version),
            env=os.getenv("APP_ENV", cls.env),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
            host=os.getenv("HOST", cls.host),
            port=int(os.getenv("PORT", str(cls.port))),
            cors_allow_origins=os.getenv("CORS_ALLOW_ORIGINS", cls.cors_allow_origins),
            data_jsonl_path=_resolve_path(os.getenv("ARCADE_DATA_JSONL", str(cls.data_jsonl_path))),
            chat_session_store_path=_resolve_project_path(
                os.getenv("CHAT_SESSION_STORE_PATH", str(cls.chat_session_store_path))
            ),
            replay_buffer_size=int(os.getenv("REPLAY_BUFFER_SIZE", str(cls.replay_buffer_size))),
            sse_keepalive_seconds=float(
                os.getenv("SSE_KEEPALIVE_SECONDS", str(cls.sse_keepalive_seconds))
            ),
            sse_max_wait_seconds=int(
                os.getenv("SSE_MAX_WAIT_SECONDS", str(cls.sse_max_wait_seconds))
            ),
            enable_provider_fallback=_env_bool(
                "ENABLE_PROVIDER_FALLBACK", cls.enable_provider_fallback
            ),
            llm_api_key=os.getenv("LLM_API_KEY", cls.llm_api_key),
            llm_base_url=os.getenv("LLM_BASE_URL", cls.llm_base_url),
            llm_model=os.getenv("LLM_MODEL", cls.llm_model),
            llm_timeout_seconds=float(
                os.getenv("LLM_TIMEOUT_SECONDS", str(cls.llm_timeout_seconds))
            ),
            llm_temperature=float(
                os.getenv("LLM_TEMPERATURE", str(cls.llm_temperature))
            ),
            llm_max_tokens=int(
                os.getenv("LLM_MAX_TOKENS", str(cls.llm_max_tokens))
            ),
            agent_max_steps=int(
                os.getenv("AGENT_MAX_STEPS", str(cls.agent_max_steps))
            ),
            agent_context_window=int(
                os.getenv("AGENT_CONTEXT_WINDOW", str(cls.agent_context_window))
            ),
            agent_nodes_definitions_dir=_resolve_path(
                os.getenv(
                    "AGENT_NODES_DEFINITIONS_DIR",
                    str(cls.agent_nodes_definitions_dir),
                )
            ),
            agent_tool_policy_file=_resolve_path(
                os.getenv("AGENT_TOOL_POLICY_FILE", str(cls.agent_tool_policy_file))
            ),
            agent_subagent_yaml_overlay_enabled=_env_bool(
                "AGENT_SUBAGENT_YAML_OVERLAY_ENABLED",
                cls.agent_subagent_yaml_overlay_enabled,
            ),
            agent_provider_profiles_file=_resolve_path(
                os.getenv(
                    "AGENT_PROVIDER_PROFILES_FILE",
                    str(cls.agent_provider_profiles_file),
                )
            ),
            agent_provider_profile=os.getenv(
                "AGENT_PROVIDER_PROFILE",
                cls.agent_provider_profile,
            ),
            mcp_default_timeout_seconds=float(
                os.getenv("MCP_DEFAULT_TIMEOUT_SECONDS", str(cls.mcp_default_timeout_seconds))
            ),
            mcp_servers_dir=_resolve_path(
                os.getenv("MCP_SERVERS_DIR", str(cls.mcp_servers_dir))
            ),
            amap_api_key=os.getenv("AMAP_API_KEY", cls.amap_api_key),
            amap_base_url=os.getenv("AMAP_BASE_URL", cls.amap_base_url),
            amap_timeout_seconds=float(
                os.getenv("AMAP_TIMEOUT_SECONDS", str(cls.amap_timeout_seconds))
            ),
            arcade_geo_cache_path=_resolve_project_path(
                os.getenv("ARCADE_GEO_CACHE_PATH", str(cls.arcade_geo_cache_path))
            ),
            arcade_geo_sync_limit=int(
                os.getenv("ARCADE_GEO_SYNC_LIMIT", str(cls.arcade_geo_sync_limit))
            ),
            arcade_geo_max_workers=int(
                os.getenv("ARCADE_GEO_MAX_WORKERS", str(cls.arcade_geo_max_workers))
            ),
            arcade_geo_request_timeout_seconds=float(
                os.getenv(
                    "ARCADE_GEO_REQUEST_TIMEOUT_SECONDS",
                    str(cls.arcade_geo_request_timeout_seconds),
                )
            ),
        )
