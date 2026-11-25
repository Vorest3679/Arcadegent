"""HTTP API layer: health and readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.core.arcade_wiring import arcade_mcp_health
from app.core.container import AppContainer

router = APIRouter(tags=["health"])


@router.get("/health")
def health(container: AppContainer = Depends(get_container)) -> dict:
    return {
        "status": "ok",
        "store": container.store.health(),
        "env": container.settings.env,
        "tool_providers": container.tool_registry.provider_health(),
        "plugins": container.plugin_set.health(),
        "mcp": arcade_mcp_health(container.tool_registry.mcp_health()),
    }
