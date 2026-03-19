"""Compatibility wrapper for the extracted MCP tool package."""

from app.agent.tools.mcp import (
    MCPExecutionResult,
    MCPServerConfig,
    MCPServerState,
    MCPToolDescriptor,
    MCPToolGateway,
    MCP_TOOL_PREFIX,
    MCP_TOOL_WILDCARD,
    build_amap_mcp_server_config,
)

__all__ = [
    "MCPExecutionResult",
    "MCPServerConfig",
    "MCPServerState",
    "MCPToolDescriptor",
    "MCPToolGateway",
    "MCP_TOOL_PREFIX",
    "MCP_TOOL_WILDCARD",
    "build_amap_mcp_server_config",
]
