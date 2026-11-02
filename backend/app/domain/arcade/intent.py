"""Arcade-specific intent classification and keyword heuristics.

The runtime only sees the resulting ``AgentSessionState.intent`` value; this
policy owns how the label is inferred and finalized. Since stage 6 the label is
derived per turn. A plain reply keeps ``navigate`` only while the previous
navigation is still pending (no route artifact yet, typically the worker asked
for an origin); after a route, an ambiguous follow-up is a search turn again.
"""

from __future__ import annotations

import re

from app.agent.runtime.lifecycle import DomainState
from app.agent.runtime.session_state import get_working_memory_artifact
from app.protocol.messages import ChatRequest, IntentType

_EXPLICIT_INTENTS = {"navigate", "search_nearby"}
# A message that clearly asks for search context should end a pending
# navigation flow (for example "find Gamma" after a route request).
_SEARCH_SIGNAL = re.compile(r"找|搜|查|有哪些|哪里|附近|机厅|店铺|find|search|nearby|near me", re.IGNORECASE)


class IntentPolicy:
    """Classify the arcade turn intent from the request, then from its artifacts."""

    def classify(self, state: DomainState, request: ChatRequest) -> None:
        """Derive this turn's label.

        Explicit intent and clear navigation/nearby signals always win. A
        plain reply after a navigation turn that produced no route is treated
        as clarification (for example "我在人民广场" after the worker asked for
        an origin) and keeps ``navigate``. Once a route exists, a plain
        follow-up is a new search turn, which removes the old sticky behaviour.
        """
        if request.intent is not None:
            state.intent = request.intent
            return
        inferred = self.infer(request.message)
        if inferred in _EXPLICIT_INTENTS:
            state.intent = inferred
            return
        if (
            self.normalize(state.intent) == "navigate"
            and self.navigation_pending(state)
            and not self.has_search_signal(request.message)
        ):
            state.intent = "navigate"
            return
        state.intent = inferred

    @staticmethod
    def navigation_pending(state: DomainState) -> bool:
        """Whether the previous turn left a navigation flow waiting for input."""
        memory = state.working_memory
        pending = memory.get("navigation_pending")
        if isinstance(pending, dict) and pending.get("turn_index") == state.turn_index - 1:
            return True
        return get_working_memory_artifact(memory, "route") is None

    @staticmethod
    def has_search_signal(message: str) -> bool:
        return bool(_SEARCH_SIGNAL.search(message.strip()))

    def finalize(self, state: DomainState) -> None:
        """A route artifact produced by this turn makes it a navigation turn."""
        memory = state.working_memory
        artifact_meta = memory.get("artifact_meta")
        route_meta = artifact_meta.get("route") if isinstance(artifact_meta, dict) else None
        if isinstance(route_meta, dict) and route_meta.get("turn_index") == state.turn_index:
            state.intent = "navigate"

    @staticmethod
    def infer(message: str) -> IntentType:
        """Fallback intent inference aligned with provider adapter behavior."""
        text = message.strip().lower()
        if re.search(r"导航|路线|怎么去|how to go|route|go to", text):
            return "navigate"
        if re.search(r"附近|nearby|near", text):
            return "search_nearby"
        return "search"

    @staticmethod
    def normalize(raw: str | None) -> IntentType:
        if raw == "navigate":
            return "navigate"
        if raw == "search_nearby":
            return "search_nearby"
        return "search"

    @staticmethod
    def extract_keyword(message: str) -> str:
        """Heuristic keyword extraction for working memory population."""
        text = message.strip()
        if not text:
            return ""
        latin_matches = re.findall(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,40}", text)
        if latin_matches:
            candidate = latin_matches[-1].strip()
            if " " in candidate:
                pieces = [item for item in re.split(r"\s+", candidate) if item]
                if pieces:
                    candidate = pieces[-1]
            return candidate
        cleaned = re.sub(
            r"(帮我找|请帮我找|帮忙找|附近哪里有|附近有没有|有没有|找一下|查一下|搜索|查询|机厅)",
            " ",
            text,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?，。！？")
        return cleaned or text
