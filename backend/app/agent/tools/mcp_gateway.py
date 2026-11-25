"""Compatibility wrapper for the extracted MCP tool package."""

from app.agent.tools.mcp import (
    MCPExecutionResult,
    MCPResultNormalizer,
    MCPServerConfig,
    MCPServerState,
    MCPToolDescriptor,
    MCPToolSelector,
    MCPToolGateway,
    MCP_TOOL_PREFIX,
    MCP_TOOL_WILDCARD,
    build_mcp_server_configs,
)

__all__ = [
    "MCPExecutionResult",
    "MCPResultNormalizer",
    "MCPServerConfig",
    "MCPServerState",
    "MCPToolDescriptor",
    "MCPToolSelector",
    "MCPToolGateway",
    "MCP_TOOL_PREFIX",
    "MCP_TOOL_WILDCARD",
    "build_mcp_server_configs",
]
