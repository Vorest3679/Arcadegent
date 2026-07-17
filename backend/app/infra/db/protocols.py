"""Repository protocols shared by runtime APIs and builtin tools."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from app.agent.runtime.session_state import AgentSessionState


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


class SessionStateRepository(Protocol):
    """Persistence contract for the ReAct runtime's session store."""

    def health(self) -> dict[str, Any]:
        """Return diagnostics for the active repository backend."""
        ...

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        """Fetch an existing session or return a fresh, unpersisted one."""
        ...

    def get_session(self, session_id: str, *, client_id: str | None = None) -> AgentSessionState | None:
        """Return a deep-copied session state for API serialization.

        Applies client-scope access control: if ``client_id`` is provided and
        does not match the session's owner, returns ``None``.
        """
        ...

    def list_sessions(
        self,
        *,
        limit: int = 50,
        client_id: str | None = None,
    ) -> list[AgentSessionState]:
        """Return recent sessions sorted by updated_at desc, optionally filtered by client."""
        ...

    def delete_session(self, session_id: str, *, client_id: str | None = None) -> bool:
        """Delete one session by id; return True when it existed."""
        ...

    def save_session(self, state: AgentSessionState) -> None:
        """Persist one mutated session state."""
        ...
