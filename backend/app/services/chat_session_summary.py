"""Session list summary derived from stored turns.

The same rules fill the `title`, `preview`, `visible_turn_count` and
`artifact_shops` columns of `chat_sessions` on every save, so the history list
never has to read `turns`. The SQL backfill in the matching migration mirrors
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.protocol.session_dto import ChatSessionArtifactShopRecord, ChatSessionSummaryRecord

TITLE_LIMIT = 32
PREVIEW_LIMIT = 72
DEFAULT_TITLE = "New chat"
# Bound the denormalized search index: a long session can mention many shops,
# but the sidebar only needs a small, deduplicated set.
ARTIFACT_SHOP_LIMIT = 200


@dataclass(frozen=True)
class SessionSummary:
    title: str
    preview: str | None
    visible_turn_count: int
    artifact_shops: list[ChatSessionArtifactShopRecord]


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


def _artifact_shop_rows(turn: AgentTurn) -> list[dict[str, Any]]:
    """Arcade rows a turn's map artifacts reference, including its destination."""
    payload = turn.payload if isinstance(turn.payload, dict) else {}
    artifacts = payload.get("map_artifacts")
    if not isinstance(artifacts, dict):
        return []
    rows: list[dict[str, Any]] = []
    raw_shops = artifacts.get("shops")
    if isinstance(raw_shops, list):
        rows.extend(item for item in raw_shops if isinstance(item, dict))
    destination = artifacts.get("destination")
    if isinstance(destination, dict):
        rows.append(destination)
    return rows


def build_artifact_shops(turns: list[AgentTurn]) -> list[ChatSessionArtifactShopRecord]:
    """Compact, deduplicated arcade names/pinyin from all persisted map artifacts.

    The client uses this as the search index for "which conversation mentioned
    this arcade"; it deliberately carries no coordinates or roster data.
    """
    shops: dict[str, ChatSessionArtifactShopRecord] = {}
    for turn in turns:
        for row in _artifact_shop_rows(turn):
            raw_name = row.get("name")
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            key = name.casefold()
            raw_pinyin = row.get("name_pinyin")
            pinyin = raw_pinyin.strip() if isinstance(raw_pinyin, str) and raw_pinyin.strip() else None
            existing = shops.get(key)
            if existing is None:
                if len(shops) >= ARTIFACT_SHOP_LIMIT:
                    return list(shops.values())
                shops[key] = ChatSessionArtifactShopRecord(name=name, name_pinyin=pinyin)
            elif existing.name_pinyin is None and pinyin:
                shops[key] = existing.model_copy(update={"name_pinyin": pinyin})
    return list(shops.values())


def summarize_state(state: AgentSessionState) -> SessionSummary:
    return SessionSummary(
        title=build_title(state.turns),
        preview=build_preview(state.turns),
        visible_turn_count=len(visible_turns(state.turns)),
        artifact_shops=build_artifact_shops(state.turns),
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
        artifact_shops=summary.artifact_shops,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )
