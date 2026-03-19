"""HTTP API layer: health and readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.core.container import AppContainer

router = APIRouter(tags=["health"])


@router.get("/health")
def health(container: AppContainer = Depends(get_container)) -> dict:
    return {
        "status": "ok",
        "store": container.store.health(),
        "env": container.settings.env,
        "mcp": container.tool_registry.mcp_health(),
    }
