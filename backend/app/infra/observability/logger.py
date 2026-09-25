"""Observability layer: centralized logger setup for API and ETL/runtime tracing."""

from __future__ import annotations

import logging
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from secrets import token_bytes
from traceback import extract_tb
from types import TracebackType
from typing import Collection


_LOG_REF_KEY = token_bytes(32)
_APP_ROOT = Path(__file__).resolve().parents[2]
# a trick:parents[2] is the root of the backend/app package, which is the root of the source tree

def _safe_app_frames(tb: TracebackType) -> str:
    """Keep code locations without absolute paths, source lines or exception text."""
    frames: list[str] = []
    for frame in extract_tb(tb):
        try:
            relative = Path(frame.filename).resolve().relative_to(_APP_ROOT)
        except ValueError:
            continue
        frames.append(f"{relative.as_posix()}:{frame.lineno}")
    return ",".join(frames[-8:])


def log_exception_frames(exc: BaseException) -> str:
    """Return backend frame locations safe to pass to any log handler."""
    return _safe_app_frames(exc.__traceback__) if exc.__traceback__ else "-"


class PrivacyFormatter(logging.Formatter):
    """Keep third-party messages and exception text out of the console log."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record, masking sensitive information for external logs."""
        application_log = record.name.startswith("app.")
        message = record.getMessage() if application_log else "external_log_event"
        if record.exc_info:
            message += f" exception_type={record.exc_info[0].__name__}"
            if application_log and record.exc_info[2] is not None:
                frames = _safe_app_frames(record.exc_info[2])
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


def log_ref(value: str | None) -> str:
    """Correlate IDs within this process without a reversible plain hash."""
    return hmac_new(_LOG_REF_KEY, value.encode("utf-8"), sha256).hexdigest()[:12] if value else "-"


def log_public_label(value: str | None, allowed: Collection[str]) -> str:
    """Show known fixed labels; correlate all other caller-controlled labels."""
    return value if value in allowed else f"ref:{log_ref(value)}"


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
