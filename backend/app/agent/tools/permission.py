"""Tool permission checks for runtime execution chain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_MCP_TOOL_PREFIX = "mcp__"
_MCP_TOOL_WILDCARD = "mcp__*"


class ToolPermissionError(RuntimeError):
    """Raised when a tool call violates policy or allowed list."""


@dataclass(frozen=True)
class ToolPolicy:
    read_only: bool = True
    concurrency_safe: bool = True


class ToolPermissionChecker:
    """Validate tool calls against policy config and runtime scope."""

    def __init__(self, *, policy_file: Path) -> None:
        self._policies, self._mcp_allow_all = self._load_policies(policy_file)

    def ensure_allowed(self, *, tool_name: str, allowed_tools: list[str]) -> None:
        if tool_name not in allowed_tools and not self._is_mcp_allowed(
            tool_name=tool_name,
            allowed_tools=allowed_tools,
        ):
            raise ToolPermissionError(f"tool_not_allowed:{tool_name}")
        if tool_name not in self._policies:
            return
        # Current tools are read-only by design. Keep this hook for future checks.
        _ = self._policies[tool_name]

    def _is_mcp_allowed(self, *, tool_name: str, allowed_tools: list[str]) -> bool:
        if not tool_name.startswith(_MCP_TOOL_PREFIX):
            return False
        return _MCP_TOOL_WILDCARD in allowed_tools or self._mcp_allow_all

    def _load_policies(self, policy_file: Path) -> tuple[dict[str, ToolPolicy], bool]:
        if not policy_file.exists():
            return {}, False
        try:
            raw = yaml.safe_load(policy_file.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return {}, False
        tool_policies = raw.get("tool_policies") if isinstance(raw, dict) else {}
        if not isinstance(tool_policies, dict):
            tool_policies = {}
        mcp_defaults = raw.get("mcp_defaults") if isinstance(raw, dict) else {}
        result: dict[str, ToolPolicy] = {}
        for name, payload in tool_policies.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                continue
            result[name] = ToolPolicy(
                read_only=bool(payload.get("read_only", True)),
                concurrency_safe=bool(payload.get("concurrency_safe", True)),
            )
        allow_all_mcp = bool(mcp_defaults.get("allow_all", False)) if isinstance(mcp_defaults, dict) else False
        return result, allow_all_mcp
