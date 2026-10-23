"""Regression guards for production evidence, accounting and failed preparations."""

import asyncio

import pytest

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.context.turn_recorder import merge_usage_totals, record_model_call
from app.agent.runtime.events import bind_run_events, reset_run_events
from app.agent.runtime.kernel import TurnInput
from app.agent.runtime.react_runtime import ReactRuntime
from app.agent.runtime.session_state import AgentSessionState, state_from_dict, state_to_dict
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.domain.arcade.domain import ArcadeTurnDomain
from app.protocol.messages import ChatRequest
from app.session.injector import CollectingPublisher


def test_model_and_worker_usage_count_each_request_once_and_keep_missing_evidence():
    parent = AgentSessionState(session_id="parent")
    worker = AgentSessionState(session_id="worker")
    response = ModelResponse(status="completed", usage={"input_tokens": 100, "output_tokens": 30,
                             "total_tokens": 130, "cached_input_tokens": 60, "reasoning_tokens": 20})
    record_model_call(state=parent, response=response, agent_name="main_agent", step=1)
    record_model_call(state=worker, response=response, agent_name="search_worker", step=1,
                      worker_run_id="worker-1")
    record_model_call(state=worker, response=ModelResponse(error={"type": "timeout"}),
                      agent_name="search_worker", step=2, worker_run_id="worker-1")
    merge_usage_totals(parent_memory=parent.working_memory, worker_memory=worker.working_memory)
    assert parent.working_memory["usage_totals"] == {
        "calls": 3, "input_tokens": 200, "output_tokens": 60,
        "cached_input_tokens": 120, "reasoning_tokens": 40, "usage_missing": 1,
    }
    missing = worker.turns[-1].payload["model"]
    assert missing["usage"] == {} and missing["raw_usage"] is None
    assert missing["error"] == {"type": "timeout"}
    restored = state_from_dict(state_to_dict(worker))
    assert restored.turns[-1].payload == worker.turns[-1].payload
    assert restored.turns[-1].worker_run_id == "worker-1"


@pytest.mark.parametrize("failure", ["unknown_tool", "prepare_failed", "invalid_json"])
def test_preparation_failure_closes_event_pair_and_preserves_raw_arguments(failure, tmp_path):
    runtime = object.__new__(ReactRuntime)
    runtime._domain = ArcadeTurnDomain()
    runtime._tool_registry = ToolRegistry(providers=[], permission_checker=ToolPermissionChecker(
        policy_file=tmp_path / "no-policy.yaml"))
    if failure == "prepare_failed":
        async def broken(**kwargs):
            raise ValueError("fixture preparation failure")
        runtime._tool_registry.prepare_arguments = broken
    state = AgentSessionState(session_id="fixture")
    raw = "{" if failure == "invalid_json" else '{"city_name":"上海"}'
    call = ModelToolCall("c1", "nonexistent_tool", {"city_name": "上海"}, raw_arguments=raw,
                         parse_error="invalid JSON" if failure == "invalid_json" else None)
    published = CollectingPublisher()
    token = bind_run_events(published)
    request = ChatRequest(message="查找机厅")
    turn = TurnInput(
        session_id=None,
        client_id=None,
        message=request.message,
        request=request,
        request_payload=request.model_dump(mode="json"),
    )
    try:
        asyncio.run(runtime._execute_tool_calls(
            session_id=state.session_id, turn=turn, session_state=state,
            tool_calls=[call], profile=SubAgentBuilder().get("main_agent"), persist=False,
        ))
    finally:
        reset_run_events(token)
    assert published.names() == ["tool.started", "tool.failed"]
    assert all(data["call_id"] == "c1" for _name, data, _output_id in published.events)
    turn = state.turns[-1]
    assert turn.payload["status"] == "failed"
    evidence = turn.payload["argument_evidence"]
    assert evidence["raw_arguments"] == raw
    assert evidence["hydrated_fields"] == []
    assert evidence["preparation_error"]
    assert state_from_dict(state_to_dict(state)).turns[-1].payload == turn.payload
