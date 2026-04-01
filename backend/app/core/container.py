"""Composition layer: build and hold long-lived service objects for dependency injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent.context.context_builder import ContextBuilder
from app.agent.events.replay_buffer import ReplayBuffer
from app.agent.llm.llm_config import resolve_llm_config
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.orchestration.transition_policy import TransitionPolicy
from app.agent.runtime.react_runtime import ReactRuntime
from app.agent.runtime.session_state import SessionStateStore
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.runtime.orchestrator import Orchestrator
from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.mcp_gateway import MCPToolGateway, build_mcp_server_configs
from app.agent.tools.registry import ToolRegistry
from app.core.config import Settings
from app.infra.db.local_store import LocalArcadeStore
from app.services.amap_reverse_geocoder import AMapReverseGeocoder, AMapReverseGeocoderConfig


@dataclass
class AppContainer:
    """Container object attached to FastAPI app state."""

    settings: Settings
    store: LocalArcadeStore
    replay_buffer: ReplayBuffer
    session_store: SessionStateStore
    reverse_geocoder: AMapReverseGeocoder
    tool_registry: ToolRegistry
    react_runtime: ReactRuntime
    orchestrator: Orchestrator


def build_container(settings: Settings) -> AppContainer:
    """Construct runtime dependencies in one place."""
    store = LocalArcadeStore.from_jsonl(settings.data_jsonl_path)
    replay_buffer = ReplayBuffer(max_events_per_session=settings.replay_buffer_size)
    provider_adapter = ProviderAdapter(resolve_llm_config(settings))
    reverse_geocoder = AMapReverseGeocoder(
        config=AMapReverseGeocoderConfig(
            api_key=settings.amap_api_key,
            base_url=settings.amap_base_url,
            timeout_seconds=settings.amap_timeout_seconds,
        )
    )
    project_root = Path(__file__).resolve().parents[1]
    context_builder = ContextBuilder(
        prompt_root=project_root / "agent" / "context" / "prompts",
        skill_root=project_root / "agent" / "context" / "skills",
        history_turn_limit=settings.agent_context_window,
    )
    subagent_builder = SubAgentBuilder(
        definitions_dir=settings.agent_nodes_definitions_dir,
        enable_yaml_overlay=settings.agent_subagent_yaml_overlay_enabled,
    )
    permission_checker = ToolPermissionChecker(policy_file=settings.agent_tool_policy_file)
    mcp_servers = build_mcp_server_configs(
        config_dir=settings.mcp_servers_dir,
        default_timeout_seconds=settings.mcp_default_timeout_seconds,
    )
    mcp_tool_gateway = MCPToolGateway(
        servers=mcp_servers
    )
    builtin_tool_provider = BuiltinToolProvider(
        runtime_services={
            "store": store,
            "settings": settings,
            "mcp_tool_gateway": mcp_tool_gateway,
            "project_root": project_root,
        }
    )
    tool_registry = ToolRegistry(
        providers=[builtin_tool_provider, mcp_tool_gateway],
        permission_checker=permission_checker,
        strict_schema=True,
    )
    session_store = SessionStateStore(storage_path=settings.chat_session_store_path)
    react_runtime = ReactRuntime(
        context_builder=context_builder,
        subagent_builder=subagent_builder,
        tool_registry=tool_registry,
        provider_adapter=provider_adapter,
        session_store=session_store,
        transition_policy=TransitionPolicy(),
        replay_buffer=replay_buffer,
        max_steps=settings.agent_max_steps,
    )
    orchestrator = Orchestrator(
        react_runtime=react_runtime,
    )
    return AppContainer(
        settings=settings,
        store=store,
        replay_buffer=replay_buffer,
        session_store=session_store,
        reverse_geocoder=reverse_geocoder,
        tool_registry=tool_registry,
        react_runtime=react_runtime,
        orchestrator=orchestrator,
    )
