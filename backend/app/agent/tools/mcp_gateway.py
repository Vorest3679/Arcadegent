"""Compatibility wrapper for the extracted MCP tool package."""

from app.agent.tools.mcp import (
    MCPExecutionResult,
    MCPServerConfig,
    MCPServerState,
    MCPToolDescriptor,
    MCPToolGateway,
    MCP_TOOL_PREFIX,
    MCP_TOOL_WILDCARD,
    build_mcp_server_configs,
)

__all__ = [
    "MCPExecutionResult",
    "MCPServerConfig",
    "MCPServerState",
    "MCPToolDescriptor",
    "MCPToolGateway",
    "MCP_TOOL_PREFIX",
    "MCP_TOOL_WILDCARD",
    "build_mcp_server_configs",
]
