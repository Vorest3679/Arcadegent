"""Executor for the builtin route planning tool."""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.provider import BuiltinToolContext
from app.protocol.messages import Location


async def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Prefer MCP-based AMap routing when available, otherwise use local fallback."""
    tool = context.require("route_plan_tool")
    origin = Location.model_validate(args["origin"])
    destination = Location.model_validate(args["destination"])

    route = None
    mcp_tool_gateway = context.get("mcp_tool_gateway")
    if args["provider"] == "amap" and mcp_tool_gateway is not None:
        route = await mcp_tool_gateway.plan_amap_route(
            mode=args["mode"],
            origin=origin,
            destination=destination,
        )
    if route is None:
        route = await tool.plan_route(
            provider=args["provider"],
            mode=args["mode"],
            origin=origin,
            destination=destination,
        )
    return {"route": route.model_dump(mode="json")}
