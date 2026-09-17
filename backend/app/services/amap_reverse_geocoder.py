"""Service layer: reverse geocode browser coordinates via AMap."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, parse, request

from app.protocol.messages import ReverseGeocodeRequest, ReverseGeocodeResponse
from app.services.coordinate_transform import wgs84_to_gcj02


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _city_or_none(value: object, *, province: str | None) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list) and not value and province and province.endswith("市"):
        return province
    return None


def _region_text(*parts: str | None) -> str | None:
    ordered: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        if part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    if not ordered:
        return None
    return " / ".join(ordered)


@dataclass(frozen=True)
class AMapReverseGeocoderConfig:
    """Runtime config for AMap reverse-geocode web service calls."""

    api_key: str
    base_url: str
    timeout_seconds: float


class AMapReverseGeocoder:
    """Reverse-geocode browser coordinates using AMap when configured."""

    def __init__(self, config: AMapReverseGeocoderConfig | None = None) -> None:
        self._config = config

    def reverse_geocode(self, lookup: ReverseGeocodeRequest) -> ReverseGeocodeResponse:
        """装载浏览器坐标，调用高德逆地理编码API，返回结构化的地址信息供对话上下文使用."""
        base_payload = {
            "lng": lookup.lng,
            "lat": lookup.lat,
            "accuracy_m": lookup.accuracy_m,
        }
        if not self._config or not self._config.api_key.strip():
            return ReverseGeocodeResponse(**base_payload, resolved=False)

        # Browser Geolocation coordinates are WGS84; AMap's REST API requires GCJ-02.
        amap_lng, amap_lat = wgs84_to_gcj02(lookup.lng, lookup.lat)

        query = parse.urlencode(
            {
                "key": self._config.api_key,
                "location": f"{amap_lng},{amap_lat}",
                "extensions": "base",
                "roadlevel": 0,
            }
        )
        url = self._config.base_url.rstrip("/") + "/v3/geocode/regeo?" + query

        # 装载完毕，准备调用高德API
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (error.URLError, error.HTTPError, TimeoutError):
            return ReverseGeocodeResponse(**base_payload, resolved=False)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ReverseGeocodeResponse(**base_payload, resolved=False)

        if not isinstance(payload, dict) or str(payload.get("status") or "") != "1":
            return ReverseGeocodeResponse(**base_payload, resolved=False)

        regeo = payload.get("regeocode")
        address_component = regeo.get("addressComponent") if isinstance(regeo, dict) else None
        if not isinstance(address_component, dict):
            return ReverseGeocodeResponse(**base_payload, resolved=False)

        province = _string_or_none(address_component.get("province"))
        city = _city_or_none(address_component.get("city"), province=province)
        district = _string_or_none(address_component.get("district"))
        township = _string_or_none(address_component.get("township"))
        adcode = _string_or_none(address_component.get("adcode"))
        formatted_address = _string_or_none(regeo.get("formatted_address") if isinstance(regeo, dict) else None)
        region_text = _region_text(province, city, district, township)

        return ReverseGeocodeResponse(
            **base_payload,
            province=province,
            city=city,
            district=district,
            township=township,
            adcode=adcode,
            formatted_address=formatted_address,
            region_text=formatted_address or region_text,
            resolved=bool(formatted_address or region_text),
        )
