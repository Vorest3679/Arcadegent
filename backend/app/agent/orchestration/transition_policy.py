"""State transition policy for subagent switching."""

from __future__ import annotations

from typing import Any


class TransitionPolicy:
    """Determine next subagent from tool execution outcomes."""

    def next_subagent(
        self,
        *,
        current_subagent: str,
        tool_name: str,
        tool_status: str,
        tool_output: dict[str, Any],
        fallback_intent: str,
        has_route: bool,
        has_shops: bool,
    ) -> str:
        normalized_intent = "navigate" if fallback_intent == "navigate" else "search"
        candidate = self._normalize_candidate(tool_output.get("next_subagent"))

        if tool_name == "select_next_subagent":
            if tool_status != "completed":
                return current_subagent
            if candidate is not None:
                return self._choose_from_candidate(
                    current_subagent=current_subagent,
                    candidate=candidate,
                    normalized_intent=normalized_intent,
                    has_route=has_route,
                    has_shops=has_shops,
                )
            return "navigation_agent" if normalized_intent == "navigate" else "search_agent"

        if tool_status != "completed":
            return current_subagent

        if tool_name == "db_query_tool":
            total = tool_output.get("total")
            if current_subagent == "navigation_agent":
                if has_route:
                    return "summary_agent"
                return "navigation_agent"
            if has_shops:
                return "summary_agent"
            # Avoid repetitive empty-query loops; move to summary stage for explicit no-result response.
            if isinstance(total, int) and total <= 0:
                return "summary_agent"
            return "search_agent"
        if tool_name == "geo_resolve_tool":
            return "navigation_agent"
        if tool_name == "route_plan_tool":
            return "summary_agent" if has_route else "navigation_agent"
        if tool_name.startswith("mcp__"):
            if isinstance(tool_output.get("route"), dict) or has_route:
                return "summary_agent"
            return current_subagent
        if tool_name == "summary_tool":
            return "summary_agent"
        if current_subagent == "intent_router":
            return "navigation_agent" if normalized_intent == "navigate" else "search_agent"
        return current_subagent

    def is_terminal_tool(self, *, tool_name: str, tool_status: str) -> bool:
        return tool_name == "summary_tool" and tool_status == "completed"

    def _normalize_candidate(self, raw: Any) -> str | None:
        if not isinstance(raw, str):
            return None
        value = raw.strip()
        if value in {"intent_router", "search_agent", "navigation_agent", "summary_agent"}:
            return value
        return None

    def _choose_from_candidate(
        self,
        *,
        current_subagent: str,
        candidate: str,
        normalized_intent: str,
        has_route: bool,
        has_shops: bool,
    ) -> str:
        if current_subagent == "intent_router":
            if normalized_intent == "navigate":
                return "navigation_agent"
            if candidate in {"search_agent", "summary_agent"} and (candidate != "summary_agent" or has_shops):
                return candidate
            return "search_agent"

        if candidate == "summary_agent":
            if has_route or has_shops:
                return "summary_agent"
            return current_subagent
        return candidate
