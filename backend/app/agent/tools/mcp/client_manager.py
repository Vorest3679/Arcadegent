"""FastMCP client lifecycle helpers used by the MCP tool gateway."""
from __future__ import annotations

from typing import Any

from app.agent.tools.mcp.models import MCPServerConfig


class MCPClientManager:
    """Small async wrapper around FastMCP's client lifecycle."""

    async def list_tools(self, config: MCPServerConfig) -> list[Any]:
        from fastmcp import Client

        async with Client(
            config.source,
            timeout=config.timeout_seconds,
            init_timeout=config.timeout_seconds,
        ) as client:
            return await client.list_tools()

    async def call_tool(
        self,
        *,
        config: MCPServerConfig,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        from fastmcp import Client

        async with Client(
            config.source,
            timeout=config.timeout_seconds,
            init_timeout=config.timeout_seconds,
        ) as client:
            return await client.call_tool(
                remote_name,
                arguments,
                raise_on_error=False,
            )
