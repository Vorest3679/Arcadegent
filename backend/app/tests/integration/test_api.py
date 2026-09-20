"""Integration tests for core FastAPI endpoints and chat session continuity."""

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
    state_to_dict,
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


def test_skill_discovery_loading_business_tools_and_execution_isolation(tmp_path, monkeypatch):
    """Exercise real tool dispatch/context/session boundaries with a deterministic model."""
    import app.core.container as container_module
    from app.agent.llm.provider_adapter import ModelToolCall
    from app.agent.skills.config import SkillConfig

    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(container_module, "load_skill_config", lambda _: SkillConfig(roots=[root]))
    client = _build_client(tmp_path)
    runtime = client.app.state.container.react_runtime
    main_calls = 0
    worker_calls = 0

    def create_skill(name, body):
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use for synthetic integration tasks.\n---\n{body}",
            encoding="utf-8",
        )
        return folder

    def call(name, args):
        return ModelResponse(tool_calls=[ModelToolCall(f"{name}-{main_calls}-{worker_calls}", name, args)])

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        nonlocal main_calls, worker_calls
        assert "PARENT_BODY_MARKER" not in json.dumps(messages)
        assert "WORKER_BODY_MARKER" not in json.dumps(messages)
        if runtime_hints["active_subagent"] == "search_worker":
            worker_calls += 1
            assert "PARENT_BODY_MARKER" not in instructions
            assert "REFERENCE_MARKER" not in instructions
            assert '"name": "worker-skill"' in instructions  # Refreshed at worker start.
            if worker_calls % 2:
                assert "WORKER_BODY_MARKER" not in instructions
                return call("read_skill", {"name": "worker-skill"})
            assert instructions.count("WORKER_BODY_MARKER") == 1
            return ModelResponse(text="worker finished")

        main_calls += 1
        assert "WORKER_BODY_MARKER" not in instructions
        if main_calls == 1:
            assert '"name": "parent-skill"' not in instructions
            folder = create_skill("parent-skill", "PARENT_BODY_MARKER. Read references/guide.md.")
            (folder / "references").mkdir()
            (folder / "references" / "guide.md").write_text("REFERENCE_MARKER")
            return call("list_skills", {})
        if main_calls == 2:
            assert '"name": "parent-skill"' in instructions
            assert "PARENT_BODY_MARKER" not in instructions
            return call("read_skill", {"name": "parent-skill", "path": "SKILL.md"})
        if main_calls == 3:
            assert instructions.count("PARENT_BODY_MARKER") == 1
            create_skill("parent-skill", "NEW_BODY_FOR_NEXT_TURN")
            return ModelResponse(tool_calls=[
                ModelToolCall("reference", "read_skill", {"name": "parent-skill", "path": "references/guide.md"}),
                ModelToolCall("duplicate", "read_skill", {"name": "parent-skill"}),
            ])
        if 4 <= main_calls <= 7:
            assert instructions.count("PARENT_BODY_MARKER") == 1
            assert instructions.count("REFERENCE_MARKER") == 1
            assert "NEW_BODY_FOR_NEXT_TURN" not in instructions
        if main_calls == 4:
            return call("db_query_tool", {"shop_name": "Gamma Arcade", "page": 1, "page_size": 5})
        if main_calls == 5:
            assert "Gamma Arcade" in instructions
            create_skill("worker-skill", "WORKER_BODY_MARKER")
            return call("invoke_worker", {"worker": "search_worker", "task": "Inspect the available skill."})
        if main_calls == 6:
            return call("invoke_worker", {"worker": "search_worker", "task": "Inspect it in a fresh execution."})
        if main_calls == 7:
            return ModelResponse(text="已找到 Gamma Arcade。")
        assert main_calls == 8
        assert "PARENT_BODY_MARKER" not in instructions and "REFERENCE_MARKER" not in instructions
        assert "NEW_BODY_FOR_NEXT_TURN" not in instructions
        return ModelResponse(text="新一轮尚未加载技能。")

    runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "已找到 Gamma Arcade。"
    assert worker_calls == 4
    state = client.app.state.container.session_store.get_session(payload["session_id"])
    query = next(turn for turn in state.turns if turn.name == "db_query_tool")
    assert query.payload["status"] == "completed"
    assert query.payload["result"]["total"] == 1
    for turn in state.turns:
        assert "PARENT_BODY_MARKER" not in turn.content
        assert "REFERENCE_MARKER" not in json.dumps(turn.payload)
        if turn.name == "read_skill":
            assert turn.payload["status"] == "completed"
            assert turn.payload["result"]["status"] == "loaded"
    assert "PARENT_BODY_MARKER" not in json.dumps(state_to_dict(state))
    response = client.post("/api/chat", json={"message": "继续", "session_id": payload["session_id"]})
    assert response.status_code == 200
    assert response.json()["reply"] == "新一轮尚未加载技能。"


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


