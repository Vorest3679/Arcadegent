"""Composition layer: build and hold long-lived service objects for dependency injection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agent.artifacts.sink import ArtifactSink
from app.agent.context.context_builder import ContextBuilder
from app.agent.skills.config import load_skill_config
from app.agent.skills.registry import SkillRegistry
from app.agent_host.llm import resolve_llm_config
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.runtime.react_runtime import ReactRuntime
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.mcp_gateway import MCPToolGateway, build_mcp_server_configs
from app.agent.tools.registry import ToolRegistry
from app.core.config import Settings
from app.domain.arcade.domain import ArcadeTurnDomain
from app.domain.arcade.tools import AMapConfig, arcade_tool_services, arcade_tool_specs
from app.infra.db.local import LocalArcadeStore
from app.infra.db.online import SupabaseArcadeRepository, SupabaseRepositoryConfig, build_supabase_session_repository
from app.infra.db.protocols import ArcadeRepository, SessionStateRepository
from app.services.arcade_geo_resolver import ArcadeGeoResolver, ArcadeGeoResolverConfig
from app.services.arcade_payload_mapper import ArcadePayloadMapper
from app.services.amap_reverse_geocoder import AMapReverseGeocoder, AMapReverseGeocoderConfig
from app.services.chat_run_service import ChatRunService
from app.services.chat_session_detail import ChatSessionDetailService
from app.services.chat_rate_limit import ChatRateLimiter
from app.session.run_log import RunLog
from app.session.runs import RunManager


@dataclass
class AppContainer:
    """Container object attached to FastAPI app state."""

    settings: Settings
    store: ArcadeRepository
    run_log: RunLog
    run_manager: RunManager
    session_store: SessionStateRepository
    reverse_geocoder: AMapReverseGeocoder
    arcade_geo_resolver: ArcadeGeoResolver
    arcade_payload_mapper: ArcadePayloadMapper
    chat_session_detail: ChatSessionDetailService
    tool_registry: ToolRegistry
    skill_registry: SkillRegistry
    react_runtime: ReactRuntime
    chat_runs: ChatRunService
    chat_rate_limiter: ChatRateLimiter = field(default_factory=ChatRateLimiter)


def build_container(
    settings: Settings,
    *,
    session_store: SessionStateRepository | None = None,
    provider_adapter: ProviderAdapter | None = None,
    mcp_tool_gateway: MCPToolGateway | None = None,
) -> AppContainer:
    """Construct runtime dependencies in one place."""
    store = _build_arcade_repository(settings)
    run_log = RunLog(max_events_per_run=settings.replay_buffer_size)
    run_manager = RunManager(log=run_log)
    provider_adapter = provider_adapter if provider_adapter is not None else ProviderAdapter(resolve_llm_config(settings))
    reverse_geocoder = AMapReverseGeocoder(
        config=AMapReverseGeocoderConfig(
            api_key=settings.amap_api_key,
            base_url=settings.amap_base_url,
            timeout_seconds=settings.amap_timeout_seconds,
        )
    )
    arcade_geo_resolver = ArcadeGeoResolver(
        config=ArcadeGeoResolverConfig(
            api_key=settings.amap_api_key,
            base_url=settings.amap_base_url,
            cache_path=settings.arcade_geo_cache_path,
            request_timeout_seconds=settings.arcade_geo_request_timeout_seconds,
            sync_limit=settings.arcade_geo_sync_limit,
            max_workers=settings.arcade_geo_max_workers,
        )
    )
    arcade_payload_mapper = ArcadePayloadMapper(geo_resolver=arcade_geo_resolver)
    project_root = Path(__file__).resolve().parents[1]
    skill_registry = SkillRegistry(load_skill_config(project_root.parent / "skill.config.py"))
    context_builder = ContextBuilder(
        prompt_root=project_root / "agent" / "context" / "prompts",
        skill_registry=skill_registry,
        history_turn_limit=settings.agent_context_window,
    )
    subagent_builder = SubAgentBuilder(
        definitions_dir=settings.agent_nodes_definitions_dir,
        enable_yaml_overlay=settings.agent_subagent_yaml_overlay_enabled,
    )
    permission_checker = ToolPermissionChecker(policy_file=settings.agent_tool_policy_file)
    if mcp_tool_gateway is None:
        mcp_servers = build_mcp_server_configs(
            config_dir=settings.mcp_servers_dir,
            default_timeout_seconds=settings.mcp_default_timeout_seconds,
        )
        mcp_tool_gateway = MCPToolGateway(servers=mcp_servers)
    # Generic agent tools (skills, worker dispatch) come from the agent's manifest.
    builtin_tool_provider = BuiltinToolProvider(services={"skill_registry": skill_registry})
    tool_registry = ToolRegistry(
        providers=[mcp_tool_gateway],
        permission_checker=permission_checker,
        strict_schema=True,
        local_provider=builtin_tool_provider,
    )
    # Arcade business tools are registered in code with the services they need.
    # The build is synchronous, so this writes the local catalog directly rather
    # than awaiting ``ToolRegistry.add_tools``, which would also run MCP discovery
    # here instead of on startup. Uniqueness is unaffected: MCP tools live under
    # ``mcp__``, a prefix ``ToolSpec`` rejects, and the catalog checks its own names.
    tool_registry.local_provider.add(
        arcade_tool_specs(),
        services=arcade_tool_services(
            store=store,
            amap_config=AMapConfig(
                api_key=settings.amap_api_key,
                base_url=settings.amap_base_url,
                timeout_seconds=settings.amap_timeout_seconds,
            ),
            mcp_tool_gateway=mcp_tool_gateway,
        ),
    )
    session_store = session_store if session_store is not None else _build_session_repository(settings)
    artifact_sink = ArtifactSink()
    arcade_domain = ArcadeTurnDomain(
        payload_mapper=arcade_payload_mapper,
        artifact_sink=artifact_sink,
    )
    react_runtime = ReactRuntime(
        context_builder=context_builder,
        subagent_builder=subagent_builder,
        tool_registry=tool_registry,
        provider_adapter=provider_adapter,
        session_store=session_store,
        max_steps=settings.agent_max_steps,
        skill_registry=skill_registry,
        domain=arcade_domain,
        artifacts=artifact_sink,
    )
    chat_session_detail = ChatSessionDetailService(
        display_source=arcade_domain,
        payload_mapper=arcade_payload_mapper,
    )
    chat_runs = ChatRunService(
        runtime=react_runtime,
        runs=run_manager,
        session_store=session_store,
    )
    return AppContainer(
        settings=settings,
        store=store,
        run_log=run_log,
        run_manager=run_manager,
        session_store=session_store,
        reverse_geocoder=reverse_geocoder,
        arcade_geo_resolver=arcade_geo_resolver,
        arcade_payload_mapper=arcade_payload_mapper,
        chat_session_detail=chat_session_detail,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        react_runtime=react_runtime,
        chat_runs=chat_runs,
    )


def _build_session_repository(settings: Settings) -> SessionStateRepository:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError(
            "supabase_session_store_config_required: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
        )
    return build_supabase_session_repository(
        SupabaseRepositoryConfig(
            url=settings.supabase_url,
            key=settings.supabase_service_role_key,
            timeout_seconds=settings.supabase_timeout_seconds,
        )
    )


def _build_arcade_repository(settings: Settings) -> ArcadeRepository:
    if settings.arcade_data_source == "jsonl":
        return LocalArcadeStore.from_jsonl(settings.data_jsonl_path)

    key = settings.supabase_anon_key or settings.supabase_service_role_key
    if not settings.supabase_url or not key:
        raise ValueError(
            "supabase_config_required: set SUPABASE_URL and SUPABASE_ANON_KEY "
            "or SUPABASE_SERVICE_ROLE_KEY when ARCADE_DATA_SOURCE=supabase"
        )
    return SupabaseArcadeRepository(
        SupabaseRepositoryConfig(
            url=settings.supabase_url,
            key=key,
            timeout_seconds=settings.supabase_timeout_seconds,
        )
    )
