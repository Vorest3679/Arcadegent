"""Unit tests for the kernel-facing AgentNode policy."""

from __future__ import annotations

from pathlib import Path

from app.agent.subagents.roster import AgentDefinition, AgentRoster
from app.agent.subagents.subagent_builder import SubAgentBuilder


def _roster(*, model: str | None = None) -> AgentRoster:
    return AgentRoster(agents=(
        AgentDefinition(
            name="main_agent",
            prompt="main.md",
            allowed_tools=(),
            role="entry",
            workers=("search_worker",),
            model=model,
        ),
        AgentDefinition(name="search_worker", prompt="search.md", allowed_tools=()),
    ))


def test_entry_node_spawns_its_declared_workers() -> None:
    builder = SubAgentBuilder(_roster().agents)

    node = builder.node("main_agent")

    assert node.scope == "conversation"
    assert node.publish_text is True
    assert node.spawnable == ("search_worker",)
    assert node.spawnable == builder.worker_names()


def test_worker_node_cannot_spawn_by_default() -> None:
    builder = SubAgentBuilder(_roster().agents)

    node = builder.node("search_worker")

    assert node.scope == "worker"
    assert node.publish_text is False
    assert node.spawnable == ()


def test_yaml_overlay_sets_node_model_and_effort(tmp_path: Path) -> None:
    (tmp_path / "query.yaml").write_text(
        "id: query\nsubagent_name: search_worker\nmodel: m-small\nreasoning_effort: low\n",
        encoding="utf-8",
    )

    roster = AgentRoster.from_yaml(
        tmp_path,
        aliases={"query": "search_worker"},
        base=_roster(),
    )
    builder = SubAgentBuilder(roster.agents, aliases=roster.aliases)

    assert builder.node("search_worker").model == "m-small"
    assert builder.node("search_worker").reasoning_effort == "low"
    assert builder.node("main_agent").model is None
