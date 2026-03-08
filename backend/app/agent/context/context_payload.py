"""Explicit DTOs for runtime context blocks consumed by LLM stages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContextBlockKey = Literal["query", "search_catalog", "shop_details", "route"]


class ContextBlockRefDto(BaseModel):
    """Directory item describing one available context block."""

    block: ContextBlockKey
    purpose: str
    primary_fields: list[str] = Field(default_factory=list)


class ContextDirectoryDto(BaseModel):
    """Table-of-contents style view for available runtime context."""

    active_intent: str | None = None
    active_subagent: str | None = None
    available_blocks: list[ContextBlockRefDto] = Field(default_factory=list)
    reading_order: list[ContextBlockKey] = Field(default_factory=list)
    focus: str | None = None
    top_shop_ids: list[int] = Field(default_factory=list)


class QueryContextDto(BaseModel):
    """Structured search/navigation query constraints."""

    keyword: str | None = None
    province_code: str | None = None
    province_name: str | None = None
    city_code: str | None = None
    city_name: str | None = None
    county_code: str | None = None
    county_name: str | None = None
    has_arcades: bool | None = None
    page: int | None = None
    page_size: int | None = None
    sort_by: str | None = None
    sort_order: str | None = None
    sort_title_name: str | None = None


class SearchCatalogShopDto(BaseModel):
    """Lightweight shop row for ranked result catalogs."""

    source_id: int | None = None
    name: str | None = None
    city_name: str | None = None
    county_name: str | None = None
    arcade_count: int | None = None
    detail_sections: list[str] = Field(default_factory=list)


class SearchCatalogContextDto(BaseModel):
    """High-level search result catalog without heavy detail fields."""

    total: int | None = None
    top_shops: list[SearchCatalogShopDto] = Field(default_factory=list)


class ShopBasicContextDto(BaseModel):
    """Core shop facts safe to mention in concise replies."""

    source_id: int | None = None
    name: str | None = None
    province_name: str | None = None
    city_name: str | None = None
    county_name: str | None = None
    address: str | None = None
    arcade_count: int | None = None


class ShopTransportContextDto(BaseModel):
    """Transport guidance block for one shop."""

    summary: str


class ShopArcadeContextDto(BaseModel):
    """Arcade title detail under one shop."""

    title_name: str | None = None
    quantity: int | None = None
    version: str | None = None
    comment: str | None = None


class ShopCommentContextDto(BaseModel):
    """Operator or user comment block for one shop."""

    summary: str


class ShopDetailContextDto(BaseModel):
    """Detailed shop payload split by section to reduce prompt ambiguity."""

    source_id: int | None = None
    basic: ShopBasicContextDto
    transport: ShopTransportContextDto | None = None
    arcades: list[ShopArcadeContextDto] = Field(default_factory=list)
    comment: ShopCommentContextDto | None = None


class RouteContextDto(BaseModel):
    """Navigation block for final route explanation."""

    destination_source_id: int | None = None
    destination_name: str | None = None
    provider: str | None = None
    mode: str | None = None
    distance_m: int | None = None
    duration_s: int | None = None
    hint: str | None = None


class RuntimeContextPayloadDto(BaseModel):
    """Top-level context payload injected into runtime instructions."""

    directory: ContextDirectoryDto
    query: QueryContextDto | None = None
    search_catalog: SearchCatalogContextDto | None = None
    shop_details: list[ShopDetailContextDto] = Field(default_factory=list)
    route: RouteContextDto | None = None
