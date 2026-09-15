"""Production container with explicit, isolated evaluation dependencies."""
from copy import deepcopy
from dataclasses import asdict
import asyncio
from time import perf_counter
from uuid import uuid4

from app.agent.events.replay_buffer import ReplayBuffer
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.runtime.session_state import AgentSessionState, _client_can_access, _client_matches_list_scope
from app.agent.tools.mcp_gateway import MCPToolGateway
from app.core.config import Settings
from app.core.container import build_container
from evaluate.config import ROOT


class BudgetExceeded(RuntimeError):
    pass


class MemorySessions:
    def __init__(self):
        self.states = {}

    def health(self):
        return {"backend": "memory", "rows": len(self.states)}

    def get_or_create_session(self, session_id):
        return deepcopy(self.states.get(session_id) or AgentSessionState(session_id=session_id))

    def get_session(self, session_id, *, client_id=None):
        state = self.states.get(session_id)
        return deepcopy(state) if state and _client_can_access(state, client_id) else None

    def save_session(self, state):
        self.states[state.session_id] = deepcopy(state)

    def list_sessions(self, *, limit=50, client_id=None):
        return sorted([deepcopy(s) for s in self.states.values() if _client_matches_list_scope(s, client_id)],
                      key=lambda s: s.updated_at, reverse=True)[:limit]

    def delete_session(self, session_id, *, client_id=None):
        if self.get_session(session_id, client_id=client_id) is None:
            return False
        del self.states[session_id]
        return True


class Budget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def reserve(self):
        if self.used >= self.limit:
            raise BudgetExceeded("request_budget_exhausted")
        self.used += 1


class RecordedProvider(ProviderAdapter):
    def __init__(self, profile, budget, per_attempt, interval, write, attempt_id, role="agent"):
        super().__init__(profile)
        self.budget, self.per_attempt, self.interval = budget, per_attempt, interval
        self.write, self.attempt_id, self.role = write, attempt_id, role
        self.calls, self.records = 0, []

    async def complete(self, **kwargs):
        if self.calls >= self.per_attempt:
            raise BudgetExceeded("attempt_call_budget_exhausted")
        if self.interval:
            await asyncio.sleep(self.interval)
        self.budget.reserve()
        self.calls += 1
        record = {"attempt_id": self.attempt_id, "role": self.role, "call_index": self.calls,
                  "evidence_id": f"{self.attempt_id}/{self.role}/{uuid4().hex}",
                  "requested_model": self._config.model, "api_mode": self._config.api_mode,
                  "request": kwargs}
        self.write("requests", record)
        start = perf_counter()
        try:
            response = await super().complete(**kwargs)
            record = {**record, "response": asdict(response)}
            return response
        except BaseException as exc:
            record = {**record, "response": {"error": {"type": type(exc).__name__}, "usage": {}}}
            raise
        finally:
            record["duration_ms"] = (perf_counter() - start) * 1000
            self.records.append(record)
            self.write("calls", record)


class RecordedReplay(ReplayBuffer):
    def __init__(self, write, attempt_id, tool_limit):
        super().__init__()
        self.write, self.attempt_id, self.tool_limit = write, attempt_id, tool_limit
        self.tools = 0

    def append(self, session_id, event_name, data=None):
        if event_name == "tool.started":
            if self.tools >= self.tool_limit:
                raise BudgetExceeded("tool_budget_exhausted")
            self.tools += 1
        event = super().append(session_id, event_name, data)
        self.write("events", {"attempt_id": self.attempt_id, "event": event.model_dump(mode="json")})
        return event


def environment(config, model, budget, directory, attempt_id, write):
    provider = RecordedProvider(model, budget, config.per_attempt, config.interval_s, write, attempt_id)
    sessions = MemorySessions()
    app = ROOT / "backend/app"
    # Construct Settings directly: Settings.from_env() would load production .env.
    settings = Settings(
        env="evaluation", data_jsonl_path=config.data, agent_max_steps=config.per_attempt,
        agent_nodes_definitions_dir=app / "agent/nodes/definitions",
        agent_tool_policy_file=app / "agent/nodes/profiles/tool_policies.yaml",
        arcade_geo_cache_path=directory / "geo-cache.json", arcade_geo_sync_limit=0,
        amap_api_key=config.values.get("EVAL_AMAP_API_KEY", "") if config.map_mode == "live" else "",
        amap_timeout_seconds=min(8, config.wall_s),
    )
    container = build_container(settings, session_store=sessions, provider_adapter=provider,
                                mcp_tool_gateway=MCPToolGateway(servers=[]))
    # Avoid synchronous geocoding inside DTO mapping; route REST remains opt-in.
    from app.services.arcade_geo_resolver import ArcadeGeoResolver, ArcadeGeoResolverConfig
    from app.services.arcade_payload_mapper import ArcadePayloadMapper
    mapper = ArcadePayloadMapper(geo_resolver=ArcadeGeoResolver(config=ArcadeGeoResolverConfig(
        api_key="", base_url="https://fixture.invalid", cache_path=directory / "geo-cache.json",
        request_timeout_seconds=1, sync_limit=0, max_workers=1)))
    container.arcade_payload_mapper = mapper
    container.react_runtime._arcade_payload_mapper = mapper
    replay = RecordedReplay(write, attempt_id, config.max_tools)
    container.replay_buffer = replay
    container.react_runtime._replay_buffer = replay
    return container, provider
