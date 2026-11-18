"""Tool layer: provider resolver for map-related requests without direct API calls."""

from __future__ import annotations

from app.protocol.messages import ProviderType


class GeoResolveTool:
    """Resolve provider by administrative code / language context."""

    def resolve_provider(self, province_code: str | None) -> ProviderType:
        if not province_code:
            return "none"
        code = province_code.strip()
        if len(code) == 12 and code.isdigit() and code[:2] not in {"71", "81", "82"}:
            return "amap"
        return "google"

