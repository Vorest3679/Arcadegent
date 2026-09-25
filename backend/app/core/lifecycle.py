"""Lifecycle hooks for startup diagnostics."""

from __future__ import annotations

from app.core.container import AppContainer
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


async def on_startup(container: AppContainer) -> None:
    stats = container.store.health()
    await container.tool_registry.refresh_tools()
    providers = container.tool_registry.provider_health()
    counts = {}
    if isinstance(stats, dict):
        for key in ("total_lines", "loaded_rows", "bad_lines"):
            value = stats.get(key)
            if type(value) is int:
                counts[key] = value
    logger.info("Data store loaded: counts=%s", counts)
    logger.info("Tool provider status: provider_count=%s", len(providers))


def on_shutdown() -> None:
    logger.info("Arcadegent agent shutdown complete.")
