"""FastMCP-backed discovery and dispatch bridge for runtime tools.

The gateway is domain-neutral: it discovers remote tools, exposes generic
execution, and delegates two decisions to ports supplied by the host:

* ``selectors`` (keyed by selector name) pick a tool per server for health
  and fast lookup;
* ``normalizers`` (keyed by server name) map a raw execution payload onto
  domain data.

An MCP server config may name an explicitly preferred tool per selector in
``select_tools``; the selector is the fallback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.agent.tools.base import ToolDescriptor
from app.agent.tools.mcp.client_manager import MCPClientManager
from app.agent.tools.mcp.discovery import (
    coerce_str,
    discover_tools,
    infer_source_type,
    local_tool_name,
    mask_url,
    utc_now_iso,
)
from app.agent.tools.mcp.dispatcher import MCPDispatcher
from app.agent.tools.mcp.models import (
    MCPExecutionResult,
    MCPServerConfig,
    MCPServerState,
    MCPToolDescriptor,
    MCP_TOOL_PREFIX,
)
from app.agent.tools.mcp.ports import MCPResultNormalizer, MCPToolSelector
from app.agent.tools.schemas import load_json_schema
from app.agent._logging import get_logger, log_public_label, log_ref

logger = get_logger(__name__)
_PUBLIC_SOURCE_TYPES = frozenset({"http", "stdio", "sse", "streamable-http", "script", "string", "config"})

_CONFIG_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Coerce a value to boolean, accepting common string and numeric forms."""

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
    """Extract a timeout in seconds from the payload, with a default fallback."""
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


def _expand_placeholders(value: Any, placeholders: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        def replace_placeholder(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in placeholders:
                raise ValueError("unresolved_mcp_placeholder")
            return placeholders[key]

        return _CONFIG_PLACEHOLDER.sub(replace_placeholder, value)
    if isinstance(value, list):
        return [_expand_placeholders(item, placeholders) for item in value]
    if isinstance(value, dict):
        return {key: _expand_placeholders(item, placeholders) for key, item in value.items()}
    return value


def _extract_server_configs(
    raw_config: dict[str, Any],
    *,
    default_server_name: str | None = None,
) -> dict[str, Any]:
    """Extract server configurations from raw MCP config objects."""
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


def _read_select_tools(payload: dict[str, Any], *, server_name: str) -> dict[str, str]:
    snake_case = payload.pop("select_tools", None)
    camel_case = payload.pop("selectTools", None)
    raw = snake_case if snake_case is not None else camel_case
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"mcp server '{server_name}' select_tools must be an object")

    select_tools: dict[str, str] = {}
    for selector_name, tool_name in raw.items():
        if not isinstance(selector_name, str) or not selector_name.strip():
            raise ValueError(f"mcp server '{server_name}' select_tools keys must be non-empty strings")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(
                f"mcp server '{server_name}' select_tools values must be non-empty strings"
            )
        select_tools[selector_name.strip()] = tool_name.strip()
    return select_tools


def build_mcp_server_configs(
    *,
    raw_config: dict[str, Any] | None = None,
    config_dir: str | Path | None = None,
    default_timeout_seconds: float = 10.0,
    placeholders: Mapping[str, str] | None = None,
) -> list[MCPServerConfig]:
    """Build runtime server configs from standard MCP JSON objects or directories.

    ``placeholders`` maps ``${key}`` tokens to values supplied by the owning
    plugin's validated config (for example ``config.xxx.api_key``). An unknown
    token fails instead of sending a literal secret reference to the server.
    """
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

        payload = _normalize_server_payload(name, _expand_placeholders(raw_payload, placeholders or {}))
        enabled = _coerce_bool(payload.pop("enabled", True), default=True)
        if "disabled" in payload:
            enabled = not _coerce_bool(payload.pop("disabled"), default=False)
        select_tools = _read_select_tools(payload, server_name=name)
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
                select_tools=select_tools,
                source_type=infer_source_type(source),
            )
        )
    return server_configs


