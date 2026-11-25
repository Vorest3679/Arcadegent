"""Arcade route planning over the generic MCP gateway.

This module owns every route-specific MCP decision: which discovered AMap tool
to use, how to build its arguments, how to map its payload onto
``RouteSummaryDto``, and the MCP-first fallback used by ``route_plan_tool``.
The generic gateway only discovers, selects through this module's selector
port, and normalizes through this module's normalizer port.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.agent._logging import get_logger, log_ref
from app.agent.tools.mcp.discovery import coerce_str
from app.agent.tools.mcp.models import MCPToolDescriptor
from app.protocol.messages import GeoPoint, Location, RouteSummaryDto

if TYPE_CHECKING:
    from app.agent.tools.mcp.gateway import MCPToolGateway

logger = get_logger(__name__)

AMAP_SERVER = "amap"
ROUTE_SELECTOR = "route"


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_geo_point(
    *,
    lng: float,
    lat: float,
    coord_system: str,
    source: str,
    precision: str = "approx",
) -> GeoPoint:
    return GeoPoint(
        lng=lng,
        lat=lat,
        coord_system=coord_system,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        precision=precision,  # type: ignore[arg-type]
    )


def _parse_polyline(polyline: str) -> list[GeoPoint]:
    points: list[GeoPoint] = []
    for point in polyline.split(";"):
        raw = point.strip()
        if not raw:
            continue
        lng_lat = raw.split(",")
        if len(lng_lat) != 2:
            continue
        try:
            lng = float(lng_lat[0])
            lat = float(lng_lat[1])
        except ValueError:
            continue
        points.append(
            _coerce_geo_point(
                lng=lng,
                lat=lat,
                coord_system="gcj02",
                source="route",
            )
        )
    return points


def _normalize_polyline(value: Any) -> list[GeoPoint]:
    if isinstance(value, str):
        return _parse_polyline(value)
    if not isinstance(value, list):
        return []
    points: list[GeoPoint] = []
    for item in value:
        if isinstance(item, dict):
            lng = item.get("lng", item.get("lon", item.get("longitude")))
            lat = item.get("lat", item.get("latitude"))
            try:
                if lng is None or lat is None:
                    continue
                points.append(
                    _coerce_geo_point(
                        lng=float(lng),
                        lat=float(lat),
                        coord_system=str(item.get("coord_system") or "gcj02"),
                        source=str(item.get("source") or "route"),
                        precision=str(item.get("precision") or "approx"),
                    )
                )
            except (TypeError, ValueError):
                continue
    return points


def _infer_mode(*, remote_name: str, arguments: dict[str, Any]) -> str:
    mode = coerce_str(arguments.get("mode"))
    if mode in {"walking", "driving"}:
        return mode
    normalized = remote_name.lower()
    if "walk" in normalized or "walking" in normalized:
        return "walking"
    if "drive" in normalized or "driving" in normalized:
        return "driving"
    return "walking"


def _fallback_point(arguments: dict[str, Any], key: str) -> GeoPoint | None:
    raw = arguments.get(key)
    if not isinstance(raw, dict):
        return None
    lng = raw.get("lng")
    lat = raw.get("lat")
    try:
        if lng is None or lat is None:
            return None
        if key == "origin":
            return _coerce_geo_point(
                lng=float(lng),
                lat=float(lat),
                coord_system=str(raw.get("coord_system") or "wgs84"),
                source=str(raw.get("source") or "client"),
            )
        return _coerce_geo_point(
            lng=float(lng),
            lat=float(lat),
            coord_system=str(raw.get("coord_system") or "gcj02"),
            source=str(raw.get("source") or "route"),
        )
    except (TypeError, ValueError):
        return None


def _extract_route_from_mapping(
    payload: dict[str, Any],
    *,
    remote_name: str,
    raw_arguments: dict[str, Any],
    depth: int,
) -> RouteSummaryDto | None:
    """Build a route summary from the common AMap MCP result shapes."""
    if depth > 2:
        return None

    mode = _infer_mode(remote_name=remote_name, arguments=raw_arguments)

    if "provider" in payload and "mode" in payload:
        try:
            return RouteSummaryDto.model_validate(payload)
        except Exception:
            pass

    distance_m = _coerce_int(payload.get("distance_m", payload.get("distance")))
    duration_s = _coerce_int(payload.get("duration_s", payload.get("duration")))
    polyline = _normalize_polyline(payload.get("polyline"))
    hint = coerce_str(payload.get("hint"))

    if distance_m is not None or duration_s is not None:
        return RouteSummaryDto(
            provider="amap",
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
            origin=_fallback_point(raw_arguments, "origin"),
            destination=_fallback_point(raw_arguments, "destination"),
            polyline=polyline,
            hint=hint,
        )

    route_obj = payload.get("route")
    if isinstance(route_obj, dict):
        route = _extract_route_from_mapping(
            route_obj,
            remote_name=remote_name,
            raw_arguments=raw_arguments,
            depth=depth + 1,
        )
        if route is not None:
            return route

    paths = payload.get("paths")
    if isinstance(paths, list) and paths:
        first = paths[0] if isinstance(paths[0], dict) else None
        if isinstance(first, dict):
            distance_m = _coerce_int(first.get("distance"))
            duration_s = _coerce_int(first.get("duration"))
            points: list[GeoPoint] = []
            steps = first.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    polyline_text = step.get("polyline")
                    if isinstance(polyline_text, str):
                        points.extend(_parse_polyline(polyline_text))
            return RouteSummaryDto(
                provider="amap",
                mode=mode,
                distance_m=distance_m,
                duration_s=duration_s,
                origin=_fallback_point(raw_arguments, "origin"),
                destination=_fallback_point(raw_arguments, "destination"),
                polyline=points,
                hint=hint,
            )

    for nested_key in ("data", "result", "payload"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        route = _extract_route_from_mapping(
            nested,
            remote_name=remote_name,
            raw_arguments=raw_arguments,
            depth=depth + 1,
        )
        if route is not None:
            return route
    return None


def maybe_extract_route_payload(
    *,
    descriptor: MCPToolDescriptor,
    raw_arguments: dict[str, Any],
    structured_content: Any,
    data: Any,
) -> RouteSummaryDto | None:
    candidates: list[Any] = []
    if isinstance(structured_content, dict):
        candidates.append(structured_content)
    if isinstance(data, dict) and data is not structured_content:
        candidates.append(data)
    for candidate in candidates:
        route = _extract_route_from_mapping(
            candidate,
            remote_name=descriptor.remote_name,
            raw_arguments=raw_arguments,
            depth=0,
        )
        if route is not None:
            return route
    return None


def _descriptor_text(descriptor: MCPToolDescriptor) -> str:
    return " ".join(
        [
            descriptor.remote_name.lower(),
            descriptor.local_name.lower(),
            descriptor.description.lower(),
            json.dumps(descriptor.input_schema, ensure_ascii=False).lower(),
        ]
    )


def _mode_compatible(descriptor: MCPToolDescriptor, mode: str) -> bool:
    text = _descriptor_text(descriptor)
    has_walking = "walk" in text or "walking" in text or "步行" in text
    has_driving = "drive" in text or "driving" in text or "驾车" in text or "开车" in text
    if mode == "walking" and has_driving and not has_walking:
        return False
    if mode == "driving" and has_walking and not has_driving:
        return False
    return True


class AMapRouteToolSelector:
    """Select the AMap route tool that fits the requested travel mode."""

    def select(
        self,
        *,
        server: str,
        descriptors: Sequence[MCPToolDescriptor],
    ) -> str | None:
        """Default route tool for health and explicit lookup (no travel mode)."""
        if server != AMAP_SERVER:
            return None
        ranked: list[tuple[int, str]] = []
        for descriptor in descriptors:
            if descriptor.server_name != server:
                continue
            text = _descriptor_text(descriptor)
            score = 0
            if "route" in text or "direction" in text or "路径" in text or "路线" in text or "导航" in text:
                score += 4
            if "walking" in text or "driving" in text or "walk" in text or "drive" in text:
                score += 2
            if "origin" in text or "destination" in text or "起点" in text or "终点" in text:
                score += 1
            if score > 0:
                ranked.append((score, descriptor.local_name))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked[0][1]

    def select_for_mode(
        self,
        *,
        server: str,
        descriptors: Sequence[MCPToolDescriptor],
        mode: str,
        preferred: str | None = None,
    ) -> MCPToolDescriptor | None:
        """Route tool for one travel mode; an explicit preference wins if compatible."""
        candidates = [item for item in descriptors if item.server_name == server]
        if preferred:
            chosen = next((item for item in candidates if item.local_name == preferred), None)
            if chosen is not None:
                return chosen if _mode_compatible(chosen, mode) else None

        ranked: list[tuple[int, MCPToolDescriptor]] = []
        for descriptor in candidates:
            if not _mode_compatible(descriptor, mode):
                continue
            text = _descriptor_text(descriptor)
            has_walking = "walk" in text or "walking" in text or "步行" in text
            has_driving = "drive" in text or "driving" in text or "驾车" in text or "开车" in text
            score = 0
            if "route" in text or "direction" in text or "路径" in text or "路线" in text or "导航" in text:
                score += 4
            if mode == "walking" and has_walking:
                score += 3
            if mode == "driving" and has_driving:
                score += 3
            if "origin" in text and "destination" in text:
                score += 1
            if score > 0:
                ranked.append((score, descriptor))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1].local_name))
        return ranked[0][1]


def build_route_arguments(
    *,
    descriptor: MCPToolDescriptor,
    origin: Location,
    destination: Location,
    mode: str,
) -> dict[str, Any]:
    properties = descriptor.input_schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    origin_string = f"{origin.lng},{origin.lat}"
    destination_string = f"{destination.lng},{destination.lat}"
    origin_object = {"lng": origin.lng, "lat": origin.lat}
    destination_object = {"lng": destination.lng, "lat": destination.lat}

    def _match_value(name: str) -> Any:
        schema = properties.get(name)
        schema_type = schema.get("type") if isinstance(schema, dict) else None
        if name in {"origin", "from", "start"}:
            return origin_object if schema_type == "object" else origin_string
        if name in {"destination", "to", "end"}:
            return destination_object if schema_type == "object" else destination_string
        if name == "mode":
            return mode
        return None

    arguments: dict[str, Any] = {}
    for key in properties:
        value = _match_value(key)
        if value is not None:
            arguments[key] = value

    if not arguments:
        arguments = {
            "origin": origin_string,
            "destination": destination_string,
        }
    return arguments


class AMapRouteNormalizer:
    """Map AMap MCP payloads onto the ``route`` field used by the arcade domain."""

    def normalize(
        self,
        *,
        descriptor: MCPToolDescriptor,
        arguments: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        route = maybe_extract_route_payload(
            descriptor=descriptor,
            raw_arguments=arguments,
            structured_content=output.get("structured_content"),
            data=output.get("data"),
        )
        if route is None:
            return output
        normalized = dict(output)
        normalized["route"] = route.model_dump(mode="json")
        return normalized


AMAP_ROUTE_SELECTOR = AMapRouteToolSelector()
AMAP_ROUTE_NORMALIZER = AMapRouteNormalizer()


async def plan_amap_route(
    gateway: MCPToolGateway,
    *,
    mode: str,
    origin: Location,
    destination: Location,
) -> RouteSummaryDto | None:
    """Use a discovered AMap MCP route tool when one is available."""
    await gateway.ensure_ready()
    descriptor = AMAP_ROUTE_SELECTOR.select_for_mode(
        server=AMAP_SERVER,
        descriptors=gateway.tools_for_server(AMAP_SERVER),
        mode=mode,
        preferred=gateway.configured_tool(ROUTE_SELECTOR, server_name=AMAP_SERVER),
    )
    if descriptor is None:
        return None
    arguments = build_route_arguments(
        descriptor=descriptor,
        origin=origin,
        destination=destination,
        mode=mode,
    )
    result = await gateway.execute(tool_name=descriptor.local_name, raw_arguments=arguments)
    if result.status != "completed":
        logger.warning(
            "mcp_route.plan.failed tool_ref=%s has_error=%s",
            log_ref(descriptor.local_name),
            bool(result.error_message),
        )
        return None
    route_payload = result.output.get("route")
    if not isinstance(route_payload, dict):
        return None
    try:
        route = RouteSummaryDto.model_validate(route_payload)
    except Exception:
        return None
    updates: dict[str, object] = {}
    if route.origin is None:
        updates["origin"] = GeoPoint(
            lng=origin.lng,
            lat=origin.lat,
            coord_system="gcj02",
            source="client",
            precision="approx",
        )
    if route.destination is None:
        updates["destination"] = GeoPoint(
            lng=destination.lng,
            lat=destination.lat,
            coord_system="gcj02",
            source="route",
            precision="approx",
        )
    return route.model_copy(update=updates) if updates else route


__all__ = [
    "AMAP_ROUTE_NORMALIZER",
    "AMAP_ROUTE_SELECTOR",
    "AMAP_SERVER",
    "AMapRouteNormalizer",
    "AMapRouteToolSelector",
    "ROUTE_SELECTOR",
    "build_route_arguments",
    "maybe_extract_route_payload",
    "plan_amap_route",
]
