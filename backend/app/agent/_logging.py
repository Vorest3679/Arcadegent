"""Logging entry point of the agent kernel modules.

Kernel modules log through this module instead of importing
``app.infra.observability`` directly, so the package boundary has one place
to swap when the agent package is extracted. For now it forwards to the
application's privacy-aware logger unchanged.
"""

from __future__ import annotations

from app.infra.observability.logger import get_logger, log_exception_frames, log_ref

__all__ = ["get_logger", "log_exception_frames", "log_ref"]