class MCPToolGateway:
    """Discover MCP tools and execute them via a FastMCP client."""

    def __init__(
        self,
        *,
        servers: list[MCPServerConfig] | None = None,
        client_manager: MCPClientManager | None = None,
        dispatcher: MCPDispatcher | None = None,
        normalizers: Mapping[str, MCPResultNormalizer] | None = None,
        selectors: Mapping[str, MCPToolSelector] | None = None,
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
        self._normalizers = dict(normalizers or {})
        self._selectors = dict(selectors or {})

    @property
    def enabled(self) -> bool:
        return any(config.enabled for config in self._servers.values())

    async def refresh(self) -> None:
        """Refresh all configured MCP tool definitions."""
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
            state.selected_tools = {}
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
                state.selected_tools = self._select_tools(config, discovered)
                state.discovered = True
                logger.info(
                    "mcp.discovery server_ref=%s source_type=%s tool_count=%s selected_tool_count=%s",
                    log_ref(server_name),
                    log_public_label(state.source_type, _PUBLIC_SOURCE_TYPES),
                    len(state.available_tools),
                    len(state.selected_tools),
                )
            except Exception as exc:  # pragma: no cover - exercised by integration/network failures
                state.last_error = str(exc)
                logger.warning(
                    "mcp.discovery.failed server_ref=%s source_type=%s exception_type=%s",
                    log_ref(server_name),
                    log_public_label(state.source_type, _PUBLIC_SOURCE_TYPES),
                    type(exc).__name__,
                )

        self._tools = descriptors
        self._discovered_once = True

    def _select_tools(
        self,
        config: MCPServerConfig,
        descriptors: list[MCPToolDescriptor],
    ) -> dict[str, str]:
        selected: dict[str, str] = {}
        for selector_name, selector in self._selectors.items():
            tool_name = self._resolve_explicit_tool(
                config,
                descriptors,
                config.select_tools.get(selector_name),
            )
            if tool_name is None:
                try:
                    candidate = selector.select(server=config.name, descriptors=tuple(descriptors))
                except Exception as exc:
                    logger.warning(
                        "mcp.selection.failed server_ref=%s selector_ref=%s exception_type=%s",
                        log_ref(config.name),
                        log_ref(selector_name),
                        type(exc).__name__,
                    )
                    continue
                tool_name = candidate if isinstance(candidate, str) else None
                if not any(item.local_name == tool_name for item in descriptors):
                    tool_name = None
            if tool_name is not None:
                selected[selector_name] = tool_name
        return selected

    def _resolve_explicit_tool(
        self,
        config: MCPServerConfig,
        descriptors: list[MCPToolDescriptor],
        value: str | None,
    ) -> str | None:
        preferred = (value or "").strip()
        if not preferred:
            return None
        if preferred.startswith(f"{MCP_TOOL_PREFIX}{config.name}__"):
            return preferred if any(item.local_name == preferred for item in descriptors) else None
        local_name = local_tool_name(config.name, preferred)
        return local_name if any(item.remote_name == preferred for item in descriptors) else None

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
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                capabilities=("mcp", descriptor.server_name),
                # Remote tools stay exclusive until MCP read-only annotations
                # (readOnlyHint) are mapped to execution_mode.
                execution_mode="exclusive",
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

    def tools_for_server(self, server_name: str) -> tuple[MCPToolDescriptor, ...]:
        """Discovered tools for one server, sorted by local name."""
        return tuple(sorted(
            (item for item in self._tools.values() if item.server_name == server_name),
            key=lambda item: item.local_name,
        ))

    def configured_tool(self, selector_name: str, *, server_name: str) -> str | None:
        """The explicitly configured tool for a selector, resolved to a local name."""
        config = self._servers.get(server_name)
        if config is None:
            return None
        return self._resolve_explicit_tool(
            config,
            list(self._tools.values()),
            config.select_tools.get(selector_name),
        )

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
        result = await self._dispatcher.execute(
            config=config,
            descriptor=descriptor,
            raw_arguments=raw_arguments,
        )
        return self._normalize(descriptor, raw_arguments, result)

    def _normalize(
        self,
        descriptor: MCPToolDescriptor,
        arguments: dict[str, Any],
        result: MCPExecutionResult,
    ) -> MCPExecutionResult:
        normalizer = self._normalizers.get(descriptor.server_name)
        if normalizer is None:
            return result
        try:
            normalized = normalizer.normalize(
                descriptor=descriptor,
                arguments=arguments,
                output=result.output,
            )
        except Exception as exc:
            logger.warning(
                "mcp.normalize.failed tool_ref=%s exception_type=%s",
                log_ref(descriptor.local_name),
                type(exc).__name__,
            )
            return result
        if not isinstance(normalized, dict):
            logger.warning("mcp.normalize.invalid tool_ref=%s", log_ref(descriptor.local_name))
            return result
        return replace(result, output=normalized)

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "discovered_tool_count": len(self._tools),
            "servers": {
                name: {
                    "enabled": state.enabled,
                    "configured": state.configured,
                    "discovered": state.discovered,
                    "available_tools": list(state.available_tools),
                    "selected_tools": dict(state.selected_tools),
                    "last_error": state.last_error,
                    "last_discovery_at": state.last_discovery_at,
                    "url": state.url,
                    "source_type": state.source_type,
                }
                for name, state in sorted(self._states.items())
            },
        }
