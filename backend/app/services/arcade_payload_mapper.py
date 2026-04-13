"""Shared DTO mappers for geo-aware arcade and session payloads."""

from __future__ import annotations

from typing import Any, Mapping

from app.protocol.messages import (
    ArcadeGeoDto,
    ArcadeShopDetailDto,
    ArcadeShopSummaryDto,
    ClientLocationContext,
    RouteSummaryDto,
)
from app.services.arcade_geo_resolver import ArcadeGeoResolver


class ArcadePayloadMapper:
    """Map raw store/runtime payloads into public DTOs with geo enrichment."""

    def __init__(self, *, geo_resolver: ArcadeGeoResolver) -> None:
        self._geo_resolver = geo_resolver

    def summaries_from_rows(
        self,
        rows: list[Mapping[str, Any]],
        *,
        sync_limit: int | None = None,
        max_workers: int | None = None,
    ) -> list[ArcadeShopSummaryDto]:
        geo_by_source_id = self._geo_resolver.resolve_many(
            rows,
            sync_limit=sync_limit,
            max_workers=max_workers,
        )
        return [self.summary_from_row(row, geo=geo_by_source_id.get(self._source_id(row) or -1)) for row in rows]

    def summary_from_row(
        self,
        row: Mapping[str, Any],
        *,
        geo: ArcadeGeoDto | None = None,
    ) -> ArcadeShopSummaryDto:
        resolved_geo = geo if geo is not None else self._geo_resolver.resolve_one(row)
        return ArcadeShopSummaryDto(
            source=str(row.get("source") or ""),
            source_id=int(row.get("source_id") or 0),
            source_url=str(row.get("source_url") or ""),
            name=str(row.get("name") or "unknown arcade"),
            name_pinyin=self._pick_optional_str(row.get("name_pinyin")),
            address=self._pick_optional_str(row.get("address")),
            transport=self._pick_optional_str(row.get("transport")),
            province_code=self._pick_optional_str(row.get("province_code")),
            province_name=self._pick_optional_str(row.get("province_name")),
            city_code=self._pick_optional_str(row.get("city_code")),
            city_name=self._pick_optional_str(row.get("city_name")),
            county_code=self._pick_optional_str(row.get("county_code")),
            county_name=self._pick_optional_str(row.get("county_name")),
            status=row.get("status"),
            type=row.get("type"),
            pay_type=row.get("pay_type"),
            locked=row.get("locked"),
            ea_status=row.get("ea_status"),
            price=row.get("price"),
            start_time=row.get("start_time"),
            end_time=row.get("end_time"),
            fav_count=self._coerce_optional_int(row.get("fav_count")),
            updated_at=self._pick_optional_str(row.get("updated_at")),
            arcade_count=int(row.get("arcade_count") or 0),
            geo=resolved_geo,
        )

    def detail_from_row(self, row: Mapping[str, Any]) -> ArcadeShopDetailDto:
        summary = self.summary_from_row(row)
        return ArcadeShopDetailDto(
            **summary.model_dump(mode="json"),
            comment=self._pick_optional_str(row.get("comment")),
            url=self._pick_optional_str(row.get("url")),
            image_thumb=row.get("image_thumb") if isinstance(row.get("image_thumb"), dict) else None,
            events=row.get("events") if isinstance(row.get("events"), list) else [],
            arcades=row.get("arcades") if isinstance(row.get("arcades"), list) else [],
            collab=row.get("collab") if isinstance(row.get("collab"), bool) else None,
            raw=row.get("raw") if isinstance(row.get("raw"), dict) else None,
        )

    def route_from_payload(self, payload: object) -> RouteSummaryDto | None:
        if not isinstance(payload, dict):
            return None
        try:
            return RouteSummaryDto.model_validate(payload)
        except Exception:
            return None

    def client_location_from_payload(self, payload: object) -> ClientLocationContext | None:
        if not isinstance(payload, dict):
            return None
        try:
            return ClientLocationContext.model_validate(payload)
        except Exception:
            return None

    @staticmethod
    def _source_id(row: Mapping[str, Any]) -> int | None:
        try:
            return int(row.get("source_id")) if row.get("source_id") is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _pick_optional_str(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    @staticmethod
    def _coerce_optional_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
