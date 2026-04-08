"""FastMCP-backed discovery and dispatch bridge for runtime tools."""

from __future__ import annotations

# 基于 FastMCP 的发现和调度桥梁，用于运行时工具。
# 总得来说属于一个工具网关，负责发现 MCP 工具并通过 FastMCP 客户端执行它们。
# 是本模块下的核心类，提供了工具发现、工具定义构建、工具执行等功能。

import os
from pathlib import Path
import re
from typing import Any

from app.agent.tools.base import ToolDescriptor
from app.agent.tools.mcp.client_manager import MCPClientManager
from app.agent.tools.mcp.discovery import (
    coerce_str,
    discover_tools,
    infer_source_type,
    mask_url,
    pick_route_tool,
    short,
    utc_now_iso,
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
from app.agent.tools.schemas import load_json_schema
from app.infra.observability.logger import get_logger
from app.protocol.messages import Location, RouteSummaryDto

logger = get_logger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Coerce a value to boolean, accepting various common string and numeric representations, with a default fallback.
    将一个值强制转换为布尔值，接受各种常见的字符串和数字表示，并提供默认回退。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _extract_timeout_seconds(
    server_name: str,
    payload: dict[str, Any],
    *,
    default_timeout_seconds: float,
) -> float:
    """Extract a timeout in seconds from the payload, accepting various common keys and formats, with a default fallback.
    从负载中提取以秒为单位的超时，接受各种常见的键和格式，并提供默认回退。"""
    for key in ("client_timeout_seconds", "clientTimeoutSeconds", "timeout_seconds", "timeoutSeconds"):
        raw_value = payload.pop(key, None)
        if raw_value is None:
            continue
        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"mcp server '{server_name}' has invalid {key}: {raw_value!r}") from exc

    raw_timeout_ms = payload.get("timeout")
    if raw_timeout_ms is None:
        return default_timeout_seconds
    try:
        timeout_ms = float(raw_timeout_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"mcp server '{server_name}' has invalid timeout milliseconds: {raw_timeout_ms!r}"
        ) from exc
    return timeout_ms / 1000.0


def _is_server_payload(value: Any) -> bool:
    return isinstance(value, dict) and ("command" in value or "url" in value)


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_placeholders(item) for key, item in value.items()}
    return value


def _extract_server_configs(
    raw_config: dict[str, Any],
    *,
    default_server_name: str | None = None,
) -> dict[str, Any]:
    """Extract server configurations from raw MCP config objects.
    从原始 MCP 配置对象中提取服务器配置。"""
    mcp_servers = raw_config.get("mcpServers")
    if isinstance(mcp_servers, dict):
        return mcp_servers

    if default_server_name is not None and _is_server_payload(raw_config):
        return {default_server_name: raw_config}

    has_root_server_mapping = any(_is_server_payload(value) for value in raw_config.values())
    if has_root_server_mapping:
        return raw_config
    raise ValueError(
        "mcp config must contain an 'mcpServers' object, a root-level server mapping, "
        "or a single-server object"
    )


def _merge_server_configs(
    *,
    merged: dict[str, Any],
    incoming: dict[str, Any],
    source_label: str,
) -> None:
    for server_name, payload in incoming.items():
        normalized_name = str(server_name).strip()
        if not normalized_name:
            raise ValueError(f"mcp server name must not be empty in {source_label}")
        if normalized_name in merged:
            raise ValueError(f"duplicate_mcp_server:{normalized_name}:{source_label}")
        merged[normalized_name] = payload


def _load_server_configs_from_directory(config_dir: Path) -> dict[str, Any]:
    if not config_dir.exists():
        raise ValueError(f"mcp config directory does not exist: {config_dir}")
    if not config_dir.is_dir():
        raise ValueError(f"mcp config directory is not a directory: {config_dir}")

    merged: dict[str, Any] = {}
    for path in sorted(config_dir.glob("*.json")):
        if not path.is_file():
            continue
        payload = load_json_schema(path)
        server_configs = _extract_server_configs(
            payload,
            default_server_name=path.stem.strip() or None,
        )
        _merge_server_configs(
            merged=merged,
            incoming=server_configs,
            source_label=str(path),
        )
    return merged


