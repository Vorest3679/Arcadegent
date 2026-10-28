"""Arcade display projection shared by responses, session details and turn maps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArcadeDisplay:
    """Raw arcade map/card projection of one session state.

    Rows stay raw dicts; the application service owns the HTTP/DTO mapping so
    the domain does not depend on ``app.protocol``.
    """

    shops: tuple[dict[str, Any], ...] = ()
    route: dict[str, Any] | None = None
    client_location: dict[str, Any] | None = None
    destination: dict[str, Any] | None = None
    view_payload: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        """Shape used by the final assistant turn's ``payload.map_artifacts``."""
        return {
            "shops": list(self.shops),
            "route": self.route,
            "client_location": self.client_location,
            "destination": self.destination,
            "view_payload": self.view_payload,
        }
