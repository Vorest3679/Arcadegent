"""Logging helpers of the agent package.

Agent modules log through this module only. It depends on the standard library
alone: loggers come from :func:`logging.getLogger`, and the redaction helpers
below keep IDs, caller-controlled labels and exception locations log-safe.
Handlers and formatting stay with the host application, which reuses these
helpers so references correlate across the agent and the application.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from secrets import token_bytes
from traceback import extract_tb
from types import TracebackType
from typing import Collection

__all__ = [
    "configure_log_frame_root",
    "get_logger",
    "log_exception_frames",
    "log_public_label",
    "log_ref",
    "safe_traceback_frames",
]

# One process-wide key: the host re-exports ``log_ref`` from here, so the same
# value gets the same reference in agent and application log lines.
_LOG_REF_KEY = token_bytes(32)
_MAX_FRAMES = 8

# Frames are reported relative to ``root`` with ``prefix`` prepended. The
# default covers this package itself (``agent/...``); the host widens it to its
# own source tree at startup so application frames are reported too.
_PACKAGE_DIR = Path(__file__).resolve().parent
_frame_root: tuple[Path, str] = (_PACKAGE_DIR, _PACKAGE_DIR.name)


def configure_log_frame_root(root: Path, *, prefix: str = "") -> None:
    """Report traceback frames under ``root``; frames elsewhere are dropped."""
    global _frame_root
    _frame_root = (root.resolve(), prefix.strip("/"))


def safe_traceback_frames(tb: TracebackType) -> str:
    """Keep code locations without absolute paths, source lines or exception text."""
    root, prefix = _frame_root
    frames: list[str] = []
    for frame in extract_tb(tb):
        try:
            relative = Path(frame.filename).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        frames.append(f"{prefix}/{relative}:{frame.lineno}" if prefix else f"{relative}:{frame.lineno}")
    return ",".join(frames[-_MAX_FRAMES:])


def log_exception_frames(exc: BaseException) -> str:
    """Return source frame locations safe to pass to any log handler."""
    return safe_traceback_frames(exc.__traceback__) if exc.__traceback__ else "-"


def log_ref(value: str | None) -> str:
    """Correlate IDs within this process without a reversible plain hash."""
    return hmac_new(_LOG_REF_KEY, value.encode("utf-8"), sha256).hexdigest()[:12] if value else "-"


def log_public_label(value: str | None, allowed: Collection[str]) -> str:
    """Show known fixed labels; correlate all other caller-controlled labels."""
    return value if value in allowed else f"ref:{log_ref(value)}"


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger instance."""
    return logging.getLogger(name)
