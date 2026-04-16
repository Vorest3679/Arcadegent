"""Supabase-backed arcade repository using PostgREST RPC endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx


@dataclass(frozen=True)
class SupabaseRepositoryConfig:
    """Connection settings for Supabase runtime reads."""

    url: str
    key: str
    timeout_seconds: float = 8.0


class SupabaseArcadeRepository:
    """Read arcade data through versioned Supabase RPC functions."""

    def __init__(
        self,
        config: SupabaseRepositoryConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        base_url = config.url.rstrip("/")
        if not base_url:
            raise ValueError("supabase_url_required")
        if not config.key:
            raise ValueError("supabase_key_required")
        self._base_url = base_url
        self._key = config.key
        self._client = client or httpx.Client(timeout=config.timeout_seconds)

    def health(self) -> dict[str, Any]:
        payload = self._rpc("arcadegent_data_health", {})
        if not isinstance(payload, dict):
            raise RuntimeError("supabase_rpc_invalid_response:arcadegent_data_health")
        payload.setdefault("backend", "supabase")
        return payload

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
        payload = self._rpc(
            "arcadegent_search_shops",
            {
                "p_keyword": keyword,
                "p_province_code": province_code,
                "p_city_code": city_code,
                "p_county_code": county_code,
                "p_province_name": province_name,
                "p_city_name": city_name,
                "p_county_name": county_name,
                "p_has_arcades": has_arcades,
                "p_page": page,
                "p_page_size": page_size,
                "p_sort_by": sort_by,
                "p_sort_order": sort_order,
                "p_sort_title_name": sort_title_name,
                "p_origin_lng": origin_lng,
                "p_origin_lat": origin_lat,
                "p_origin_coord_system": origin_coord_system,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError("supabase_rpc_invalid_response:arcadegent_search_shops")
        rows = payload.get("rows")
        total = payload.get("total")
        if not isinstance(rows, list) or not isinstance(total, int):
            raise RuntimeError("supabase_rpc_invalid_shape:arcadegent_search_shops")
        return [row for row in rows if isinstance(row, dict)], total

    def get_shop(self, source_id: int) -> dict[str, Any] | None:
        payload = self._rpc("arcadegent_get_shop", {"p_source_id": source_id})
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise RuntimeError("supabase_rpc_invalid_response:arcadegent_get_shop")
        return payload

    def list_provinces(self) -> list[dict[str, str]]:
        return self._list_regions(level="province", parent_code=None)

    def list_cities(self, province_code: str) -> list[dict[str, str]]:
        return self._list_regions(level="city", parent_code=province_code)

    def list_counties(self, city_code: str) -> list[dict[str, str]]:
        return self._list_regions(level="county", parent_code=city_code)

    def _list_regions(self, *, level: str, parent_code: str | None) -> list[dict[str, str]]:
        payload = self._rpc(
            "arcadegent_list_regions",
            {
                "p_level": level,
                "p_parent_code": parent_code,
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError("supabase_rpc_invalid_response:arcadegent_list_regions")
        regions: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            name = item.get("name")
            if isinstance(code, str) and isinstance(name, str):
                regions.append({"code": code, "name": name})
        return regions

    def _rpc(self, function_name: str, payload: dict[str, Any]) -> Any:
        response = self._client.post(
            f"{self._base_url}/rest/v1/rpc/{function_name}",
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            message = response.text[:500]
            raise RuntimeError(
                f"supabase_rpc_failed:{function_name}:{response.status_code}:{message}"
            )
        if not response.content:
            return None
        return response.json()
