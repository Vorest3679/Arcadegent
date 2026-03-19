"""FastMCP client lifecycle helpers used by the MCP tool gateway."""
"""
这里是一些FastMCP客户端的生命周期管理工具，供MCP工具网关使用。
核心功能就包括两个：列出工具和调用工具，支持同步或者异步调用（内部会自动根据当前线程环境选择合适的方式）。
通过这个manager，我们就可以把FastMCP的异步接口包装成同步接口，方便在不支持异步的环境中使用。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable, TypeVar

from app.agent.tools.mcp.models import MCPServerConfig

_T = TypeVar("_T")


def _run_async(factory: Callable[[], Awaitable[_T]]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except Exception as exc:  # pragma: no cover - defensive bridge path
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    error = box.get("error")
    if error is not None:
        raise error
    return box["value"]


class MCPClientManager:
    """Small sync bridge around FastMCP's async client."""

    def list_tools(self, config: MCPServerConfig) -> list[Any]:
        return _run_async(lambda: self._list_tools_async(config))

    def call_tool(
        self,
        *,
        config: MCPServerConfig,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        return _run_async(
            lambda: self._call_tool_async(
                config=config,
                remote_name=remote_name,
                arguments=arguments,
            )
        )

    async def _list_tools_async(self, config: MCPServerConfig) -> list[Any]:
        from fastmcp import Client

        async with Client(
            config.source,
            timeout=config.timeout_seconds,
            init_timeout=config.timeout_seconds,
        ) as client:
            return await client.list_tools()

    async def _call_tool_async(
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
