"""Domain ports for MCP tool selection and result normalization.

The generic MCP layer discovers and executes tools. Which discovered tool a
domain prefers, and how a raw tool result maps onto domain objects, are domain
decisions supplied through these protocols. Selectors return a local tool name
(``mcp__<server>__<tool>``), normalizers receive the generic execution payload
and return a replacement payload.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.agent.tools.mcp.models import MCPToolDescriptor


class MCPToolSelector(Protocol):
    """Choose one discovered tool for a named purpose on a server."""

    def select(
        self,
        *,
        server: str,
        descriptors: Sequence[MCPToolDescriptor],
    ) -> str | None:
        """Return the local name of the selected tool, or ``None``."""
        ...


class MCPResultNormalizer(Protocol):
    """Augment or replace a generic MCP execution payload for one server."""

    def normalize(
        self,
        *,
        descriptor: MCPToolDescriptor,
        arguments: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the payload consumed by the runtime and by callers."""
        ...


__all__ = ["MCPResultNormalizer", "MCPToolSelector"]