def test_health_arcades_and_chat(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mcp"]["enabled"] is False

    listing = client.get("/api/arcades", params={"keyword": "Gamma"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["source_id"] == 10

    chat_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert chat_resp.status_code == 200
    assert chat_resp.json()["intent"] in {"search", "search_nearby"}


def test_arcade_list_enriches_geo_and_writes_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "arcade_geo_cache.json"
    row = {
        "source": "bemanicn",
        "source_id": 21,
        "source_url": "https://map.bemanicn.com/s/21",
        "name": "Geo Arcade",
        "address": "Nanjing Road",
        "province_code": "310000000000",
        "province_name": "Shanghai",
        "city_code": "310100000000",
        "city_name": "Shanghai",
        "county_code": "310101000000",
        "county_name": "Huangpu",
        "updated_at": "2026-04-13T00:00:00Z",
        "arcades": [{"title_name": "maimai", "quantity": 2}],
    }
    client = _build_client_with_rows(tmp_path, [row], cache_path=cache_path)
    client.app.state.container.arcade_geo_resolver._request_geocode = lambda **_: {  # type: ignore[method-assign]
        "status": "1",
        "geocodes": [{"location": "121.475,31.228"}],
    }

    resp = client.get("/api/arcades")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["geo"]["gcj02"]["lng"] == 121.475
    assert cache_path.exists()


def test_arcade_list_supports_shop_name_search_without_title_matches(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 31,
                "source_url": "https://map.bemanicn.com/s/31",
                "name": "星际传奇人民广场店",
                "name_pinyin": "xing-ji-chuan-qi-ren-min-guang-chang-dian",
                "arcades": [{"title_name": "SOUND VOLTEX", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 32,
                "source_url": "https://map.bemanicn.com/s/32",
                "name": "Gamma Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 2}],
            },
        ],
    )

    by_shop_name = client.get("/api/arcades", params={"shop_name": "星际传奇"})
    assert by_shop_name.status_code == 200
    assert by_shop_name.json()["total"] == 1
    assert by_shop_name.json()["items"][0]["source_id"] == 31

    by_title_as_shop_name = client.get("/api/arcades", params={"shop_name": "maimai"})
    assert by_title_as_shop_name.status_code == 200
    assert by_title_as_shop_name.json()["total"] == 0

    legacy_keyword = client.get("/api/arcades", params={"keyword": "maimai"})
    assert legacy_keyword.status_code == 200
    assert legacy_keyword.json()["total"] == 1
    assert legacy_keyword.json()["items"][0]["source_id"] == 32


def test_arcade_list_supports_title_name_filter(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 41,
                "source_url": "https://map.bemanicn.com/s/41",
                "name": "星际传奇一号店",
                "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 42,
                "source_url": "https://map.bemanicn.com/s/42",
                "name": "星际传奇二号店",
                "arcades": [{"title_name": "SOUND VOLTEX", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 43,
                "source_url": "https://map.bemanicn.com/s/43",
                "name": "Delta Arcade",
                "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
            },
        ],
    )

    resp = client.get(
        "/api/arcades",
        params={"shop_name": "星际传奇", "title_name": "CHUNITHM"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_id"] == 41


def test_arcade_detail_returns_geo(tmp_path: Path) -> None:
    row = {
        "source": "bemanicn",
        "source_id": 22,
        "source_url": "https://map.bemanicn.com/s/22",
        "name": "Detail Geo Arcade",
        "address": "Xidan",
        "province_code": "110000000000",
        "province_name": "Beijing",
        "city_code": "110100000000",
        "city_name": "Beijing",
        "county_code": "110102000000",
        "county_name": "Xicheng",
        "updated_at": "2026-04-13T00:00:00Z",
        "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
    }
    client = _build_client_with_rows(tmp_path, [row])
    client.app.state.container.arcade_geo_resolver._request_geocode = lambda **_: {  # type: ignore[method-assign]
        "status": "1",
        "geocodes": [{"location": "116.3974,39.9087"}],
    }

    resp = client.get("/api/arcades/22")

    assert resp.status_code == 200
    assert resp.json()["geo"]["gcj02"]["lat"] == 39.9087


def test_chat_session_detail_supports_legacy_route_payload(tmp_path: Path) -> None:
    session_store_path = tmp_path / "legacy_chat_sessions.json"
    session_store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "session_id": "legacy-session",
                        "turn_index": 1,
                        "active_subagent": "main_agent",
                        "intent": "navigate",
                        "status": "completed",
                        "last_error": None,
                        "turns": [
                            {
                                "role": "user",
                                "content": "how to go",
                                "payload": {},
                                "created_at": "2026-04-13T00:00:00Z",
                            },
                            {
                                "role": "assistant",
                                "content": "route ready",
                                "payload": {
                                    "final": True,
                                    "map_artifacts": {
                                        "shops": [
                                            {
                                                "source": "bemanicn",
                                                "source_id": 10,
                                                "source_url": "https://map.bemanicn.com/s/10",
                                                "name": "Gamma Arcade",
                                                "address": "Test Address",
                                                "longitude_wgs84": 116.397428,
                                                "latitude_wgs84": 39.90923,
                                                "arcade_count": 1,
                                            }
                                        ],
                                        "route": {
                                            "provider": "amap",
                                            "mode": "walking",
                                            "distance_m": 1200,
                                            "duration_s": 900,
                                            "polyline": [
                                                {"lng": 116.397428, "lat": 39.90923},
                                                {"lng": 116.407428, "lat": 39.91923},
                                            ],
                                        },
                                        "destination": {
                                            "source": "bemanicn",
                                            "source_id": 10,
                                            "source_url": "https://map.bemanicn.com/s/10",
                                            "name": "Gamma Arcade",
                                            "address": "Test Address",
                                            "arcade_count": 1,
                                        },
                                        "view_payload": {"version": 1, "scene": "agent_route"},
                                    },
                                },
                                "created_at": "2026-04-13T00:00:10Z",
                            },
                        ],
                        "working_memory": {
                            "artifacts": {
                                "shops": [
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
                                        "arcade_count": 1,
                                    }
                                ],
                                "route": {
                                    "provider": "amap",
                                    "mode": "walking",
                                    "distance_m": 1200,
                                    "duration_s": 900,
                                    "polyline": [
                                        {"lng": 116.397428, "lat": 39.90923},
                                        {"lng": 116.407428, "lat": 39.91923},
                                    ],
                                },
                            },
                            "reply": "route ready",
                        },
                        "previous_response_id": None,
                        "created_at": "2026-04-13T00:00:00Z",
                        "updated_at": "2026-04-13T00:00:10Z",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_store = InMemorySessionStateRepository()
    legacy = json.loads(session_store_path.read_text(encoding="utf-8"))
    for raw in legacy.get("sessions", []):
        state = state_from_dict(raw)
        if state is not None:
            fake_store.seed(state)
    client = _build_client(tmp_path, session_store=fake_store)

    resp = client.get("/api/chat/sessions/legacy-session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"]["origin"]["lng"] == 116.397428
    assert body["destination"]["source_id"] == 10
    assert body["turns"][-1]["map_artifacts"]["route"]["distance_m"] == 1200
    assert body["turns"][-1]["map_artifacts"]["destination"]["source_id"] == 10
    assert "client_location" in body
    assert "view_payload" in body


def test_health_reports_mcp_tools_loaded_from_config_directory(tmp_path: Path) -> None:
    mcp_dir = tmp_path / "mcp_servers"
    mcp_dir.mkdir()
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    (mcp_dir / "amap.json").write_text(
        json.dumps(
            {
                "command": sys.executable,
                "args": [str(fixture_server)],
                "route_tool_name": "maps_direction_walking",
            }
        ),
        encoding="utf-8",
    )

    client = _build_client(tmp_path, mcp_servers_dir=mcp_dir)

    health = client.get("/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["mcp"]["enabled"] is True
    assert payload["mcp"]["discovered_tool_count"] == 1
    assert payload["mcp"]["servers"]["amap"]["discovered"] is True
    assert payload["mcp"]["servers"]["amap"]["selected_route_tool"] == "mcp__amap__maps_direction_walking"
    assert payload["mcp"]["servers"]["amap"]["available_tools"] == ["mcp__amap__maps_direction_walking"]


def test_navigation_worker_hydrates_route_strings_and_returns_route(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall

    mcp_dir = tmp_path / "navigation_mcp"
    mcp_dir.mkdir()
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    (mcp_dir / "amap.json").write_text(json.dumps({
        "command": sys.executable,
        "args": [str(fixture_server)],
        "route_tool_name": "maps_direction_walking",
    }), encoding="utf-8")
    client = _build_client(tmp_path, mcp_servers_dir=mcp_dir)
    adapter = client.app.state.container.react_runtime._provider_adapter
    main_calls = 0
    worker_calls = 0

    async def fake_complete(**kwargs):
        nonlocal main_calls, worker_calls
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            main_calls += 1
            if main_calls == 1:
                return ModelResponse(tool_calls=[ModelToolCall(
                    "dispatch-route", "invoke_worker",
                    {"worker": "navigation_worker", "task": "从起点步行到 Gamma Arcade"},
                )])
            return ModelResponse(text="步行路线已经规划完成。")

        worker_calls += 1
        if worker_calls == 1:
            return ModelResponse(tool_calls=[ModelToolCall(
                "destination", "db_query_tool", {"shop_id": 10, "page": 1, "page_size": 1},
            )])
        if worker_calls == 2:
            return ModelResponse(tool_calls=[ModelToolCall(
                "provider", "geo_resolve_tool", {"province_code": "110000000000"},
            )])
        if worker_calls == 3:
            return ModelResponse(tool_calls=[ModelToolCall(
                "route", "route_plan_tool", {
                    "provider": "amap",
                    "mode": "walking",
                    "origin": "116.3,39.9",
                    "destination": "116.32,39.905",
                },
            )])
        return ModelResponse(text="route ready")

    adapter.complete = fake_complete  # type: ignore[method-assign]
    response = client.post("/api/chat", json={"message": "从起点走到 Gamma Arcade"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "navigate"
    assert payload["route"]["mode"] == "walking"
    assert payload["route"]["origin"]["lng"] == 116.3
    assert payload["route"]["destination"]["lng"] == 116.32
    state = client.app.state.container.session_store.get_session(payload["session_id"])
    assert state.working_memory["last_route_endpoints"]["destination"]["lng"] == 116.32
    route_turn = next(turn for turn in state.turns if turn.name == "route_plan_tool")
    assert route_turn.payload["argument_evidence"]["hydrated_fields"] == ["origin", "destination"]


def test_chat_reuses_session_context(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert first_resp.status_code == 200
    first_payload = first_resp.json()
    session_id = first_payload["session_id"]

    second_resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "continue with previous result"},
    )
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert second_payload["session_id"] == session_id
    if first_payload["shops"]:
        assert first_payload["shops"][0]["source_id"] == 10
        assert second_payload["shops"]
        assert second_payload["shops"][0]["source_id"] == 10

    sessions_resp = client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["turn_count"] >= 2

    detail_resp = client.get(f"/api/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    turns = detail["turns"]
    assert turns
    assert turns[0]["role"] == "user"
    assert turns[-1]["role"] == "assistant"

    delete_resp = client.delete(f"/api/chat/sessions/{session_id}")
    assert delete_resp.status_code == 204

    deleted_detail = client.get(f"/api/chat/sessions/{session_id}")
    assert deleted_detail.status_code == 404


def test_chat_sessions_survive_app_restart(tmp_path: Path) -> None:
    shared_store = InMemorySessionStateRepository()
    client = _build_client(tmp_path, session_store=shared_store)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert first_resp.status_code == 200
    session_id = first_resp.json()["session_id"]

    restarted_client = _build_client(tmp_path, session_store=shared_store)
    _stub_provider_adapter(restarted_client)

    sessions_resp = restarted_client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert any(row["session_id"] == session_id for row in sessions)

    detail_resp = restarted_client.get(f"/api/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"


def test_chat_records_native_tool_round_trip(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall

    client = _build_client(tmp_path)
    adapter = client.app.state.container.react_runtime._provider_adapter
    captured_messages: list[list[dict[str, object]]] = []
    calls = {"count": 0}

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        captured_messages.append(messages)
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        call_id="call_1",
                        name="db_query_tool",
                        arguments={"keyword": "Gamma", "page": 1, "page_size": 3},
                        raw_arguments='{"keyword": "Gamma", "page": 1, "page_size": 3}',
                    )
                ],
                status="completed",
                protocol="responses",
                reported_model="stub-model",
                transcript={
                    "responses_output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "db_query_tool",
                            "arguments": '{"keyword": "Gamma", "page": 1, "page_size": 3}',
                        }
                    ]
                },
            )
        return ModelResponse(text="found Gamma", status="completed", protocol="responses")

    adapter.complete = fake_complete  # type: ignore[method-assign]

    resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["reply"] == "found Gamma"
    assert payload["shops"] and payload["shops"][0]["source_id"] == 10

    # The second model call must receive the native tool round trip:
    # assistant transcript (responses_output) followed by the paired tool output.
    assert calls["count"] == 2
    second_call = captured_messages[1]
    assistant_message = next(item for item in second_call if item.get("responses_output"))
    assert assistant_message["responses_output"][0]["call_id"] == "call_1"
    tool_message = next(item for item in second_call if item.get("role") == "tool")
    assert tool_message["tool_call_id"] == "call_1"

    detail = client.get(f"/api/chat/sessions/{payload['session_id']}").json()
    assert detail["status"] == "completed"
    model_turns = [
        turn.__dict__ for turn in client.app.state.container.session_store.get_session(payload["session_id"]).turns
        if turn.role == "assistant" and turn.payload.get("model")
    ]
    assert model_turns
    first_model = model_turns[0]["payload"]["model"]
    assert first_model["tool_calls"][0]["raw_arguments"] == '{"keyword": "Gamma", "page": 1, "page_size": 3}'
    tool_turns = [turn.__dict__ for turn in client.app.state.container.session_store.get_session(payload["session_id"]).turns if turn.role == "tool"]
    assert tool_turns
    evidence = tool_turns[0].get("payload", {}).get("argument_evidence")
    assert evidence is not None
    assert evidence["parsed_arguments"]["keyword"] == "Gamma"


def test_chat_marks_session_failed_when_model_errors(tmp_path: Path) -> None:
    client = _build_client(tmp_path)  # LLM_API_KEY is empty, so the provider errors out

    resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["reply"]  # fallback reply is still returned to the caller

    detail_resp = client.get(f"/api/chat/sessions/{payload['session_id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "failed"
    assert detail["last_error"]

    replay_buffer = client.app.state.container.replay_buffer
    events = replay_buffer.list_events(payload["session_id"])
    assert any(event.event == "session.failed" for event in events)
    completed = [event for event in events if event.event == "assistant.completed"]
    assert not completed
    stream = client.get(f"/api/stream/{payload['session_id']}")
    assert "event: session.failed" in stream.text


def test_chat_sessions_are_scoped_by_client_id(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    first_resp = client.post(
        "/api/chat",
        json={"client_id": "client-a", "message": "find Gamma", "page_size": 3},
    )
    assert first_resp.status_code == 200
    first_session_id = first_resp.json()["session_id"]

    second_resp = client.post(
        "/api/chat",
        json={"client_id": "client-b", "message": "find Gamma", "page_size": 3},
    )
    assert second_resp.status_code == 200
    second_session_id = second_resp.json()["session_id"]

    first_list_resp = client.get("/api/chat/sessions", params={"client_id": "client-a"})
    assert first_list_resp.status_code == 200
    assert [row["session_id"] for row in first_list_resp.json()] == [first_session_id]

    second_list_resp = client.get("/api/chat/sessions", params={"client_id": "client-b"})
    assert second_list_resp.status_code == 200
    assert [row["session_id"] for row in second_list_resp.json()] == [second_session_id]

    wrong_detail_resp = client.get(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-b"},
    )
    assert wrong_detail_resp.status_code == 404

    wrong_continue_resp = client.post(
        "/api/chat",
        json={
            "client_id": "client-b",
            "session_id": first_session_id,
            "message": "continue from another client",
        },
    )
    assert wrong_continue_resp.status_code == 404

    wrong_delete_resp = client.delete(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-b"},
    )
    assert wrong_delete_resp.status_code == 404

    right_delete_resp = client.delete(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-a"},
    )
    assert right_delete_resp.status_code == 204


def test_second_turn_resets_stream_replay_buffer(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "松江区有哪些机厅可以去？", "page_size": 3})
    assert first_resp.status_code == 200
    session_id = first_resp.json()["session_id"]

    replay_buffer = client.app.state.container.replay_buffer
    first_events = replay_buffer.list_events(session_id)
    assert first_events
    first_event_ids = {event.id for event in first_events}
    assert any(event.event == "assistant.completed" for event in first_events)

    second_resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "上海松江区", "page_size": 3},
    )
    assert second_resp.status_code == 200

    second_events = replay_buffer.list_events(session_id)
    assert second_events
    assert all(event.id not in first_event_ids for event in second_events)
    assert any(event.event == "assistant.completed" for event in second_events)


def test_chat_dispatch_runs_in_background(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    dispatch_resp = client.post("/api/chat/sessions", json={"message": "find Gamma", "page_size": 3})
    assert dispatch_resp.status_code == 202
    dispatch_payload = dispatch_resp.json()
    session_id = dispatch_payload["session_id"]
    assert dispatch_payload["status"] == "running"

    detail = _wait_for_session_status(client, session_id, "completed")
    assert detail["session_id"] == session_id
    assert detail["reply"]
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"

    sessions_resp = client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    session_row = next(row for row in sessions if row["session_id"] == session_id)
    assert session_row["status"] == "completed"


def test_chat_dispatch_rejects_duplicate_running_session(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)
    runtime = client.app.state.container.react_runtime
    original_run_chat = runtime.run_chat

    async def slow_run_chat(request):
        await asyncio.sleep(0.2)
        return await original_run_chat(request)

    runtime.run_chat = slow_run_chat  # type: ignore[method-assign]

    session_id = "s_duplicate123"
    first_resp = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma", "page_size": 3},
    )
    assert first_resp.status_code == 202

    second_resp = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma again", "page_size": 3},
    )
    assert second_resp.status_code == 409

    _wait_for_session_status(client, session_id, "completed")


def test_cancel_running_chat_preserves_context_for_the_next_input(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    adapter = client.app.state.container.react_runtime._provider_adapter

    async def slow_complete(*, instructions, messages, tools, runtime_hints=None):
        await asyncio.sleep(5)
        return ModelResponse(text="too late", status="completed", protocol="responses")

    adapter.complete = slow_complete  # type: ignore[method-assign]
    session_id = "s_cancelled123"
    first = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma", "page_size": 3},
    )
    assert first.status_code == 202

    cancelled = client.post(f"/api/chat/sessions/{session_id}/cancel")
    assert cancelled.status_code == 200
    cancelled_detail = cancelled.json()
    assert cancelled_detail["status"] == "failed"
    assert "上下文" in cancelled_detail["last_error"]
    assert [turn["content"] for turn in cancelled_detail["turns"]] == ["find Gamma"]

    _stub_provider_adapter(client, reply="continued from saved context")
    second = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "continue", "page_size": 3},
    )
    assert second.status_code == 202
    completed = _wait_for_session_status(client, session_id, "completed")
    assert [turn["content"] for turn in completed["turns"]] == [
        "find Gamma",
        "continue",
        "continued from saved context",
    ]


def test_session_detail_hides_model_evidence_turns(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client, reply="only show once")

    response = client.post("/api/chat", json={"session_id": "s_visible123", "message": "hello"})
    assert response.status_code == 200
    detail = client.get("/api/chat/sessions/s_visible123").json()
    assert [turn["content"] for turn in detail["turns"]] == ["hello", "only show once"]
    assert detail["turn_count"] == 2


def test_arcades_api_supports_title_quantity_sorting(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 10,
                "source_url": "https://map.bemanicn.com/s/10",
                "name": "Gamma Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 11,
                "source_url": "https://map.bemanicn.com/s/11",
                "name": "Delta Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 4}],
            },
            {
                "source": "bemanicn",
                "source_id": 12,
                "source_url": "https://map.bemanicn.com/s/12",
                "name": "Epsilon Arcade",
                "arcades": [{"title_name": "sdvx", "quantity": 2}],
            },
        ],
    )

    resp = client.get(
        "/api/arcades",
        params={
            "has_arcades": "true",
            "sort_by": "title_quantity",
            "sort_order": "desc",
            "sort_title_name": "maimai",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 3
    assert [row["source_id"] for row in payload["items"]] == [11, 10, 12]


def test_arcades_api_supports_distance_sorting(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 10,
                "source_url": "https://map.bemanicn.com/s/10",
                "name": "Near Arcade",
                "longitude_wgs84": 116.397428,
                "latitude_wgs84": 39.90923,
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 11,
                "source_url": "https://map.bemanicn.com/s/11",
                "name": "Far Arcade",
                "longitude_wgs84": 116.407428,
                "latitude_wgs84": 39.91923,
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
        ],
    )

    resp = client.get(
        "/api/arcades",
        params={
            "has_arcades": "true",
            "sort_by": "distance",
            "sort_order": "asc",
            "origin_lng": 116.397428,
            "origin_lat": 39.90923,
            "origin_coord_system": "wgs84",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    assert [row["source_id"] for row in payload["items"]] == [10, 11]
    assert payload["items"][0]["distance_m"] == 0


def test_incomplete_text_fails_without_success_event(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    async def fake_complete(**kwargs):
        return ModelResponse(text="partial", status="incomplete", error={"type": "incomplete_response", "message": "length"})
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    state = client.app.state.container.session_store.get_session(response["session_id"])
    assert state.status == "failed"
    stream = client.get(f"/api/stream/{state.session_id}").text
    assert "event: session.failed" in stream
    assert "event: assistant.completed" not in stream


def test_invalid_json_cannot_be_hydrated_into_success(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall
    client = _build_client(tmp_path)
    calls = 0
    async def fake_complete(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(tool_calls=[ModelToolCall("bad", "summary_tool", {}, raw_arguments="{", parse_error="invalid JSON")])
        return ModelResponse(text="Unable to summarize")
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = client.app.state.container.replay_buffer.list_events(response["session_id"])
    assert [event.event for event in events if event.event.startswith("tool.")] == ["tool.started", "tool.failed"]
    state = client.app.state.container.session_store.get_session(response["session_id"])
    evidence = next(turn.payload["argument_evidence"] for turn in state.turns if turn.role == "tool")
    assert evidence["parse_error"] == "invalid JSON"
    assert evidence["hydrated_fields"] == []


def test_previous_route_is_not_promoted_as_new_result() -> None:
    from app.agent.runtime.react_runtime import ReactRuntime
    from app.agent.runtime.session_state import set_working_memory_artifact
    runtime = object.__new__(ReactRuntime)
    memory = {}
    set_working_memory_artifact(memory, "route", {"distance_m": 100}, turn_index=1)
    set_working_memory_artifact(memory, "shops", [{"source_id": 10}], turn_index=1)
    memory["last_route_endpoints"] = {
        "origin": {"lng": 116.3, "lat": 39.9},
        "destination": {"lng": 116.4, "lat": 39.91},
    }
    prepared = runtime._prepare_turn_memory(memory)
    assert "route" not in prepared["artifacts"]
    worker = runtime._build_worker_memory_snapshot(prepared)
    assert worker["artifacts"]["shops"] == [{"source_id": 10}]
    assert worker["last_route_endpoints"] == memory["last_route_endpoints"]
    assert runtime._promote_worker_artifacts(parent_memory=memory, worker_memory=worker, turn_index=2) == {}
    assert memory["artifact_meta"]["shops"]["turn_index"] == 1
    set_working_memory_artifact(worker, "shops", [], turn_index=2)
    assert runtime._promote_worker_artifacts(parent_memory=memory, worker_memory=worker, turn_index=2) == {"shops": []}


def test_online_route_unavailable_does_not_estimate() -> None:
    import pytest
    from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
    from app.protocol.messages import Location
    with pytest.raises(RuntimeError, match="route_unavailable"):
        asyncio.run(RoutePlanTool().plan_route(provider="amap", mode="walking", origin=Location(lng=116, lat=39), destination=Location(lng=117, lat=40)))


def test_history_window_keeps_complete_tool_group() -> None:
    from app.agent.context.context_builder import ContextBuilder
    from app.agent.runtime.session_state import AgentTurn
    builder = ContextBuilder(prompt_root=Path("."), history_turn_limit=4)
    turns = [AgentTurn(role="user", content="query"), AgentTurn(role="assistant", content="", payload={"model": {"transcript": {"responses_output": [{"type": "function_call", "call_id": "c"}]}}})]
    turns.extend(AgentTurn(role="tool", content="result", call_id=str(i)) for i in range(5))
    retained = builder._tail_turns(turns, scope="conversation")
    assert retained == turns


def test_incomplete_worker_call_is_not_executed(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall
    client = _build_client(tmp_path)
    async def fake_complete(**kwargs):
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            if any(message.get("role") == "tool" for message in kwargs["messages"]):
                return ModelResponse(text="Worker failed")
            return ModelResponse(tool_calls=[ModelToolCall("dispatch", "invoke_worker", {"worker": "search_worker", "task": "find Gamma"})])
        return ModelResponse(text="partial", tool_calls=[ModelToolCall("partial", "db_query_tool", {"page": 1, "page_size": 3})], error={"type": "incomplete_response", "message": "length"})
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = client.app.state.container.replay_buffer.list_events(response["session_id"])
    assert any(event.event == "worker.failed" for event in events)
    assert not any(event.event == "tool.started" and event.data.get("call_id") == "partial" for event in events)
    assert any(event.event == "tool.failed" and event.data.get("call_id") == "dispatch" for event in events)


def test_route_metrics_without_geometry_does_not_invent_polyline() -> None:
    from app.agent.tools.mcp.dispatcher import _extract_route_from_mapping
    route = _extract_route_from_mapping({"distance": 100, "duration": 90}, remote_name="maps_direction_walking", raw_arguments={"origin": "116,39", "destination": "116.01,39.01"}, depth=0)
    assert route is not None
    assert route.distance_m == 100
    assert route.polyline == []
