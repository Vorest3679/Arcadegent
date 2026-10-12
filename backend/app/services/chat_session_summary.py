"""Session list summary derived from stored turns.

The same rules fill the `title`, `preview` and `visible_turn_count` columns of
`chat_sessions` on every save, so the history list never has to read `turns`.
The SQL backfill in latest sql
mirrors them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.protocol.session_dto import ChatSessionSummaryRecord

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


def summarize_state(state: AgentSessionState) -> SessionSummary:
    return SessionSummary(
        title=build_title(state.turns),
        preview=build_preview(state.turns),
        visible_turn_count=len(visible_turns(state.turns)),
    )


def summary_record_from_state(state: AgentSessionState) -> ChatSessionSummaryRecord:
    """History-list record computed from a full state instead of the stored summary columns."""
    summary = summarize_state(state)
    return ChatSessionSummaryRecord(
        session_id=state.session_id,
        client_id=state.client_id,
        intent=state.intent,
        status=state.status,
        title=summary.title,
        preview=summary.preview,
        turn_count=summary.visible_turn_count,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )
