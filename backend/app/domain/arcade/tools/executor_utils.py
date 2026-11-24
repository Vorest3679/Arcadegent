"""Shared utilities for the arcade tool executors."""

from __future__ import annotations

import re

_REGION_CODE_PATTERN = re.compile(r"^\d{12}$")


def short_text(text: str | None, *, limit: int = 80) -> str:
    """Collapse whitespace for logs and trim long text values."""
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def as_region_code_or_name(
    code_value: str | None,
    name_value: str | None,
) -> tuple[str | None, str | None]:
    """Accept either a 12-digit code or a natural-language region name."""
    code = code_value.strip() if isinstance(code_value, str) else None
    name = name_value.strip() if isinstance(name_value, str) else None
    if code and not _REGION_CODE_PATTERN.fullmatch(code):
        if not name:
            name = code
        code = None
    return code or None, name or None
