"""Tool permission checks for runtime execution chain.

An agent may call a tool only when the tool is named in its ``allowed_tools``;
MCP tools are also allowed by the ``mcp__*`` wildcard. There is no global
override: every plugin's agents carry their own tool scope.
"""

from __future__ import annotations

from collections.abc import Sequence

_MCP_TOOL_PREFIX = "mcp__"
_MCP_TOOL_WILDCARD = "mcp__*"


class ToolPermissionError(RuntimeError):
    """Raised when a tool call violates the agent's allowed list."""


class ToolPermissionChecker:
    """Validate tool calls against the calling agent's ``allowed_tools``."""

    def ensure_allowed(self, *, tool_name: str, allowed_tools: Sequence[str]) -> None:
        if tool_name in allowed_tools:
            return
        if tool_name.startswith(_MCP_TOOL_PREFIX) and _MCP_TOOL_WILDCARD in allowed_tools:
            return
        raise ToolPermissionError(f"tool_not_allowed:{tool_name}")


__all__ = ["ToolPermissionChecker", "ToolPermissionError"]
