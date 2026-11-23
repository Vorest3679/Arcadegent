"""Composition layer: build and hold long-lived service objects for dependency injection."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from app.agent.artifacts.sink import ArtifactSink
from app.agent.context.context_builder import ContextBuilder
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.plugins import (
    CoreConfig,
    CorePlugin,
    Plugin,
    PluginConfig,
    PluginSet,
    build_plugin_mcp_servers,
    collect_skill_roots,
    install_configured,
    load_plugin_configs,
    require_enabled_plugins,
    resolve_plugin_resources,
)
from app.agent.runtime.react_runtime import ReactRuntime
from app.agent.skills.config import SkillConfig
from app.agent.skills.registry import SkillRegistry
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.mcp_gateway import MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.agent_host.llm import resolve_llm_config
from app.core.arcade_wiring import build_arcade_services
from app.core.config import Settings
from app.domain.arcade.plugin import ArcadePlugin
from app.domain.arcade.replies import ArcadeSessionDisplay
from app.infra.db.local import LocalArcadeStore
from app.infra.db.online import SupabaseArcadeRepository, SupabaseRepositoryConfig, build_supabase_session_repository
from app.infra.db.protocols import ArcadeRepository, SessionStateRepository
from app.services.amap_reverse_geocoder import AMapReverseGeocoder
from app.services.arcade_geo_resolver import ArcadeGeoResolver
from app.services.arcade_payload_mapper import ArcadePayloadMapper
from app.services.chat_rate_limit import ChatRateLimiter
from app.services.chat_run_service import ChatRunService
from app.services.chat_session_detail import ChatSessionDetailService
from app.session.run_log import RunLog
from app.session.runs import RunManager

# Host decisions ``plugins.yaml`` cannot change. Both plugins are required while
# the agent definitions use ``invoke_worker`` and the arcade tools; ``core`` is
# the only plugin trusted with reserved capabilities. The class table is also
# the host whitelist: plugins.yaml may only name these plugins.
PLUGIN_TYPES: dict[str, type[Plugin]] = {"core": CorePlugin, "arcade": ArcadePlugin}
REQUIRED_PLUGINS = ("core", "arcade")
TRUSTED_PLUGINS = frozenset({"core"})


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
    plugin_set: PluginSet
    react_runtime: ReactRuntime
    chat_runs: ChatRunService
    chat_rate_limiter: ChatRateLimiter = field(default_factory=ChatRateLimiter)


def build_container(
    settings: Settings,
    *,
    plugin_configs: Mapping[str, PluginConfig] | None = None,
    session_store: SessionStateRepository | None = None,
    provider_adapter: ProviderAdapter | None = None,
    mcp_tool_gateway: MCPToolGateway | None = None,
    arcade_payload_mapper: ArcadePayloadMapper | None = None,
) -> AppContainer:
    """Construct runtime dependencies in one place.

    Plugins are resolved from ``settings.agent_plugins_file``; placeholders take
    their values from the process environment, which ``Settings.from_env()`` has
    already filled from the project ``.env``. Tests and evaluation pass
    ``plugin_configs`` instead (enabled plugins' configs in install order) so
    production configuration is never read.

    Resource declarations are read before plugin instances are built, because
    the skill registry and MCP gateway depend on them. The resulting
    ``PluginSet`` then provides hooks, tools and agent definitions to the
    runtime.
    """
    store = _build_arcade_repository(settings)
    run_log = RunLog(max_events_per_run=settings.replay_buffer_size)
    run_manager = RunManager(log=run_log)
    provider_adapter = provider_adapter if provider_adapter is not None else ProviderAdapter(resolve_llm_config(settings))
    arcade = build_arcade_services(settings, payload_mapper=arcade_payload_mapper)

    configs = _resolve_plugin_configs(settings, plugin_configs)
    require_enabled_plugins(configs, REQUIRED_PLUGINS)
    resources = resolve_plugin_resources(PLUGIN_TYPES, configs)
    core_config = cast(CoreConfig, configs["core"])
    skill_registry = SkillRegistry(
        SkillConfig(
            roots=list(collect_skill_roots(resources)),
            **core_config.skills.model_dump(),
        )
    )
    if mcp_tool_gateway is None:
        mcp_tool_gateway = MCPToolGateway(
            servers=build_plugin_mcp_servers(
                configs,
                resources,
                default_timeout_seconds=settings.mcp_default_timeout_seconds,
            )
        )
    session_store = session_store if session_store is not None else _build_session_repository(settings)
    artifact_sink = ArtifactSink()

    plugins: dict[str, Plugin] = {
        "core": CorePlugin(skill_registry=skill_registry),
        "arcade": ArcadePlugin(
            store=store,
            payload_mapper=arcade.payload_mapper,
            artifact_sink=artifact_sink,
            mcp_tool_gateway=mcp_tool_gateway,
        ),
    }
    plugin_set = install_configured(
        plugins,
        configs,
        trusted=TRUSTED_PLUGINS,
        required=REQUIRED_PLUGINS,
    )
    roster = plugin_set.agents
    subagent_builder = SubAgentBuilder(roster.agents, aliases=roster.aliases)
    context_builder = ContextBuilder(
        prompt_loader=plugin_set.prompt_loader(),
        skill_registry=skill_registry,
        history_turn_limit=settings.agent_context_window,
    )
    tool_registry = ToolRegistry(
        providers={"mcp": mcp_tool_gateway, **plugin_set.tool_providers},
        permission_checker=ToolPermissionChecker(),
        strict_schema=True,
        # The runtime catalog is the only target of add/update/delete; runtime
        # writes to plugin providers are locked at installation.
        local_provider=LocalToolProvider(),
    )
    react_runtime = ReactRuntime(
        context_builder=context_builder,
        subagent_builder=subagent_builder,
        tool_registry=tool_registry,
        provider_adapter=provider_adapter,
        session_store=session_store,
        max_steps=settings.agent_max_steps,
        skill_registry=skill_registry,
        domain=plugin_set.domain,
        artifacts=artifact_sink,
    )
    chat_session_detail = ChatSessionDetailService(
        display_source=ArcadeSessionDisplay(artifact_sink=artifact_sink),
        payload_mapper=arcade.payload_mapper,
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
        reverse_geocoder=arcade.reverse_geocoder,
        arcade_geo_resolver=arcade.geo_resolver,
        arcade_payload_mapper=arcade.payload_mapper,
        chat_session_detail=chat_session_detail,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        plugin_set=plugin_set,
        react_runtime=react_runtime,
        chat_runs=chat_runs,
    )


def _resolve_plugin_configs(
    settings: Settings,
    overrides: Mapping[str, PluginConfig] | None,
) -> dict[str, PluginConfig]:
    if overrides is not None:
        return dict(overrides)
    return load_plugin_configs(
        settings.agent_plugins_file,
        plugins=PLUGIN_TYPES,
        environ=os.environ,
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
