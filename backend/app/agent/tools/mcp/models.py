"""Shared models and constants for MCP-backed tools."""
"""这里存放一些MCP工具相关的模型和常量定义（可以类比成interface），供discovery、gateway、client_manager等模块使用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MCP_TOOL_PREFIX = "mcp__"
MCP_TOOL_WILDCARD = "mcp__*"


@dataclass(frozen=True)
class MCPServerConfig:
    """Runtime connection config for one MCP server."""

    name: str
    enabled: bool
    source: Any
    url: str | None = None
    timeout_seconds: float = 10.0
    route_tool_name: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class MCPToolDescriptor:
    """Discovered MCP tool projected into the local tool registry."""

    server_name: str
    remote_name: str
    local_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


@dataclass
class MCPServerState:
    """Health/debug state for one configured MCP server."""

    enabled: bool
    configured: bool
    discovered: bool = False
    available_tools: list[str] = field(default_factory=list)
    selected_route_tool: str | None = None
    last_error: str | None = None
    last_discovery_at: str | None = None
    url: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class MCPExecutionResult:
    """Normalized result returned by the MCP gateway."""

    status: str
    output: dict[str, Any]
    error_message: str | None = None
