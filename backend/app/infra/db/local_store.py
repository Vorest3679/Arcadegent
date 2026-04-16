"""Data layer: local JSONL-backed read store for fast query and region lookups."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class LoadStats:
    """Basic diagnostics collected while loading source JSONL."""

    total_lines: int
    loaded_rows: int
    bad_lines: int


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_title(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _as_int(raw.get("id")),
        "title_id": raw.get("title_id"),
        "title_name": raw.get("title_name"),
        "quantity": _as_int(raw.get("quantity")),
        "version": raw.get("version"),
        "coin": raw.get("coin"),
        "eacoin": raw.get("eacoin"),
        "comment": raw.get("comment"),
    }


def _build_search_blob(shop: dict[str, Any]) -> str:
    chunks: list[str] = [
        str(shop.get("name") or ""),
        str(shop.get("name_pinyin") or ""),
        str(shop.get("address") or ""),
        str(shop.get("transport") or ""),
        str(shop.get("comment") or ""),
        str(shop.get("province_name") or ""),
        str(shop.get("city_name") or ""),
        str(shop.get("county_name") or ""),
        str(shop.get("province_code") or ""),
        str(shop.get("city_code") or ""),
        str(shop.get("county_code") or ""),
    ]
    for item in shop.get("arcades", []):
        chunks.append(str(item.get("title_name") or ""))
        chunks.append(str(item.get("version") or ""))
        chunks.append(str(item.get("comment") or ""))
    return " ".join(chunks).lower()


def _keyword_terms(keyword: str | None) -> list[str]:
    if not keyword:
        return []
    normalized = keyword.strip().lower()
    if not normalized:
        return []
    parts = [
        term.strip()
        for term in re.split(r"[\s,.;!?|/\\，。；！？、]+", normalized)
        if term.strip()
    ]
    if not parts:
        return []
    # Keep term order while deduplicating to preserve matching signal.
    deduped = list(dict.fromkeys(parts))
    return deduped


_SORT_BY_VALUES = {"default", "updated_at", "source_id", "arcade_count", "title_quantity", "distance"}
_SORT_ORDER_VALUES = {"asc", "desc"}
_COORD_SYSTEM_VALUES = {"wgs84", "gcj02"}


def _normalize_title_name(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[\s_\-./]+", "", text)
    if "舞萌" in text or text.startswith("maimai"):
        return "maimai"
    if text.startswith("soundvoltex") or text == "sdvx":
        return "sdvx"
    return text


def _title_quantity(row: dict[str, Any], title_name_norm: str) -> int:
    if not title_name_norm:
        return 0
    total = 0
    for item in row.get("arcades") or []:
        if not isinstance(item, dict):
            continue
        if _normalize_title_name(item.get("title_name")) != title_name_norm:
            continue
        total += int(_as_int(item.get("quantity")) or 0)
    return total


def _valid_lng_lat(lng: float | None, lat: float | None) -> bool:
    return lng is not None and lat is not None and -180 <= lng <= 180 and -90 <= lat <= 90


def _haversine_meters(origin_lng: float, origin_lat: float, dest_lng: float, dest_lat: float) -> float:
    radius_m = 6371000.0
    lat1 = math.radians(origin_lat)
    lat2 = math.radians(dest_lat)
    d_lat = lat2 - lat1
    d_lng = math.radians(dest_lng - origin_lng)
    x = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(x), math.sqrt(max(1e-12, 1 - x)))
    return radius_m * c


def _row_coordinates(row: dict[str, Any], coord_system: str) -> tuple[float, float] | None:
    preferred = coord_system if coord_system in _COORD_SYSTEM_VALUES else "wgs84"
    fallbacks = (preferred, "gcj02" if preferred == "wgs84" else "wgs84")
    for system in fallbacks:
        lng = _as_float(row.get(f"longitude_{system}"))
        lat = _as_float(row.get(f"latitude_{system}"))
        if _valid_lng_lat(lng, lat):
            return lng, lat
    return None


def _distance_sorted_shops(
    items: list[dict[str, Any]],
    *,
    sort_order: Literal["asc", "desc"] | str,
    origin_lng: float | None,
    origin_lat: float | None,
    origin_coord_system: str | None,
) -> list[dict[str, Any]]:
    if not _valid_lng_lat(origin_lng, origin_lat):
        return items

    coord_system = (origin_coord_system or "wgs84").strip().lower()
    if coord_system not in _COORD_SYSTEM_VALUES:
        coord_system = "wgs84"
    reverse_distance = (sort_order or "asc").strip().lower() == "desc"

    decorated: list[tuple[float | None, dict[str, Any]]] = []
    for row in items:
        coords = _row_coordinates(row, coord_system)
        if coords is None:
            decorated.append((None, row))
            continue
        distance_m = _haversine_meters(origin_lng, origin_lat, coords[0], coords[1])
        payload = dict(row)
        payload["distance_m"] = int(round(distance_m))
        decorated.append((distance_m, payload))

    def sort_key(item: tuple[float | None, dict[str, Any]]) -> tuple[bool, float, int]:
        distance_m, row = item
        if distance_m is None:
            distance_value = math.inf
        else:
            distance_value = -distance_m if reverse_distance else distance_m
        return (
            distance_m is None,
            distance_value,
            int(row.get("source_id") or 0),
        )

    return [row for _, row in sorted(decorated, key=sort_key)]


def _sort_shops(
    items: list[dict[str, Any]],
    *,
    sort_by: str,
    sort_order: Literal["asc", "desc"] | str,
    sort_title_name: str | None,
    origin_lng: float | None,
    origin_lat: float | None,
    origin_coord_system: str | None,
) -> list[dict[str, Any]]:
    normalized_by = (sort_by or "default").strip().lower()
    if normalized_by not in _SORT_BY_VALUES:
        normalized_by = "default"

    normalized_order = (sort_order or "desc").strip().lower()
    if normalized_order not in _SORT_ORDER_VALUES:
        normalized_order = "desc"
    reverse = normalized_order == "desc"

    if normalized_by == "default":
        return items

    if normalized_by == "distance":
        return _distance_sorted_shops(
            items,
            sort_order=normalized_order,
            origin_lng=origin_lng,
            origin_lat=origin_lat,
            origin_coord_system=origin_coord_system,
        )

    if normalized_by == "title_quantity":
        title_name_norm = _normalize_title_name(sort_title_name)
        if not title_name_norm:
            return items
        return sorted(
            items,
            key=lambda row: (
                _title_quantity(row, title_name_norm),
                str(row.get("updated_at") or ""),
                int(row.get("source_id") or 0),
            ),
            reverse=reverse,
        )

    if normalized_by == "arcade_count":
        return sorted(
            items,
            key=lambda row: (
                int(row.get("arcade_count") or 0),
                str(row.get("updated_at") or ""),
                int(row.get("source_id") or 0),
            ),
            reverse=reverse,
        )

    if normalized_by == "updated_at":
        return sorted(
            items,
            key=lambda row: (
                str(row.get("updated_at") or ""),
                int(row.get("source_id") or 0),
            ),
            reverse=reverse,
        )

    return sorted(
        items,
        key=lambda row: int(row.get("source_id") or 0),
        reverse=reverse,
    )


def _normalize_region_name(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", "", str(value).strip().lower())
    if not text:
        return ""
    suffixes = (
        "特别行政区",
        "自治区",
        "自治州",
        "自治县",
        "地区",
        "省",
        "市",
        "区",
        "县",
        "州",
        "盟",
    )
    changed = True
    while changed and text:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


class LocalArcadeStore:
    """Read-optimized in-memory store built from the configured arcade JSONL."""

    def __init__(self, shops: list[dict[str, Any]], stats: LoadStats) -> None:
        self._shops = shops
        self._stats = stats
        self._by_source_id = {int(item["source_id"]): item for item in shops}
        self._provinces = self._build_province_index(shops)
        self._cities = self._build_city_index(shops)
        self._counties = self._build_county_index(shops)

    @classmethod
    def from_jsonl(cls, path: Path) -> "LocalArcadeStore":
        if not path.exists():
            raise FileNotFoundError(f"Arcade data file not found: {path}")

        shops: list[dict[str, Any]] = []
        bad_lines = 0
        total = 0

        with path.open("r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle, start=1):
                total += 1
                raw_line = line.strip()
                if not raw_line:
                    bad_lines += 1
                    continue
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                normalized = cls._normalize_shop(payload)
                if normalized is None:
                    bad_lines += 1
                    continue
                normalized["_search_blob"] = _build_search_blob(normalized)
                normalized["_load_line"] = idx
                shops.append(normalized)

        with_updated = [row for row in shops if row.get("updated_at")]
        without_updated = [row for row in shops if not row.get("updated_at")]
        with_updated.sort(key=lambda item: (str(item.get("updated_at")), item["source_id"]), reverse=True)
        without_updated.sort(key=lambda item: item["source_id"])
        ordered = with_updated + without_updated

        return cls(
            ordered,
            stats=LoadStats(total_lines=total, loaded_rows=len(ordered), bad_lines=bad_lines),
        )

    @staticmethod
    def _normalize_shop(raw: dict[str, Any]) -> dict[str, Any] | None:
        required = ("source", "source_id", "source_url", "name")
        if any(raw.get(key) in (None, "") for key in required):
            return None

        source_id = _as_int(raw.get("source_id"))
        if source_id is None:
            return None

        raw_arcades = raw.get("arcades", [])
        arcades: list[dict[str, Any]] = []
        if isinstance(raw_arcades, list):
            for item in raw_arcades:
                if isinstance(item, dict):
                    arcades.append(_normalize_title(item))

        result = {
            "source": str(raw.get("source")),
            "source_id": source_id,
            "source_url": str(raw.get("source_url")),
            "name": str(raw.get("name")),
            "name_pinyin": raw.get("name_pinyin"),
            "address": raw.get("address"),
            "transport": raw.get("transport"),
            "url": raw.get("url"),
            "comment": raw.get("comment"),
            "province_code": raw.get("province_code"),
            "province_name": raw.get("province_name"),
            "city_code": raw.get("city_code"),
            "city_name": raw.get("city_name"),
            "county_code": raw.get("county_code"),
            "county_name": raw.get("county_name"),
            "status": raw.get("status"),
            "type": raw.get("type"),
            "pay_type": raw.get("pay_type"),
            "locked": raw.get("locked"),
            "ea_status": raw.get("ea_status"),
            "price": raw.get("price"),
            "start_time": raw.get("start_time"),
            "end_time": raw.get("end_time"),
            "fav_count": _as_int(raw.get("fav_count")),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "option1": raw.get("option1"),
            "option2": raw.get("option2"),
            "option3": raw.get("option3"),
            "option4": raw.get("option4"),
            "option5": raw.get("option5"),
            "collab": raw.get("collab"),
            "image_thumb": raw.get("image_thumb"),
            "events": raw.get("events") if isinstance(raw.get("events"), list) else [],
            "arcades": arcades,
            "arcade_count": len(arcades),
            "longitude_gcj02": raw.get("longitude_gcj02"),
            "latitude_gcj02": raw.get("latitude_gcj02"),
            "longitude_wgs84": raw.get("longitude_wgs84"),
            "latitude_wgs84": raw.get("latitude_wgs84"),
            "raw": raw,
        }
        return result

    @staticmethod
    def _build_province_index(shops: list[dict[str, Any]]) -> list[dict[str, str]]:
        mapping: dict[str, str] = {}
        for row in shops:
            code = row.get("province_code")
            name = row.get("province_name")
            if code and name:
                mapping[str(code)] = str(name)
        return [{"code": k, "name": mapping[k]} for k in sorted(mapping.keys())]

    @staticmethod
    def _build_city_index(shops: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        by_province: dict[str, dict[str, str]] = {}
        for row in shops:
            p_code = row.get("province_code")
            c_code = row.get("city_code")
            c_name = row.get("city_name")
            if not p_code or not c_code or not c_name:
                continue
            by_province.setdefault(str(p_code), {})[str(c_code)] = str(c_name)
        result: dict[str, list[dict[str, str]]] = {}
        for province_code, entries in by_province.items():
            result[province_code] = [{"code": k, "name": entries[k]} for k in sorted(entries.keys())]
        return result

    @staticmethod
    def _build_county_index(shops: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        by_city: dict[str, dict[str, str]] = {}
        for row in shops:
            c_code = row.get("city_code")
            ct_code = row.get("county_code")
            ct_name = row.get("county_name")
            if not c_code or not ct_code or not ct_name:
                continue
            by_city.setdefault(str(c_code), {})[str(ct_code)] = str(ct_name)
        result: dict[str, list[dict[str, str]]] = {}
        for city_code, entries in by_city.items():
            result[city_code] = [{"code": k, "name": entries[k]} for k in sorted(entries.keys())]
        return result

    def health(self) -> dict[str, Any]:
        """Expose basic load/quality stats for health endpoint."""
        return {
            "backend": "jsonl",
            "total_lines": self._stats.total_lines,
            "loaded_rows": self._stats.loaded_rows,
            "bad_lines": self._stats.bad_lines,
        }

    def list_shops(
        self,
        *,
        keyword: str | None,
        province_code: str | None,
        city_code: str | None,
        county_code: str | None,
        has_arcades: bool | None,
        page: int,
        page_size: int,
        province_name: str | None = None,
        city_name: str | None = None,
        county_name: str | None = None,
        sort_by: str = "default",
        sort_order: Literal["asc", "desc"] | str = "desc",
        sort_title_name: str | None = None,
        origin_lng: float | None = None,
        origin_lat: float | None = None,
        origin_coord_system: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filter and paginate shop list with deterministic order."""
        items: list[dict[str, Any]] = []
        terms = _keyword_terms(keyword)
        province_name_norm = _normalize_region_name(province_name)
        city_name_norm = _normalize_region_name(city_name)
        county_name_norm = _normalize_region_name(county_name)
        for row in self._shops:
            if province_code and str(row.get("province_code") or "") != province_code:
                continue
            if city_code and str(row.get("city_code") or "") != city_code:
                continue
            if county_code and str(row.get("county_code") or "") != county_code:
                continue
            if province_name_norm and province_name_norm != _normalize_region_name(
                str(row.get("province_name") or "")
            ):
                continue
            if city_name_norm and city_name_norm != _normalize_region_name(
                str(row.get("city_name") or "")
            ):
                continue
            if county_name_norm and county_name_norm != _normalize_region_name(
                str(row.get("county_name") or "")
            ):
                continue
            if has_arcades is True and row.get("arcade_count", 0) <= 0:
                continue
            if has_arcades is False and row.get("arcade_count", 0) > 0:
                continue
            search_blob = str(row.get("_search_blob") or "")
            if terms and not all(term in search_blob for term in terms):
                continue
            items.append(row)

        items = _sort_shops(
            items,
            sort_by=sort_by,
            sort_order=sort_order,
            sort_title_name=sort_title_name,
            origin_lng=_as_float(origin_lng),
            origin_lat=_as_float(origin_lat),
            origin_coord_system=origin_coord_system,
        )
        total = len(items)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return items[start:end], total

    def get_shop(self, source_id: int) -> dict[str, Any] | None:
        """Fetch one shop by source_id."""
        return self._by_source_id.get(source_id)

    def list_provinces(self) -> list[dict[str, str]]:
        """Return all provinces sorted by code."""
        return self._provinces

    def list_cities(self, province_code: str) -> list[dict[str, str]]:
        """Return city list under one province code."""
        return self._cities.get(province_code, [])

    def list_counties(self, city_code: str) -> list[dict[str, str]]:
        """Return county list under one city code."""
        return self._counties.get(city_code, [])
