"""Repository protocols shared by runtime APIs and builtin tools."""

from __future__ import annotations

from typing import Any, Literal, Protocol


class ArcadeRepository(Protocol):
    """Read contract for arcade shop repositories."""

    def health(self) -> dict[str, Any]:
        """Return diagnostics for the active repository backend."""
        ...

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
        shop_name: str | None = None,
        title_name: str | None = None,
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
        """Filter and paginate arcade shops."""
        ...

    def get_shop(self, source_id: int) -> dict[str, Any] | None:
        """Fetch one shop by source id."""
        ...

    def list_provinces(self) -> list[dict[str, str]]:
        """Return province choices."""
        ...

    def list_cities(self, province_code: str) -> list[dict[str, str]]:
        """Return city choices under a province code."""
        ...

    def list_counties(self, city_code: str) -> list[dict[str, str]]:
        """Return county choices under a city code."""
        ...
