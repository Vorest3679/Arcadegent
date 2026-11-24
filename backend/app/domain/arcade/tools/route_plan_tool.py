"""Tool layer: route plan via AMap API using online routing only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import parse

import httpx

from app.protocol.messages import GeoPoint, Location, ProviderType, RouteSummaryDto


@dataclass(frozen=True)
class AMapConfig:
    """Runtime config for AMap web service calls."""

    api_key: str
    base_url: str
    timeout_seconds: float

def _route_point_from_location(location: Location, *, source: str) -> GeoPoint:
    return GeoPoint(
        lng=location.lng,
        lat=location.lat,
        coord_system="gcj02",
        source=source,  # type: ignore[arg-type]
        precision="approx",
    )


def _parse_polyline(polyline: str) -> list[GeoPoint]:
    """解析高德地图API返回的polyline字符串为坐标列表。"""
    result: list[GeoPoint] = []
    if not polyline:
        return result
    for point in polyline.split(";"):
        raw = point.strip()
        if not raw:
            continue
        parts = raw.split(",")
        if len(parts) != 2:
            continue
        try:
            lng = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        result.append(
            GeoPoint(
                lng=lng,
                lat=lat,
                coord_system="gcj02",
                source="route",
                precision="approx",
            )
        )
    return result


class RoutePlanTool:
    """Route planner requiring an online route result."""

    def __init__(self, amap_config: AMapConfig | None = None) -> None:
        self._amap_config = amap_config

    async def _plan_with_amap(
        self,
        *,
        mode: str,
        origin: Location,
        destination: Location,
    ) -> RouteSummaryDto | None:
        """用高德地图API规划路线，失败时返回 None。"""
        if not self._amap_config or not self._amap_config.api_key.strip():
            raise RuntimeError("route_unavailable: amap_key_missing")

        endpoint = "/v3/direction/driving" if mode == "driving" else "/v3/direction/walking"
        query = parse.urlencode(
            {
                "key": self._amap_config.api_key,
                "origin": f"{origin.lng},{origin.lat}",
                "destination": f"{destination.lng},{destination.lat}",
            }
        )
        url = self._amap_config.base_url.rstrip("/") + endpoint + "?" + query
        try:
            async with httpx.AsyncClient(timeout=self._amap_config.timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"route_unavailable: amap_http_{exc.response.status_code}") from None
        except (httpx.HTTPError, TimeoutError) as exc:
            # HTTP exception messages may contain the URL and its API key.
            raise RuntimeError(f"route_unavailable: amap_transport_{type(exc).__name__}") from None

        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise RuntimeError("route_unavailable: amap_invalid_json") from None

        if isinstance(payload, dict) and str(payload.get("status", "1")) != "1":
            code = str(payload.get("infocode", ""))
            safe_code = code if code.isdigit() and len(code) <= 8 else "unknown"
            raise RuntimeError(f"route_unavailable: amap_api_error_{safe_code}")

        route_obj = payload.get("route") if isinstance(payload, dict) else None
        paths = route_obj.get("paths") if isinstance(route_obj, dict) else None
        if not isinstance(paths, list) or not paths:
            return None

        first = paths[0] if isinstance(paths[0], dict) else {}
        try:
            distance_m = int(float(first.get("distance")))
            duration_s = int(float(first.get("duration")))
        except (TypeError, ValueError):
            return None

        points: list[GeoPoint] = []
        steps = first.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_polyline = step.get("polyline")
                if isinstance(step_polyline, str):
                    points.extend(_parse_polyline(step_polyline))

        return RouteSummaryDto(
            provider="amap",
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
            origin=_route_point_from_location(origin, source="client"),
            destination=_route_point_from_location(destination, source="route"),
            polyline=points,
            hint=None,
        )

    async def plan_route(
        self,
        *,
        provider: ProviderType,
        mode: str,
        origin: Location,
        destination: Location,
    ) -> RouteSummaryDto:
        """规划路线，优先使用高德地图API，失败时报告路线不可用。"""
        amap_result = None
        if provider == "amap":
            amap_result = await self._plan_with_amap(
                mode=mode,
                origin=origin,
                destination=destination,
            )
        if amap_result:
            return amap_result

        raise RuntimeError("route_unavailable: online route service unavailable")
