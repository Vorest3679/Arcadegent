"""Agent roster overlay and kernel-facing subagent policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.subagents.roster import AgentDefinition, AgentRoster, AgentRosterError
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.domain.arcade.profiles import AGENTS_DIR, DEFAULT_ROSTER, YAML_ALIASES, arcade_agent_roster


def test_arcade_roster_uses_default_profiles_and_yaml_overlay() -> None:
    roster = arcade_agent_roster()
    builder = SubAgentBuilder(roster.agents, aliases=roster.aliases)

    main_profile = builder.get("main_agent")
    search_profile = builder.get("search_worker")
    nav_profile = builder.get("navigation_worker")

    assert main_profile.prompt == "main_agent.md"
    assert main_profile.role == "entry"
    assert main_profile.workers == ("search_worker", "navigation_worker")
    assert main_profile.allowed_tools == (
        "list_skills",
        "read_skill",
        "invoke_worker",
        "db_query_tool",
        "geo_resolve_tool",
        "route_plan_tool",
        "summary_tool",
        "result_selection_tool",
        "mcp__*",
    )

    assert search_profile.prompt == "search_worker.md"
    assert search_profile.role == "worker"
    assert search_profile.workers == ()
    assert search_profile.allowed_tools == (
        "list_skills",
        "read_skill",
        "db_query_tool",
        "result_selection_tool",
        "mcp__*",
    )

    assert nav_profile.prompt == "navigation_worker.md"
    assert nav_profile.role == "worker"
    assert nav_profile.allowed_tools == (
        "list_skills",
        "read_skill",
        "db_query_tool",
        "geo_resolve_tool",
        "route_plan_tool",
        "mcp__*",
    )

    assert builder.node("main_agent").spawnable == ("search_worker", "navigation_worker")
    assert builder.node("search_worker").spawnable == ()
    assert builder.node("search_worker").scope == "worker"
    assert builder.get("query").name == "search_worker"


def test_roster_can_skip_the_yaml_overlay() -> None:
    assert arcade_agent_roster(yaml_overlay=False) is DEFAULT_ROSTER


def test_yaml_overlay_updates_prompt_tools_and_workers(tmp_path: Path) -> None:
    base = AgentRoster(
        agents=(
            AgentDefinition(
                name="main_agent", prompt="main.md", allowed_tools=("keep",), role="entry", workers=("worker",)
            ),
            AgentDefinition(name="worker", prompt="worker.md", allowed_tools=("old",)),
            AgentDefinition(name="other", prompt="other.md", allowed_tools=("other",)),
        ),
        base_prompt="system.md",
    )
    (tmp_path / "main.yaml").write_text(
        "id: intent\n"
        "status: active\n"
        "prompt_file: overlay-main.md\n"
        "allowed_tools_mode: replace\n"
        "allowed_tools: [list_skills]\n"
        "workers: [worker, other]\n",
        encoding="utf-8",
    )
    (tmp_path / "worker.yaml").write_text(
        "id: query\nstatus: planned\nprompt_file: ignored.md\n",
        encoding="utf-8",
    )

    roster = AgentRoster.from_yaml(tmp_path, aliases={"intent": "main_agent", "query": "worker"}, base=base)
    builder = SubAgentBuilder(roster.agents, aliases=roster.aliases)

    assert builder.get("main_agent").prompt == "overlay-main.md"
    assert builder.get("main_agent").allowed_tools == ("list_skills",)
    assert builder.get("main_agent").workers == ("worker", "other")
    assert builder.get("worker").prompt == "worker.md"  # planned files are skipped
    assert roster.base_prompt == "system.md"


def test_roster_is_immutable() -> None:
    roster = arcade_agent_roster()

    assert isinstance(roster.agents[0].allowed_tools, tuple)
    with pytest.raises(TypeError):
        roster.aliases["intent"] = "search_worker"  # type: ignore[index]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("id: [unclosed\n", "invalid_agent_yaml"),
        ("- just\n- a list\n", "invalid_agent_yaml"),
        ("id: query\nskill_files: []\n", "unsupported_agent_yaml_key:skill_files"),
    ],
)
def test_invalid_overlay_files_fail_instead_of_being_skipped(tmp_path: Path, text: str, code: str) -> None:
    (tmp_path / "query.yaml").write_text(text, encoding="utf-8")

    with pytest.raises(AgentRosterError, match=rf"^{code}$"):
        AgentRoster.from_yaml(tmp_path, aliases=YAML_ALIASES, base=DEFAULT_ROSTER)


def test_missing_overlay_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(AgentRosterError, match=r"^missing_agent_definitions_dir$"):
        arcade_agent_roster(definitions_dir=tmp_path / "missing")


def test_builder_requires_one_entry_and_existing_workers() -> None:
    with pytest.raises(ValueError, match="entry_agent_required"):
        SubAgentBuilder([AgentDefinition(name="worker", prompt="w.md", allowed_tools=())])

    with pytest.raises(ValueError, match="multiple_entry_agents"):
        SubAgentBuilder([
            AgentDefinition(name="a", prompt="a.md", allowed_tools=(), role="entry"),
            AgentDefinition(name="b", prompt="b.md", allowed_tools=(), role="entry"),
        ])

    with pytest.raises(ValueError, match="unknown_worker:main:ghost"):
        SubAgentBuilder([
            AgentDefinition(name="main", prompt="m.md", allowed_tools=(), role="entry", workers=("ghost",)),
        ])


def test_builder_resolves_real_names_before_aliases() -> None:
    agents = [
        AgentDefinition(name="main", prompt="m.md", allowed_tools=(), role="entry", workers=("search",)),
        AgentDefinition(name="search", prompt="s.md", allowed_tools=()),
    ]

    with pytest.raises(ValueError, match=r"^alias_shadows_agent:search$"):
        SubAgentBuilder(agents, aliases={"search": "main"})
    with pytest.raises(ValueError, match=r"^unknown_alias_target:query:ghost$"):
        SubAgentBuilder(agents, aliases={"query": "ghost"})

    builder = SubAgentBuilder(agents, aliases={"query": "search"})
    assert builder.get("search").name == "search"
    assert builder.get("query").name == "search"
    assert builder.get("unknown").name == "main"


def test_repository_overlay_targets_the_bundled_definitions() -> None:
    assert (AGENTS_DIR / "intent.yaml").exists()
    roster = AgentRoster.from_yaml(AGENTS_DIR, aliases=YAML_ALIASES)
    assert {agent.name for agent in roster.agents} == {"main_agent", "search_worker", "navigation_worker"}
