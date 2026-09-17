"""Coordinate-system conversions used when calling Chinese map providers."""

from __future__ import annotations

import math

_EARTH_RADIUS = 6378245.0
_EE = 0.00669342162296594323


def _outside_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(lng: float, lat: float) -> float:
    value = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    value += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0


def _transform_lng(lng: float, lat: float) -> float:
    value = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    value += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """Convert browser GPS coordinates to the GCJ-02 system expected by AMap."""
    if _outside_china(lng, lat):
        return lng, lat
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_EARTH_RADIUS * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_EARTH_RADIUS / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lng + d_lng, lat + d_lat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """Approximate the WGS84 point corresponding to a GCJ-02 coordinate."""
    if _outside_china(lng, lat):
        return lng, lat
    converted_lng, converted_lat = wgs84_to_gcj02(lng, lat)
    return lng * 2.0 - converted_lng, lat * 2.0 - converted_lat
