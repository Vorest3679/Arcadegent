"""Arcade plugin: business hooks, tools, resources and agent definitions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic import Field, SecretStr

from app.agent.artifacts.sink import ArtifactSink
from app.agent.plugins import (
    AgentRoster,
    LocalToolProvider,
    Plugin,
    PluginConfig,
    PluginInfo,
    PluginResources,
    RelPath,
)
from app.domain.arcade.domain import ArcadeTurnDomain
from app.domain.arcade.mcp_route import (
    AMAP_ROUTE_NORMALIZER,
    AMAP_ROUTE_SELECTOR,
    AMAP_SERVER,
    ROUTE_SELECTOR,
)
from app.domain.arcade.profiles import MCP_SERVERS_DIR, PROMPTS_DIR, SKILLS_DIR, arcade_agent_roster
from app.domain.arcade.tools import AMapConfig, arcade_tool_services, arcade_tool_specs
from app.infra.db.protocols import ArcadeRepository
from app.services.arcade_payload_mapper import ArcadePayloadMapper


class AMapSettings(PluginConfig):
    """AMap web service configuration for the arcade route tool."""

    api_key: SecretStr = SecretStr("")
    base_url: str = "https://restapi.amap.com"
    timeout_seconds: float = Field(8.0, gt=0)

    def to_amap_config(self) -> AMapConfig:
        return AMapConfig(
            api_key=self.api_key.get_secret_value(),
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        )


class ArcadeAgentsSettings(PluginConfig):
    """Whether and from where the arcade agent YAML overlay is read."""

    yaml_overlay: bool = True
    definitions_dir: RelPath | None = None


class ArcadeConfig(PluginConfig):
    """Configuration values for the arcade plugin (from ``plugins.yaml``)."""

    amap: AMapSettings = AMapSettings()
    agents: ArcadeAgentsSettings = ArcadeAgentsSettings()
    mcp_servers_dir: RelPath | None = None


class ArcadePlugin(Plugin):
    """Arcade hooks, tools, skill/MCP/prompt resources and agent definitions."""

    info = PluginInfo(name="arcade", version="1.0.0", description="Arcade search, navigation and display domain.")
    config_model = ArcadeConfig
    resources = PluginResources(
        skills=(SKILLS_DIR,),
        mcp_servers=(MCP_SERVERS_DIR,),
        prompts=(PROMPTS_DIR,),
        mcp_selectors={ROUTE_SELECTOR: AMAP_ROUTE_SELECTOR},
        mcp_normalizers={AMAP_SERVER: AMAP_ROUTE_NORMALIZER},
    )

    def __init__(
        self,
        *,
        store: ArcadeRepository,
        payload_mapper: ArcadePayloadMapper,
        artifact_sink: ArtifactSink,
        mcp_tool_gateway: Any | None = None,
    ) -> None:
        self._store = store
        self._payload_mapper = payload_mapper
        self._artifact_sink = artifact_sink
        self._mcp_tool_gateway = mcp_tool_gateway

    @classmethod
    def resolved_resources(cls, config: ArcadeConfig) -> PluginResources:
        if config.mcp_servers_dir is None:
            return cls.resources
        return replace(cls.resources, mcp_servers=(config.mcp_servers_dir,))

    def hooks(self, config: ArcadeConfig) -> ArcadeTurnDomain:
        return ArcadeTurnDomain(payload_mapper=self._payload_mapper, artifact_sink=self._artifact_sink)

    def tools(self, config: ArcadeConfig) -> LocalToolProvider:
        return LocalToolProvider(
            specs=arcade_tool_specs(),
            services=arcade_tool_services(
                store=self._store,
                amap_config=config.amap.to_amap_config(),
                mcp_tool_gateway=self._mcp_tool_gateway,
            ),
        )

    def agents(self, config: ArcadeConfig) -> AgentRoster:
        return arcade_agent_roster(
            yaml_overlay=config.agents.yaml_overlay,
            definitions_dir=config.agents.definitions_dir,
        )


__all__ = ["AMapSettings", "ArcadeAgentsSettings", "ArcadeConfig", "ArcadePlugin"]
