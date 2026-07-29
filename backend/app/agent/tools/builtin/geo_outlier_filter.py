"""Geo outlier filter for arcade query results.

Removes shops whose coordinates are implausibly far from the rest of the
result set (or from a known origin), so artifacts from other provinces do
not leak into city-level answers.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

MAX_DISTANCE_KM = 50.0
_MIN_CLUSTER_SIZE = 3
_EARTH_RADIUS_M = 6371000.0


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_lng_lat(lng: float | None, lat: float | None) -> bool:
    return lng is not None and lat is not None and -180 <= lng <= 180 and -90 <= lat <= 90


def haversine_meters(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lat = lat2_rad - lat1_rad
    d_lng = math.radians(lng2 - lng1)
    x = math.sin(d_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(x), math.sqrt(max(1e-12, 1 - x)))
    return _EARTH_RADIUS_M * c


def row_coordinates(row: dict[str, Any]) -> tuple[float, float] | None:
    """Pick the best available coordinates; gcj02 preferred, wgs84 fallback.

    The gcj02/wgs84 offset is at most a few hundred meters, negligible
    against the 50km outlier threshold.
    """
    for system in ("gcj02", "wgs84"):
        lng = _as_float(row.get(f"longitude_{system}"))
        lat = _as_float(row.get(f"latitude_{system}"))
        if _valid_lng_lat(lng, lat):
            return lng, lat
    return None


def filter_geo_outliers(
    rows: list[dict[str, Any]],
    *,
    origin: tuple[float, float] | None = None,
    max_distance_km: float = MAX_DISTANCE_KM,
    min_cluster_size: int = _MIN_CLUSTER_SIZE,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], float]]]:
    """Split rows into (kept, removed) by geographic plausibility.

    removed entries are (row, distance_km) pairs. Rows without coordinates
    are always kept — they cannot be judged and must not be dropped.

    - origin given (nearby search): drop rows farther than max_distance_km
      from the origin.
    - no origin (cluster mode): compute a median center of the rows with
      coordinates; if the median distance to that center exceeds the
      threshold the result set is geographically dispersed (e.g. a
      nationwide query) and is returned unchanged; otherwise drop rows
      farther than max_distance_km from the center.
    """
    max_distance_m = max_distance_km * 1000.0

    if origin is not None:
        origin_lng, origin_lat = origin
        if not _valid_lng_lat(origin_lng, origin_lat):
            return list(rows), []
        kept: list[dict[str, Any]] = []
        removed: list[tuple[dict[str, Any], float]] = []
        for row in rows:
            coords = row_coordinates(row)
            if coords is None:
                kept.append(row)
                continue
            distance_m = haversine_meters(origin_lng, origin_lat, coords[0], coords[1])
            if distance_m > max_distance_m:
                removed.append((row, distance_m / 1000.0))
            else:
                kept.append(row)
        return kept, removed

    located: list[tuple[dict[str, Any], float, float]] = []
    for row in rows:
        coords = row_coordinates(row)
        if coords is not None:
            located.append((row, coords[0], coords[1]))

    if len(located) < max(1, min_cluster_size):
        return list(rows), []

    center_lng = median(lng for _, lng, _ in located)
    center_lat = median(lat for _, _, lat in located)
    distances = [
        (row, haversine_meters(center_lng, center_lat, lng, lat))
        for row, lng, lat in located
    ]
    median_distance_m = median(distance_m for _, distance_m in distances)
    if median_distance_m > max_distance_m:
        # Geographically dispersed result set (nationwide query) — not a
        # cluster, so nothing can be called an outlier.
        return list(rows), []

    distance_by_id = {id(row): distance_m for row, distance_m in distances}
    kept = []
    removed = []
    for row in rows:
        distance_m = distance_by_id.get(id(row))
        if distance_m is not None and distance_m > max_distance_m:
            removed.append((row, distance_m / 1000.0))
        else:
            kept.append(row)
    return kept, removed
