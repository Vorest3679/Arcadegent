"""HTTP API layer: arcade list/detail read endpoints."""

from __future__ import annotations

from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_container
from app.core.container import AppContainer
from app.protocol.messages import ArcadeShopDetailDto, PagedArcadeResponse

router = APIRouter(prefix="/api/arcades", tags=["arcades"])

@router.get("", response_model=PagedArcadeResponse)
def list_arcades(
    keyword: str | None = Query(default=None),
    shop_name: str | None = Query(default=None),
    title_name: str | None = Query(default=None),
    province_code: str | None = Query(default=None),
    city_code: str | None = Query(default=None),
    county_code: str | None = Query(default=None),
    has_arcades: bool | None = Query(default=None),
    sort_by: Literal["default", "updated_at", "source_id", "arcade_count", "title_quantity", "distance"] = Query(
        default="default"
    ),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    sort_title_name: str | None = Query(default=None),
    origin_lng: float | None = Query(default=None, ge=-180, le=180),
    origin_lat: float | None = Query(default=None, ge=-90, le=90),
    origin_coord_system: Literal["wgs84", "gcj02"] = Query(default="wgs84"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    container: AppContainer = Depends(get_container),
) -> PagedArcadeResponse:
    rows, total = container.store.list_shops(
        keyword=keyword,
        shop_name=shop_name,
        title_name=title_name,
        province_code=province_code,
        city_code=city_code,
        county_code=county_code,
        has_arcades=has_arcades,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        sort_title_name=sort_title_name,
        origin_lng=origin_lng,
        origin_lat=origin_lat,
        origin_coord_system=origin_coord_system,
    )
    items = container.arcade_payload_mapper.summaries_from_rows(
        rows,
        sync_limit=container.settings.arcade_geo_sync_limit,
        max_workers=container.settings.arcade_geo_max_workers,
    )
    total_pages = ceil(total / page_size) if total > 0 else 0
    return PagedArcadeResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get("/{source_id}", response_model=ArcadeShopDetailDto)
def get_arcade_detail(
    source_id: int,
    container: AppContainer = Depends(get_container),
) -> ArcadeShopDetailDto:
    row = container.store.get_shop(source_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"arcade source_id={source_id} not found")
    return container.arcade_payload_mapper.detail_from_row(row)
