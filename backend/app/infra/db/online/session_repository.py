"""Supabase-backed session repository using PostgREST table endpoints."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

import httpx

from app.agent.runtime.session_state import (
    AgentSessionState,
    _client_can_access,
    _client_matches_list_scope,
)
from app.infra.db.online.config import SupabaseRepositoryConfig
from app.infra.db.protocols import SessionStateRepository
from app.protocol.session_dto import ChatSessionCreateDto, ChatSessionRecord, ChatSessionSummaryRecord
from app.services.chat_session_mapper import ChatSessionMapper
from app.services.chat_session_summary import summary_record_from_state


_SUMMARY_COLUMNS = (
    "session_id,client_id,intent,status,title,preview,visible_turn_count,created_at,updated_at"
)


class SupabaseSessionStateRepository:
    """Persist chat sessions to the Supabase `chat_sessions` table."""

    def __init__(
        self,
        config: SupabaseRepositoryConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        base_url = config.url.rstrip("/")
        if not base_url:
            raise ValueError("supabase_url_required")
        if not config.key:
            raise ValueError("supabase_key_required")
        self._base_url = base_url
        self._key = config.key
        self._client = client or httpx.Client(timeout=config.timeout_seconds)

    def health(self) -> dict[str, Any]:
        response = self._http_get(
            "/chat_sessions",
            params={"select": "count()"},
        )
        payload = response.json()
        count = 0
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            count = int(payload[0].get("count") or 0)
        return {"backend": "supabase", "reachable": True, "rows": count}

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        existing = self._fetch_by_id(session_id)
        if existing is not None:
            return deepcopy(existing)

        self._http_post(
            "/chat_sessions",
            payload=ChatSessionCreateDto(session_id=session_id).model_dump(mode="json"),
            params={"on_conflict": "session_id"},
            prefer="resolution=ignore-duplicates,return=minimal",
        )
        return AgentSessionState(session_id=session_id)

    def get_session(self, session_id: str, *, client_id: str | None = None) -> AgentSessionState | None:
        """Read one session with client-scope access control."""
        state = self._fetch_by_id(session_id)
        if state is None:
            return None
        if not _client_can_access(state.client_id, client_id):
            return None
        return deepcopy(state)

    def get_session_for_display(
        self, session_id: str, *, client_id: str | None = None
    ) -> AgentSessionState | None:
        """Read one session from the slim `chat_session_details` view."""
        response = self._http_get(
            "/chat_session_details",
            params={
                "select": "*",
                "session_id": f"eq.{quote(session_id, safe='')}",
                "limit": "1",
            },
        )
        states = self._parse_state_list(response.json())
        if not states or not _client_can_access(states[0].client_id, client_id):
            return None
        return states[0]

    def list_session_summaries(
        self,
        *,
        limit: int = 50,
        client_id: str | None = None,
    ) -> list[ChatSessionSummaryRecord]:
        """History list from the summary columns; `turns` is not transferred."""
        safe_limit = max(1, min(limit, 200))
        params: dict[str, str] = {
            "select": _SUMMARY_COLUMNS,
            "order": "updated_at.desc",
            "limit": str(safe_limit),
        }
        if client_id is not None:
            params["client_id"] = f"eq.{client_id}"
        rows = self._http_get("/chat_sessions", params=params).json()
        if not isinstance(rows, list):
            raise RuntimeError("supabase_session_invalid_response")
        rows = [
            row
            for row in rows
            if isinstance(row, dict) and _client_matches_list_scope(row.get("client_id"), client_id)
        ]
        # Rows saved before the summary columns existed: summarize them from turns.
        legacy_ids = [row["session_id"] for row in rows if row.get("title") is None]
        legacy: dict[str, ChatSessionSummaryRecord] = {}
        if legacy_ids:
            id_list = ",".join(f'"{sid}"' for sid in legacy_ids if '"' not in sid and "," not in sid)
            response = self._http_get(
                "/chat_sessions",
                params={"select": "*", "session_id": f"in.({id_list})"},
            )
            for state in self._parse_state_list(response.json()):
                legacy[state.session_id] = summary_record_from_state(state)
        summaries: list[ChatSessionSummaryRecord] = []
        for row in rows:
            if row["session_id"] in legacy:
                summaries.append(legacy[row["session_id"]])
            elif row.get("title") is not None:
                summaries.append(
                    ChatSessionSummaryRecord(
                        session_id=row["session_id"],
                        client_id=row.get("client_id"),
                        intent=row.get("intent") or "search",
                        status=row.get("status") or "idle",
                        title=row["title"],
                        preview=row.get("preview"),
                        turn_count=int(row.get("visible_turn_count") or 0),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )
        return summaries

    def delete_session(self, session_id: str, *, client_id: str | None = None) -> bool:
        # Ownership check on the two columns it needs, not the whole row.
        response = self._http_get(
            "/chat_sessions",
            params={
                "select": "session_id,client_id",
                "session_id": f"eq.{quote(session_id, safe='')}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return False
        if not _client_can_access(rows[0].get("client_id"), client_id):
            return False
        self._http_delete(f"/chat_sessions?session_id=eq.{quote(session_id, safe='')}")
        return True

    def save_session(self, state: AgentSessionState) -> None:
        record = ChatSessionMapper.to_record(state)
        self._http_post(
            "/chat_sessions",
            payload=record.model_dump(mode="json"),
            params={"on_conflict": "session_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def _fetch_by_id(self, session_id: str) -> AgentSessionState | None:
        response = self._http_get(
            "/chat_sessions",
            params={
                "select": "*",
                "session_id": f"eq.{quote(session_id, safe='')}",
                "limit": "1",
            },
        )
        states = self._parse_state_list(response.json())
        return states[0] if states else None

    def _parse_state_list(self, payload: Any) -> list[AgentSessionState]:
        if not isinstance(payload, list):
            raise RuntimeError("supabase_session_invalid_response")
        states: list[AgentSessionState] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            record = ChatSessionRecord.model_validate(item)
            states.append(ChatSessionMapper.from_record(record))
        return states

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _http_get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        response = self._client.get(
            f"{self._base_url}/rest/v1{path}",
            headers=self._headers(),
            params=params,
        )
        self._raise_for_status("GET", path, response)
        return response

    def _http_post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        response = self._client.post(
            f"{self._base_url}/rest/v1{path}",
            headers=self._headers(prefer=prefer),
            params=params,
            json=payload,
        )
        self._raise_for_status("POST", path, response)
        return response

    def _http_delete(self, path: str) -> httpx.Response:
        response = self._client.delete(
            f"{self._base_url}/rest/v1{path}",
            headers=self._headers(),
        )
        self._raise_for_status("DELETE", path, response)
        return response

    @staticmethod
    def _raise_for_status(method: str, path: str, response: httpx.Response) -> None:
        if response.status_code >= 400:
            message = response.text[:500]
            raise RuntimeError(
                f"supabase_session_request_failed:{method}:{path}:{response.status_code}:{message}"
            )


def build_supabase_session_repository(config: SupabaseRepositoryConfig) -> SessionStateRepository:
    """Factory that returns the repository typed as the protocol."""
    return SupabaseSessionStateRepository(config)
