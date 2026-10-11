"""Session list summary derived from stored turns.

The same rules fill the `title`, `preview` and `visible_turn_count` columns of
`chat_sessions` on every save, so the history list never has to read `turns`.
The SQL backfill in latest sql
mirrors them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.runtime.session_state import AgentSessionState, AgentTurn

TITLE_LIMIT = 32
PREVIEW_LIMIT = 72
DEFAULT_TITLE = "New chat"


@dataclass(frozen=True)
class SessionSummary:
    title: str
    preview: str | None
    visible_turn_count: int


def single_line(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def visible_turns(turns: list[AgentTurn]) -> list[AgentTurn]:
    """Keep model/tool traces for context while excluding them from the chat UI."""
    return [
        turn
        for turn in turns
        if turn.role == "user"
        or (
            turn.role == "assistant"
            and turn.scope == "conversation"
            and not isinstance(turn.payload.get("model"), dict)
        )
    ]


def build_title(turns: list[AgentTurn]) -> str:
    for turn in turns:
        if turn.role != "user":
            continue
        title = single_line(turn.content, limit=TITLE_LIMIT)
        if title:
            return title
    return DEFAULT_TITLE


def build_preview(turns: list[AgentTurn]) -> str | None:
    for turn in reversed(visible_turns(turns)):
        preview = single_line(turn.content, limit=PREVIEW_LIMIT)
        if preview:
            return preview
    return None


def summarize_turns(turns: list[AgentTurn]) -> SessionSummary:
    return SessionSummary(
        title=build_title(turns),
        preview=build_preview(turns),
        visible_turn_count=len(visible_turns(turns)),
    )


def summarize_state(state: AgentSessionState) -> SessionSummary:
    return summarize_turns(state.turns)
