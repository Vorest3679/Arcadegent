"""Executor for worker dispatch requests."""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.provider import BuiltinToolContext


def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, str]:
    """Return a normalized worker dispatch payload for runtime handling."""
    _ = context
    return {
        "worker": str(args["worker"]).strip(),
        "task": str(args["task"]).strip(),
    }
