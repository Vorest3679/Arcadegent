"""Observability layer: centralized logger setup for API and ETL/runtime tracing.

The redaction helpers (``log_ref``, ``log_public_label``, ``log_exception_frames``)
live in the agent package so agent and application log lines share one
reference key; this module re-exports them and owns handlers and formatting.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agent._logging import (
    configure_log_frame_root,
    log_exception_frames,
    log_public_label,
    log_ref,
    safe_traceback_frames,
)

__all__ = [
    "PrivacyFormatter",
    "get_logger",
    "log_exception_frames",
    "log_public_label",
    "log_ref",
    "setup_logging",
]

# parents[2] is the ``backend/app`` package: frames are reported relative to it.
_APP_ROOT = Path(__file__).resolve().parents[2]
configure_log_frame_root(_APP_ROOT)


class PrivacyFormatter(logging.Formatter):
    """Keep third-party messages and exception text out of the console log."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record, masking sensitive information for external logs."""
        application_log = record.name.startswith("app.")
        message = record.getMessage() if application_log else "external_log_event"
        if record.exc_info:
            message += f" exception_type={record.exc_info[0].__name__}"
            if application_log and record.exc_info[2] is not None:
                frames = safe_traceback_frames(record.exc_info[2])
                if frames:
                    message += f" app_frames={frames}"
        safe_record = logging.makeLogRecord({
            **record.__dict__,
            "name": record.name if application_log else "external",
            "msg": message,
            "args": (),
            "exc_info": None,
            "exc_text": None,
            "stack_info": None,
        })
        return super().format(safe_record)


def setup_logging(level: str = "INFO") -> None:
    """Configure the single console handler with a privacy-aware formatter."""
    normalized = level.upper()
    handler = logging.StreamHandler()
    handler.setFormatter(PrivacyFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.basicConfig(level=normalized, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(normalized)
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger instance."""
    return logging.getLogger(name)
