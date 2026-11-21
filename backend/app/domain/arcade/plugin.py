"""Arcade plugin: business hooks and tools installed into the generic agent host."""

from __future__ import annotations

from typing import Any

from pydantic import Field, SecretStr

from app.agent.artifacts.sink import ArtifactSink
from app.agent.plugins.spec import Plugin, PluginConfig, PluginInfo
from app.agent.tools.local import LocalToolProvider
from app.domain.arcade.domain import ArcadeTurnDomain
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


class ArcadeConfig(PluginConfig):
    """Configuration values for the arcade plugin (from ``plugins.yaml``)."""

    amap: AMapSettings = AMapSettings()


class ArcadePlugin(Plugin):
    """Arcade hooks and tools; resources and agents arrive in plan stage 2d."""

    info = PluginInfo(name="arcade", version="1.0.0", description="Arcade search, navigation and display domain.")
    config_model = ArcadeConfig

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

    def hooks(self, config: PluginConfig) -> ArcadeTurnDomain:
        return ArcadeTurnDomain(payload_mapper=self._payload_mapper, artifact_sink=self._artifact_sink)

    def tools(self, config: PluginConfig) -> LocalToolProvider:
        assert isinstance(config, ArcadeConfig)  # the host passes an instance of ``config_model``
        return LocalToolProvider(
            specs=arcade_tool_specs(),
            services=arcade_tool_services(
                store=self._store,
                amap_config=config.amap.to_amap_config(),
                mcp_tool_gateway=self._mcp_tool_gateway,
            ),
        )


__all__ = ["AMapSettings", "ArcadeConfig", "ArcadePlugin"]
