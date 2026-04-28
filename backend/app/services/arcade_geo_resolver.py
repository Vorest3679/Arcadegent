"""Geo enrichment service for arcade shops with runtime cache and AMap geocoding."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
from urllib import parse

import httpx

from app.protocol.messages import ArcadeGeoDto, GeoPoint


@dataclass(frozen=True)
class ArcadeGeoResolverConfig:
    """Runtime configuration for cache-backed arcade geocoding."""

    api_key: str
    base_url: str
    cache_path: Path
    request_timeout_seconds: float = 1.2
    sync_limit: int = 8
    max_workers: int = 4
    request_interval_seconds: float = 0.0


class ArcadeGeoResolver:
    """Resolve per-shop geo data from catalog fields, cache, then AMap geocoding."""

    def __init__(self, *, config: ArcadeGeoResolverConfig) -> None:
        self._config = config
        self._lock = Lock()
        self._request_lock = Lock()
        self._last_request_at = 0.0
        self._cache = self._load_cache()

    def resolve_one(self, raw: Mapping[str, Any]) -> ArcadeGeoDto | None:
        """Resolve one shop geo payload without raising on failures."""
        direct = self._geo_from_catalog(raw)
        if direct is not None:
            return direct

        key = self._cache_key(raw)
        if key is None:
            return None

        cached = self._geo_from_cache(key)
        if cached is not None:
            return cached

        geocoded = self._geocode(raw)
        if geocoded is not None:
            self._write_cache_entry(key=key, raw=raw, geo=geocoded)
        return geocoded

    def geocode_one(self, raw: Mapping[str, Any]) -> ArcadeGeoDto | None:
        """Force an AMap geocode lookup, reusing cache but ignoring catalog coordinates."""
        key = self._cache_key(raw)
        cached = self._geo_from_cache(key) if key is not None else None
        if cached is not None and cached.gcj02 is not None:
            return cached

        geocoded = self._geocode(raw)
        if geocoded is not None and key is not None:
            self._write_cache_entry(key=key, raw=raw, geo=geocoded)
        return geocoded

    def resolve_many(
        self,
        rows: list[Mapping[str, Any]],
        *,
        sync_limit: int | None = None,
        max_workers: int | None = None,
    ) -> dict[int, ArcadeGeoDto | None]:
        """Resolve a page of shops, geocoding only a bounded subset on cache misses."""
        resolved: dict[int, ArcadeGeoDto | None] = {}
        pending: list[Mapping[str, Any]] = []

        for row in rows:
            source_id = self._source_id(row)
            if source_id is None:
                continue
            direct = self._geo_from_catalog(row)
            if direct is not None:
                resolved[source_id] = direct
                continue
            key = self._cache_key(row)
            cached = self._geo_from_cache(key) if key is not None else None
            if cached is not None:
                resolved[source_id] = cached
                continue
            resolved[source_id] = None
            pending.append(row)

        if not pending:
            return resolved

        limit = max(0, sync_limit if sync_limit is not None else self._config.sync_limit)
        if limit <= 0:
            return resolved

        workers = max(1, max_workers if max_workers is not None else self._config.max_workers)
        geocode_rows = pending[:limit]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for row, geo in zip(geocode_rows, executor.map(self.resolve_one, geocode_rows)):
                source_id = self._source_id(row)
                if source_id is not None:
                    resolved[source_id] = geo
        return resolved

    def _geo_from_catalog(self, raw: Mapping[str, Any]) -> ArcadeGeoDto | None:
        embedded = raw.get("geo")
        if isinstance(embedded, dict):
            try:
                return ArcadeGeoDto.model_validate(embedded)
            except Exception:
                pass

        gcj_lng = self._coerce_float(raw.get("longitude_gcj02"))
        gcj_lat = self._coerce_float(raw.get("latitude_gcj02"))
        wgs_lng = self._coerce_float(raw.get("longitude_wgs84"))
        wgs_lat = self._coerce_float(raw.get("latitude_wgs84"))

        gcj = (
            GeoPoint(
                lng=gcj_lng,
                lat=gcj_lat,
                coord_system="gcj02",
                source="catalog",
                precision="exact",
            )
            if gcj_lng is not None and gcj_lat is not None
            else None
        )
        wgs = (
            GeoPoint(
                lng=wgs_lng,
                lat=wgs_lat,
                coord_system="wgs84",
                source="catalog",
                precision="exact",
            )
            if wgs_lng is not None and wgs_lat is not None
            else None
        )
        if gcj is None and wgs is None:
            return None
        return ArcadeGeoDto(gcj02=gcj, wgs84=wgs, source="catalog", precision="exact")

    def _geo_from_cache(self, key: str | None) -> ArcadeGeoDto | None:
        if not key:
            return None
        with self._lock:
            entry = self._cache.get(key)
        if not isinstance(entry, dict):
            return None
        geo = entry.get("geo")
        if not isinstance(geo, dict):
            return None
        try:
            return ArcadeGeoDto.model_validate(geo)
        except Exception:
            return None

    def _geocode(self, raw: Mapping[str, Any]) -> ArcadeGeoDto | None:
        if not self._config.api_key.strip():
            return None

        query = self._build_query(raw)
        if not query:
            return None

        payload = self._request_geocode(query=query, city=self._coerce_str(raw.get("city_name")))
        if not isinstance(payload, dict) or str(payload.get("status") or "") != "1":
            return None
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            return None
        first = geocodes[0]
        if not isinstance(first, dict):
            return None
        location = self._parse_location(first.get("location"))
        if location is None:
            return None

        gcj = GeoPoint(
            lng=location[0],
            lat=location[1],
            coord_system="gcj02",
            source="geocode",
            precision="approx",
        )
        return ArcadeGeoDto(gcj02=gcj, wgs84=None, source="geocode", precision="approx")

    def _request_geocode(self, *, query: str, city: str | None) -> dict[str, Any] | None:
        self._wait_for_request_slot()
        params = {
            "key": self._config.api_key,
            "address": query,
        }
        if city:
            params["city"] = city
        url = self._config.base_url.rstrip("/") + "/v3/geocode/geo?" + parse.urlencode(params)
        try:
            with httpx.Client(timeout=self._config.request_timeout_seconds) as client:
                response = client.get(url)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError):
            return None

        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _wait_for_request_slot(self) -> None:
        interval = max(0.0, float(self._config.request_interval_seconds or 0.0))
        if interval <= 0:
            return
        with self._request_lock:
            now = time.monotonic()
            wait_seconds = self._last_request_at + interval - now
            if wait_seconds > 0:
                time.sleep(wait_seconds)
                now = time.monotonic()
            self._last_request_at = now

    def _write_cache_entry(self, *, key: str, raw: Mapping[str, Any], geo: ArcadeGeoDto) -> None:
        source_id = self._source_id(raw)
        entry = {
            "source_id": source_id,
            "updated_at": self._coerce_str(raw.get("updated_at")),
            "address_fingerprint": self._address_fingerprint(raw),
            "geo": geo.model_dump(mode="json"),
        }
        with self._lock:
            stale_keys = [
                existing_key
                for existing_key, payload in self._cache.items()
                if isinstance(payload, dict) and payload.get("source_id") == source_id and existing_key != key
            ]
            for stale_key in stale_keys:
                self._cache.pop(stale_key, None)
            self._cache[key] = entry
            self._flush_cache_locked()

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        path = self._config.cache_path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in entries.items():
            if isinstance(key, str) and isinstance(value, dict):
                normalized[key] = value
        return normalized

    def _flush_cache_locked(self) -> None:
        path = self._config.cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": self._cache,
        }
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _cache_key(self, raw: Mapping[str, Any]) -> str | None:
        source_id = self._source_id(raw)
        if source_id is None:
            return None
        updated_at = self._coerce_str(raw.get("updated_at")) or "-"
        fingerprint = self._address_fingerprint(raw)
        return f"{source_id}:{updated_at}:{fingerprint}"

    def _address_fingerprint(self, raw: Mapping[str, Any]) -> str:
        parts = [
            self._coerce_str(raw.get("name")) or "",
            self._coerce_str(raw.get("address")) or "",
            self._coerce_str(raw.get("city_name")) or "",
        ]
        digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:16]

    def _build_query(self, raw: Mapping[str, Any]) -> str:
        parts = [
            self._coerce_str(raw.get("name")) or "",
            self._coerce_str(raw.get("address")) or "",
            self._coerce_str(raw.get("city_name")) or "",
        ]
        query = " ".join(part.strip() for part in parts if part and part.strip()).strip()
        return query

    @staticmethod
    def _parse_location(raw: object) -> tuple[float, float] | None:
        if not isinstance(raw, str):
            return None
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 2:
            return None
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None

    @staticmethod
    def _coerce_float(raw: object) -> float | None:
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_str(raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text or None

    @staticmethod
    def _source_id(raw: Mapping[str, Any]) -> int | None:
        value = raw.get("source_id")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
