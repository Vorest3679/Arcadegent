"""Executor for the builtin route planning tool."""

from __future__ import annotations

import re
from typing import Any

from app.agent.tools.builtin.provider import BuiltinToolContext
from app.protocol.messages import Location


def _memory_artifact(memory: dict[str, Any], key: str) -> Any:
    artifacts = memory.get("artifacts")
    if isinstance(artifacts, dict) and key in artifacts:
        return artifacts.get(key)
    return memory.get(key)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _point(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        lng = _number(value.get("lng", value.get("longitude")))
        lat = _number(value.get("lat", value.get("latitude")))
    elif isinstance(value, str):
        match = re.fullmatch(
            r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[,，]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*",
            value,
        )
        if match is None:
            return None
        lng, lat = float(match.group(1)), float(match.group(2))
    else:
        return None
    if lng is None or lat is None or not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return {"lng": lng, "lat": lat}


def _shop_point(shop: dict[str, Any]) -> dict[str, float] | None:
    geo = shop.get("geo")
    if isinstance(geo, dict):
        gcj02 = geo.get("gcj02")
        point = _point(gcj02)
        if point is not None:
            return point
    return _point({"lng": shop.get("longitude_gcj02"), "lat": shop.get("latitude_gcj02")})


def _candidate_shops(memory: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for key in ("search_candidates", "selected_shops", "shops", "shop"):
        value = _memory_artifact(memory, key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict):
                continue
            marker = id(item)
            if marker not in seen:
                seen.add(marker)
                result.append(item)
    return result


def _normalized_name(value: str) -> str:
    return re.sub(r"[\s（）()·•\-—_]", "", value).casefold()


def _named_shop_point(value: Any, memory: dict[str, Any]) -> dict[str, float] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    target = _normalized_name(value)
    matches: list[dict[str, float]] = []
    for shop in _candidate_shops(memory):
        name = shop.get("name")
        if not isinstance(name, str):
            continue
        normalized = _normalized_name(name)
        if normalized not in target and target not in normalized:
            continue
        point = _shop_point(shop)
        if point is not None and point not in matches:
            matches.append(point)
    return matches[0] if len(matches) == 1 else None


def _reuse_previous_endpoints(memory: dict[str, Any]) -> bool:
    request = memory.get("last_request")
    message = str(request.get("message") or "") if isinstance(request, dict) else ""
    return bool(re.search(r"起点终点.*不变|两端.*不变|原(?:来|先).*路线|same (?:origin|start|end|destination)", message, re.IGNORECASE))


def _infer_mode(memory: dict[str, Any]) -> str | None:
    request = memory.get("last_request")
    message = str(request.get("message") or "").lower() if isinstance(request, dict) else ""
    if re.search(r"开车|驾车|自驾|driv", message):
        return "driving"
    if re.search(r"步行|走路|walk", message):
        return "walking"
    return None


def prepare_arguments(raw_arguments: dict[str, Any], runtime_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Bind model-friendly endpoint names/strings to strict route coordinates."""
    args = dict(raw_arguments)
    hydrated: list[str] = []
    previous = runtime_context.get("last_route_endpoints")
    can_reuse = _reuse_previous_endpoints(runtime_context) and isinstance(previous, dict)

    for key in ("origin", "destination"):
        raw = args.get(key)
        resolved = _point(raw) or _named_shop_point(raw, runtime_context)
        if resolved is None and can_reuse:
            resolved = _point(previous.get(key))
        if resolved is not None and resolved != raw:
            args[key] = resolved
            hydrated.append(key)

    if args.get("mode") not in {"walking", "driving"}:
        mode = _infer_mode(runtime_context)
        if mode is not None:
            args["mode"] = mode
            hydrated.append("mode")
    if args.get("provider") not in {"amap", "google", "none"}:
        provider = runtime_context.get("provider")
        if provider in {"amap", "google", "none"}:
            args["provider"] = provider
            hydrated.append("provider")
    return args, hydrated


async def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Prefer MCP-based AMap routing when available, otherwise call the online REST service."""
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
