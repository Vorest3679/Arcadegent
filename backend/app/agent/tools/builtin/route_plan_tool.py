"""Tool layer: route plan via AMap API with offline fallback."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from urllib import parse

import httpx

from app.protocol.messages import Location, ProviderType, RouteSummaryDto


def _haversine_meters(a: Location, b: Location) -> float:
    """计算两点间的直线距离（米）"""
    radius_m = 6371000.0
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    d_lat = lat2 - lat1
    d_lng = math.radians(b.lng - a.lng)
    x = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(x), math.sqrt(max(1e-12, 1 - x)))
    return radius_m * c


@dataclass(frozen=True)
class AMapConfig:
    """Runtime config for AMap web service calls."""

    api_key: str
    base_url: str
    timeout_seconds: float

def _parse_polyline(polyline: str) -> list[Location]:
    """解析高德地图API返回的polyline字符串为坐标列表。"""
    result: list[Location] = []
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
        result.append(Location(lng=lng, lat=lat))
    return result


class RoutePlanTool:
    """Route planner using AMap first (if configured), then fallback estimation."""

    def __init__(self, amap_config: AMapConfig | None = None) -> None:
        self._amap_config = amap_config

    async def _plan_with_amap(
        self,
        *,
        mode: str,
        origin: Location,
        destination: Location,
    ) -> RouteSummaryDto | None:
        """用高德地图API规划路线，失败时返回None以触发离线估算。"""
        if not self._amap_config or not self._amap_config.api_key.strip():
            return None

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
        except (httpx.HTTPError, TimeoutError):
            return None

        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None

        route_obj = payload.get("route") if isinstance(payload, dict) else None
        paths = route_obj.get("paths") if isinstance(route_obj, dict) else None
        if not isinstance(paths, list) or not paths:
            return None

        first = paths[0] if isinstance(paths[0], dict) else {}
        try:
            distance_m = int(float(first.get("distance", 0)))
            duration_s = int(float(first.get("duration", 0)))
        except (TypeError, ValueError):
            return None

        points: list[Location] = []
        steps = first.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_polyline = step.get("polyline")
                if isinstance(step_polyline, str):
                    points.extend(_parse_polyline(step_polyline))
        if not points:
            points = [origin, destination]

        return RouteSummaryDto(
            provider="amap",
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
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
        """规划路线，优先使用高德地图API，失败时返回离线估算结果。"""
        amap_result = None
        if provider == "amap":
            amap_result = await self._plan_with_amap(
                mode=mode,
                origin=origin,
                destination=destination,
            )
        if amap_result:
            return amap_result

        distance_m = int(_haversine_meters(origin, destination))
        speed = 9.0 if mode == "driving" else 1.3
        duration_s = int(distance_m / speed) if distance_m > 0 else 0
        hint = "AMap route API unavailable; returned an offline estimate."
        return RouteSummaryDto(
            provider=provider,
            mode=mode,
            distance_m=distance_m,
            duration_s=duration_s,
            polyline=[origin, destination],
            hint=hint,
        )
