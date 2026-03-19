"""FastMCP-backed discovery and dispatch bridge for runtime tools."""
"""基于FastMCP的发现和调度桥梁，用于运行时工具。
总得来说属于一个工具网关，负责发现MCP工具并通过FastMCP客户端执行它们。
是本模块下的核心类，提供了工具发现、工具定义构建、工具执行和路径规划等功能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.tools.mcp.client_manager import MCPClientManager
from app.agent.tools.mcp.discovery import (
    build_tool_definitions,
    discover_tools,
    infer_source_type,
    local_tool_name,
    mask_url,
    pick_route_tool,
    short,
    utc_now_iso,
    with_query_param,
)
from app.agent.tools.mcp.dispatcher import (
    MCPDispatcher,
    build_route_arguments,
    pick_route_descriptor,
)
from app.agent.tools.mcp.models import (
    MCPExecutionResult,
    MCPServerConfig,
    MCPServerState,
    MCPToolDescriptor,
)
from app.infra.observability.logger import get_logger
from app.protocol.messages import Location, RouteSummaryDto

logger = get_logger(__name__)


class MCPToolGateway:
    """
    Discover MCP tools and execute them via FastMCP Client.
    发现MCP工具并通过FastMCP客户端执行它们。
    """

    def __init__(
        self,
        *,
        servers: list[MCPServerConfig] | None = None,
        client_manager: MCPClientManager | None = None,
        dispatcher: MCPDispatcher | None = None,
    ) -> None:
        self._servers: dict[str, MCPServerConfig] = {
            item.name: item for item in (servers or []) if item.name.strip()
        }
        self._states: dict[str, MCPServerState] = {
            item.name: MCPServerState(
                enabled=item.enabled,
                configured=bool(item.enabled and item.source is not None),
                url=mask_url(item.url),
                source_type=item.source_type or infer_source_type(item.source),
            )
            for item in (servers or [])
            if item.name.strip()
        }
        self._tools: dict[str, MCPToolDescriptor] = {}
        self._discovered_once = False
        self._client_manager = client_manager or MCPClientManager()
        self._dispatcher = dispatcher or MCPDispatcher(client_manager=self._client_manager)

    @property
    def enabled(self) -> bool:
        return any(config.enabled for config in self._servers.values())

    def refresh(self) -> None:
        """Refresh all configured MCP tool definitions.
        刷新所有配置的MCP工具定义。
        这个方法会遍历所有配置的MCP服务器，尝试从每个服务器获取工具列表，
        并根据获取到的工具信息构建工具描述符和工具定义。   """
        descriptors: dict[str, MCPToolDescriptor] = {}
        for server_name, config in self._servers.items():
            state = self._states.setdefault(
                server_name,
                MCPServerState(
                    enabled=config.enabled,
                    configured=bool(config.source is not None),
                    source_type=config.source_type or infer_source_type(config.source),
                ),
            )
            state.enabled = config.enabled
            state.configured = bool(config.enabled and config.source is not None)
            state.last_discovery_at = utc_now_iso()
            state.available_tools = []
            state.selected_route_tool = None
            state.last_error = None
            state.discovered = False
            state.url = mask_url(config.url)
            state.source_type = config.source_type or infer_source_type(config.source)

            if not config.enabled:
                continue
            if config.source is None:
                state.last_error = "mcp server source is missing"
                continue

            try:
                raw_tools = self._client_manager.list_tools(config)
                discovered = discover_tools(server_name, raw_tools)
                for descriptor in discovered:
                    descriptors[descriptor.local_name] = descriptor
                    state.available_tools.append(descriptor.local_name)

                state.available_tools.sort()
                state.selected_route_tool = pick_route_tool(config=config, descriptors=discovered)
                state.discovered = True
                logger.info(
                    "mcp.discovery server=%s source_type=%s tools=%s selected_route_tool=%s",
                    server_name,
                    state.source_type,
                    state.available_tools,
                    state.selected_route_tool,
                )
            except Exception as exc:  # pragma: no cover - exercised by integration/network failures
                state.last_error = str(exc)
                logger.warning(
                    "mcp.discovery.failed server=%s source_type=%s error=%s",
                    server_name,
                    state.source_type,
                    short(str(exc)),
                )

        self._tools = descriptors
        self._discovered_once = True

    def ensure_ready(self) -> None:
        if self.enabled and not self._discovered_once:
            self.refresh()

    def build_tool_definitions(self, *, allowed_tools: list[str], strict: bool) -> list[dict[str, Any]]:
        self.ensure_ready()
        return build_tool_definitions(self._tools, allowed_tools=allowed_tools, strict=strict)

    def has_tool(self, tool_name: str) -> bool:
        self.ensure_ready()
        return tool_name in self._tools

    def execute(self, *, tool_name: str, raw_arguments: dict[str, Any]) -> MCPExecutionResult:
        self.ensure_ready()
        descriptor = self._tools.get(tool_name)
        if descriptor is None:
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "runtime_error", "message": f"unknown_tool:{tool_name}"}},
                error_message=f"unknown_tool:{tool_name}",
            )
        config = self._servers.get(descriptor.server_name)
        if config is None:
            message = f"unknown_mcp_server:{descriptor.server_name}"
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "runtime_error", "message": message}},
                error_message=message,
            )
        return self._dispatcher.execute(
            config=config,
            descriptor=descriptor,
            raw_arguments=raw_arguments,
        )

    def plan_amap_route(
        self,
        *,
        mode: str,
        origin: Location,
        destination: Location,
    ) -> RouteSummaryDto | None:
        """Use a discovered AMap MCP route tool when one is available.
        当有可用的已发现的AMap MCP路线工具时使用它。"""
        self.ensure_ready()
        descriptor = pick_route_descriptor(
            descriptors=list(self._tools.values()),
            server_name="amap",
            mode=mode,
        )
        if descriptor is None:
            return None
        arguments = build_route_arguments(
            descriptor=descriptor,
            origin=origin,
            destination=destination,
            mode=mode,
        )
        result = self.execute(tool_name=descriptor.local_name, raw_arguments=arguments)
        if result.status != "completed":
            logger.warning(
                "mcp.route.failed tool=%s error=%s",
                descriptor.local_name,
                short(result.error_message),
            )
            return None
        route_payload = result.output.get("route")
        if not isinstance(route_payload, dict):
            return None
        try:
            return RouteSummaryDto.model_validate(route_payload)
        except Exception:
            return None

    def health(self) -> dict[str, Any]:
        self.ensure_ready()
        return {
            "enabled": self.enabled,
            "discovered_tool_count": len(self._tools),
            "servers": {
                name: {
                    "enabled": state.enabled,
                    "configured": state.configured,
                    "discovered": state.discovered,
                    "available_tools": state.available_tools,
                    "selected_route_tool": state.selected_route_tool,
                    "last_error": state.last_error,
                    "last_discovery_at": state.last_discovery_at,
                    "url": state.url,
                    "source_type": state.source_type,
                }
                for name, state in sorted(self._states.items())
            },
        }


def build_amap_mcp_server_config(
    *,
    enabled: bool,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    route_tool_name: str | None,
) -> MCPServerConfig:
    """Build an AMap MCP config using HTTP or a local FastMCP server script."""
    raw_base = base_url.strip()
    if raw_base.endswith(".py") and Path(raw_base).exists():
        return MCPServerConfig(
            name="amap",
            enabled=enabled,
            source=raw_base,
            url=raw_base,
            timeout_seconds=timeout_seconds,
            route_tool_name=route_tool_name,
            source_type="script",
        )

    url = raw_base or "https://mcp.amap.com/mcp"
    if api_key.strip():
        url = with_query_param(url, key="key", value=api_key.strip())
    return MCPServerConfig(
        name="amap",
        enabled=enabled,
        source=url,
        url=url,
        timeout_seconds=timeout_seconds,
        route_tool_name=route_tool_name,
        source_type="http",
    )
