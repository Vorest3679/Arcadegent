"""Executor for the map provider selection tool (``geo_resolve_tool``)."""

from __future__ import annotations

from app.agent.tools.spec import ToolContext


def execute(context: ToolContext, args: dict[str, object]) -> dict[str, str]:
    """Resolve the provider for one province code."""
    tool = context.require("geo_resolve_tool")
    provider = tool.resolve_provider(args.get("province_code"))
    return {"provider": provider}
