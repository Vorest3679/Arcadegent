"""HTTP API layer: administrative region endpoints for UI cascade filters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container
from app.core.container import AppContainer
from app.protocol.messages import RegionItemDto

router = APIRouter(prefix="/api/regions", tags=["regions"])


@router.get("/provinces", response_model=list[RegionItemDto])
def list_provinces(container: AppContainer = Depends(get_container)) -> list[RegionItemDto]:
    return [RegionItemDto(code=row["code"], name=row["name"]) for row in container.store.list_provinces()]


@router.get("/cities", response_model=list[RegionItemDto])
def list_cities(
    province_code: str = Query(..., min_length=1),
    container: AppContainer = Depends(get_container),
) -> list[RegionItemDto]:
    rows = container.store.list_cities(province_code=province_code)
    return [RegionItemDto(code=row["code"], name=row["name"]) for row in rows]


@router.get("/counties", response_model=list[RegionItemDto])
def list_counties(
    city_code: str = Query(..., min_length=1),
    container: AppContainer = Depends(get_container),
) -> list[RegionItemDto]:
    rows = container.store.list_counties(city_code=city_code)
    return [RegionItemDto(code=row["code"], name=row["name"]) for row in rows]
