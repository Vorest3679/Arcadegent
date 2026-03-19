"""Lifecycle hooks for startup diagnostics."""

from __future__ import annotations

from app.core.container import AppContainer
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


def on_startup(container: AppContainer) -> None:
    stats = container.store.health()
    container.tool_registry.refresh_mcp_tools()
    mcp = container.tool_registry.mcp_health()
    logger.info("Data store loaded: %s", stats)
    logger.info("MCP status: %s", mcp)


def on_shutdown() -> None:
    logger.info("Arcadegent agent shutdown complete.")
