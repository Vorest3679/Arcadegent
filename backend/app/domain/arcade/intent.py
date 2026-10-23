"""Arcade-specific intent classification and keyword heuristics.

The runtime only sees the resulting ``AgentSessionState.intent`` value; this
policy owns how the label is inferred, carried across turns and finalized.
"""

from __future__ import annotations

import re

from app.agent.runtime.lifecycle import DomainState
from app.protocol.messages import ChatRequest, IntentType

_EXPLICIT_INTENTS = {"navigate", "search_nearby"}


class IntentPolicy:
    """Classify and normalize the arcade turn intent (stage 2: sticky rules kept)."""

    def classify(self, state: DomainState, request: ChatRequest) -> None:
        """Apply the request intent, keeping the previous label when ambiguous."""
        inferred = request.intent or self.infer(request.message)
        if request.intent is not None:
            state.intent = request.intent
        elif inferred in _EXPLICIT_INTENTS:
            state.intent = inferred
        elif not state.intent:
            state.intent = inferred

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
    def mark_navigate(state: DomainState) -> None:
        """A route result makes the current turn a navigation turn."""
        state.intent = "navigate"

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
