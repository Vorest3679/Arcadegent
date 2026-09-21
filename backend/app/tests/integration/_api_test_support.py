"""Shared fixtures and helpers for FastAPI integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.llm.provider_adapter import ModelResponse
from app.agent.runtime.session_state import (
    AgentSessionState,
    _client_can_access,
    _client_matches_list_scope,
    state_from_dict,
)
from app.infra.db.protocols import SessionStateRepository


def _stub_provider_adapter(client: TestClient, reply: str = "stubbed model reply") -> None:
    """Replace the provider adapter with a deterministic model response."""
    adapter = client.app.state.container.react_runtime._provider_adapter

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        return ModelResponse(
            text=reply,
            status="completed",
            protocol="responses",
            reported_model="stub-model",
        )

    adapter.complete = fake_complete  # type: ignore[method-assign]


class InMemorySessionStateRepository:
    """Test-only in-memory implementation of SessionStateRepository."""

    def __init__(self) -> None:
        self._states: dict[str, AgentSessionState] = {}

    def health(self) -> dict[str, object]:
        return {"backend": "memory", "rows": len(self._states)}

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        from copy import deepcopy

        state = self._states.get(session_id)
        if state is None:
            state = AgentSessionState(session_id=session_id)
            self._states[session_id] = state
        return deepcopy(state)

    def get_session(self, session_id: str, *, client_id: str | None = None) -> AgentSessionState | None:
        from copy import deepcopy

        state = self._states.get(session_id)
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
        from copy import deepcopy

        snapshots = [
            deepcopy(state)
            for state in self._states.values()
            if _client_matches_list_scope(state, client_id)
        ]
        snapshots.sort(key=lambda s: s.updated_at, reverse=True)
        return snapshots[:limit]

    def delete_session(self, session_id: str, *, client_id: str | None = None) -> bool:
        state = self._states.get(session_id)
        if state is None:
            return False
        if not _client_can_access(state, client_id):
            return False
        del self._states[session_id]
        return True

    def save_session(self, state: AgentSessionState) -> None:
        from copy import deepcopy

        self._states[state.session_id] = deepcopy(state)

    def seed(self, state: AgentSessionState) -> None:
        from copy import deepcopy

        self._states[state.session_id] = deepcopy(state)


def _seed_data(path: Path) -> None:
    rows = [
        {
            "source": "bemanicn",
            "source_id": 10,
            "source_url": "https://map.bemanicn.com/s/10",
            "name": "Gamma Arcade",
            "address": "Test Address",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "updated_at": "2026-02-20T00:00:00Z",
            "longitude_wgs84": 116.397428,
            "latitude_wgs84": 39.90923,
            "arcades": [{"title_name": "CHUNITHM", "quantity": 2}],
        }
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _clear_mcp_env() -> None:
    for name in (
        "MCP_SERVERS_DIR",
        "MCP_DEFAULT_TIMEOUT_SECONDS",
    ):
        os.environ.pop(name, None)


def _clear_llm_env() -> None:
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
    ):
        os.environ.pop(name, None)


def _build_client(
    tmp_path: Path,
    *,
    session_store: SessionStateRepository | None = None,
    mcp_servers_dir: Path | None = None,
    cache_path: Path | None = None,
) -> TestClient:
    data_path = tmp_path / "shops.jsonl"
    empty_mcp_dir = tmp_path / "mcp_empty"
    empty_mcp_dir.mkdir(exist_ok=True)
    _seed_data(data_path)
    _clear_mcp_env()
    _clear_llm_env()
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["ARCADE_DATA_SOURCE"] = "jsonl"
    os.environ["ARCADE_GEO_CACHE_PATH"] = str(cache_path or (tmp_path / "arcade_geo_cache.json"))
    os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_BASE_URL"] = "https://api.example.invalid/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["AMAP_API_KEY"] = "test-amap-key"
    os.environ["MCP_SERVERS_DIR"] = str(mcp_servers_dir or empty_mcp_dir)
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"

    store = session_store or InMemorySessionStateRepository()
    import app.core.container as container_module

    original_build_session_repository = container_module._build_session_repository
    container_module._build_session_repository = lambda _settings: store

    from app.main import create_app

    try:
        client = TestClient(create_app())
        client.app.state.container.session_store = store  # type: ignore[attr-defined]
        client.__enter__()
    finally:
        container_module._build_session_repository = original_build_session_repository
    return client


def _build_client_with_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    session_store: SessionStateRepository | None = None,
    cache_path: Path | None = None,
) -> TestClient:
    data_path = tmp_path / "shops_custom.jsonl"
    empty_mcp_dir = tmp_path / "mcp_empty"
    empty_mcp_dir.mkdir(exist_ok=True)
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    _clear_mcp_env()
    _clear_llm_env()
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["ARCADE_DATA_SOURCE"] = "jsonl"
    os.environ["ARCADE_GEO_CACHE_PATH"] = str(cache_path or (tmp_path / "arcade_geo_cache.json"))
    os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_BASE_URL"] = "https://api.example.invalid/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["AMAP_API_KEY"] = "test-amap-key"
    os.environ["MCP_SERVERS_DIR"] = str(empty_mcp_dir)
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"

    store = session_store or InMemorySessionStateRepository()
    import app.core.container as container_module

    original_build_session_repository = container_module._build_session_repository
    container_module._build_session_repository = lambda _settings: store

    from app.main import create_app

    try:
        client = TestClient(create_app())
        client.app.state.container.session_store = store  # type: ignore[attr-defined]
        client.__enter__()
    finally:
        container_module._build_session_repository = original_build_session_repository
    return client


def _wait_for_session_status(
    client: TestClient,
    session_id: str,
    expected_status: str,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/chat/sessions/{session_id}")
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict):
                last_payload = payload
                if payload.get("status") == expected_status:
                    return payload
        time.sleep(0.05)
    raise AssertionError(
        f"session '{session_id}' did not reach status '{expected_status}', last_payload={last_payload}"
    )
