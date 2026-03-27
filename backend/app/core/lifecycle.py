"""Lifecycle hooks for startup diagnostics."""

from __future__ import annotations

from app.core.container import AppContainer
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


async def on_startup(container: AppContainer) -> None:
    stats = container.store.health()
    await container.tool_registry.refresh_tools()
    providers = container.tool_registry.provider_health()
    logger.info("Data store loaded: %s", stats)
    logger.info("Tool provider status: %s", providers)


def on_shutdown() -> None:
    logger.info("Arcadegent agent shutdown complete.")
