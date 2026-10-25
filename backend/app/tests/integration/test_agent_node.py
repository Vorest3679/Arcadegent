"""Unit tests for the kernel-facing AgentNode policy."""

from __future__ import annotations

from app.agent.subagents.subagent_builder import SubAgentBuilder


def test_main_node_can_spawn_all_registered_workers() -> None:
    builder = SubAgentBuilder()

    node = builder.node("main_agent")

    assert node.scope == "conversation"
    assert node.publish_text is True
    assert node.spawnable == builder.worker_names()


def test_worker_node_cannot_spawn_by_default() -> None:
    builder = SubAgentBuilder()

    node = builder.node("search_worker")

    assert node.scope == "worker"
    assert node.publish_text is False
    assert node.spawnable == ()


def test_yaml_overlay_sets_node_model_and_effort(tmp_path) -> None:
    (tmp_path / "query.yaml").write_text(
        "id: query\nsubagent_name: search_worker\nmodel: m-small\nreasoning_effort: low\n",
        encoding="utf-8",
    )

    builder = SubAgentBuilder(definitions_dir=tmp_path)

    assert builder.node("search_worker").model == "m-small"
    assert builder.node("search_worker").reasoning_effort == "low"
    assert builder.node("main_agent").model is None
