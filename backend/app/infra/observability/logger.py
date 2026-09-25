"""Observability layer: centralized logger setup for API and ETL/runtime tracing."""

from __future__ import annotations

import logging
from hashlib import sha256
from hmac import new as hmac_new
from secrets import token_bytes


_LOG_REF_KEY = token_bytes(32)


class PrivacyFormatter(logging.Formatter):
    """Keep third-party messages and exception text out of the console log."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record, masking sensitive information for external logs."""
        application_log = record.name.startswith("app.")
        message = record.getMessage() if application_log else "external_log_event"
        if record.exc_info:
            message += f" exception_type={record.exc_info[0].__name__}"
        safe_record = logging.makeLogRecord({ # sanitize the record of sensitive info,like exception text and third-party messages
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


def setup_logging(level: str = "INFO") -> None:
    """Configure the single console handler with a privacy-aware formatter."""
    normalized = level.upper()
    handler = logging.StreamHandler()
    # Set the customized PrivacyFormatter for the handler
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
