"""Lifecycle hooks for startup diagnostics and graceful shutdown."""

from __future__ import annotations

import asyncio
import signal
import threading
from types import FrameType

from app.core.container import AppContainer
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)

# Keep below the server's graceful-shutdown timeout so drain can finish first.
SHUTDOWN_DRAIN_SECONDS = 10.0
_SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


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
    install_shutdown_drain(container)


def install_shutdown_drain(container: AppContainer) -> None:
    """Start draining runs as soon as a shutdown signal arrives.

    Uvicorn waits for open connections before running the lifespan shutdown,
    and an SSE connection stays open until its run is sealed. Draining on the
    signal itself cancels and seals active runs, so their streams end and the
    server can proceed. The server's own handler is called afterwards and
    restored by the server when it exits.
    """
    if threading.current_thread() is not threading.main_thread():
        return
    loop = asyncio.get_running_loop()

    def start_drain() -> None:
        logger.info("Shutdown signal received: draining active runs.")
        container.run_manager.start_drain(SHUTDOWN_DRAIN_SECONDS)

    for sig in _SHUTDOWN_SIGNALS:
        previous = signal.getsignal(sig)
        if not callable(previous):
            continue

        def handler(signum: int, frame: FrameType | None, previous=previous) -> None:
            loop.call_soon_threadsafe(start_drain)
            previous(signum, frame)

        signal.signal(sig, handler)


async def on_shutdown(container: AppContainer) -> None:
    # Stop accepting runs and let active ones record their cancelled state
    # before the process releases its resources; reuses a signal-started drain.
    unfinished = await container.run_manager.start_drain(SHUTDOWN_DRAIN_SECONDS)
    logger.info("Arcadegent agent shutdown complete. unfinished_runs=%s", unfinished)