def _normalize_server_payload(server_name: str, raw_payload: Any) -> dict[str, Any]:
    if not isinstance(raw_payload, dict):
        raise ValueError(f"mcp server '{server_name}' config must be an object")

    payload = dict(raw_payload)
    transport = coerce_str(payload.get("transport"))
    transport_type = coerce_str(payload.get("type"))
    if transport is None and transport_type in {"http", "streamable-http", "sse", "stdio"}:
        payload["transport"] = transport_type
    if "url" not in payload and "command" not in payload:
        raise ValueError(f"mcp server '{server_name}' must define either 'url' or 'command'")
    return payload


def build_mcp_server_configs(
    *,
    raw_config: dict[str, Any] | None = None,
    config_dir: str | Path | None = None,
    default_timeout_seconds: float = 10.0,
) -> list[MCPServerConfig]:
    """Build runtime server configs from standard MCP JSON objects or directories."""
    if raw_config is None:
        merged_server_configs: dict[str, Any] = {}
        if config_dir is not None:
            _merge_server_configs(
                merged=merged_server_configs,
                incoming=_load_server_configs_from_directory(Path(config_dir)),
                source_label=str(config_dir),
            )
        if not merged_server_configs:
            return []
        raw_config = {"mcpServers": merged_server_configs}

    if not isinstance(raw_config, dict):
        raise ValueError("mcp config must be a JSON object")

    server_configs: list[MCPServerConfig] = []
    for server_name, raw_payload in _extract_server_configs(raw_config).items():
        name = str(server_name).strip()
        if not name:
            raise ValueError("mcp server name must not be empty")

        payload = _normalize_server_payload(name, _expand_env_placeholders(raw_payload))
        enabled = _coerce_bool(payload.pop("enabled", True), default=True)
        if "disabled" in payload:
            enabled = not _coerce_bool(payload.pop("disabled"), default=False)
        route_tool_name = coerce_str(payload.pop("route_tool_name", payload.pop("routeToolName", None)))
        timeout_seconds = _extract_timeout_seconds(
            name,
            payload,
            default_timeout_seconds=default_timeout_seconds,
        )
        source = {"mcpServers": {name: payload}}
        server_configs.append(
            MCPServerConfig(
                name=name,
                enabled=enabled,
                source=source,
                url=coerce_str(payload.get("url")),
                timeout_seconds=timeout_seconds,
                route_tool_name=route_tool_name,
                source_type=infer_source_type(source),
            )
        )
    return server_configs


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
    def provider_name(self) -> str:
        return "mcp"

    @property
    def enabled(self) -> bool:
        return any(config.enabled for config in self._servers.values())

    async def refresh(self) -> None:
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
                raw_tools = await self._client_manager.list_tools(config)
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

    async def ensure_ready(self) -> None:
        if self.enabled and not self._discovered_once:
            await self.refresh()

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        await self.ensure_ready()
        descriptors: dict[str, ToolDescriptor] = {}
        for local_name, descriptor in self._tools.items():
            descriptors[local_name] = ToolDescriptor(
                name=descriptor.local_name,
                description=descriptor.description,
                provider=self.provider_name,
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                capabilities=("mcp", descriptor.server_name),
            )
        return descriptors

    async def build_tool_definitions(self, *, allowed_tools: list[str], strict: bool) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        tools = await self.get_tools()
        allow_all_mcp = "mcp__*" in allowed_tools
        for tool_name in sorted(tools):
            if not (allow_all_mcp or tool_name in allowed_tools):
                continue
            descriptor = tools[tool_name]
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": descriptor.name,
                        "description": descriptor.description,
                        "parameters": descriptor.input_schema,
                        "strict": strict,
                    },
                }
            )
        return definitions

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    async def execute(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        validated_arguments: Any | None = None,
    ) -> MCPExecutionResult:
        _ = validated_arguments
        await self.ensure_ready()
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
        return await self._dispatcher.execute(
            config=config,
            descriptor=descriptor,
            raw_arguments=raw_arguments,
        )

    async def plan_amap_route(
        self,
        *,
        mode: str,
        origin: Location,
        destination: Location,
    ) -> RouteSummaryDto | None:
        """Use a discovered AMap MCP route tool when one is available.
        当有可用的已发现的AMap MCP路线工具时使用它。"""
        await self.ensure_ready()
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
        result = await self.execute(tool_name=descriptor.local_name, raw_arguments=arguments)
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
