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
from app.protocol.session_dto import ChatSessionCreateDto, ChatSessionRecord
from app.services.chat_session_mapper import ChatSessionMapper


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
        if not _client_can_access(state, client_id):
            return None
        return deepcopy(state)

    def list_sessions(
        self,
        *,
        limit: int = 50,
        client_id: str | None = None,
    ) -> list[AgentSessionState]:
        safe_limit = max(1, min(limit, 200))
        params: dict[str, str] = {
            "select": "*",
            "order": "updated_at.desc",
            "limit": str(safe_limit),
        }
        if client_id is not None:
            params["client_id"] = f"eq.{client_id}"
        response = self._http_get("/chat_sessions", params=params)
        states = self._parse_state_list(response.json())
        return [deepcopy(state) for state in states if _client_matches_list_scope(state, client_id)]

    def delete_session(self, session_id: str, *, client_id: str | None = None) -> bool:
        state = self._fetch_by_id(session_id)
        if state is None:
            return False
        if not _client_can_access(state, client_id):
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
