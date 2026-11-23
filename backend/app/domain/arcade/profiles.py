"""Agent definitions owned by the arcade plugin.

These are the default profiles for the generic runtime. YAML files in
``resources/agents`` overlay them when the plugin config enables the overlay;
the prompt files, skill roots and MCP server configs live beside them under
``resources/``. The generic agent package never sees these names or paths.
"""

from __future__ import annotations

from pathlib import Path

from app.agent.plugins import AgentDefinition, AgentRoster

RESOURCES = Path(__file__).resolve().parent / "resources"
PROMPTS_DIR = RESOURCES / "prompts"
AGENTS_DIR = RESOURCES / "agents"
SKILLS_DIR = RESOURCES / "skills"
MCP_SERVERS_DIR = RESOURCES / "mcp_servers"

# Legacy node ids still stored in sessions and YAML overlays.
YAML_ALIASES = {
    "intent": "main_agent",
    "query": "search_worker",
    "navigation": "navigation_worker",
    "summary": "main_agent",
}

DEFAULT_ROSTER = AgentRoster(
    agents=(
        AgentDefinition(
            name="main_agent",
            prompt="main_agent.md",
            role="entry",
            workers=("search_worker", "navigation_worker"),
            allowed_tools=(
                "list_skills",
                "read_skill",
                "invoke_worker",
                "db_query_tool",
                "geo_resolve_tool",
                "route_plan_tool",
                "summary_tool",
                "result_selection_tool",
                "mcp__*",
            ),
        ),
        AgentDefinition(
            name="search_worker",
            prompt="search_worker.md",
            role="worker",
            allowed_tools=(
                "list_skills",
                "read_skill",
                "db_query_tool",
                "result_selection_tool",
                "mcp__*",
            ),
        ),
        AgentDefinition(
            name="navigation_worker",
            prompt="navigation_worker.md",
            role="worker",
            allowed_tools=(
                "list_skills",
                "read_skill",
                "db_query_tool",
                "geo_resolve_tool",
                "route_plan_tool",
                "mcp__*",
            ),
        ),
    ),
    base_prompt="system_base.md",
    aliases=YAML_ALIASES,
)


def arcade_agent_roster(*, yaml_overlay: bool = True, definitions_dir: Path | None = None) -> AgentRoster:
    """Build the arcade agent roster, optionally applying the YAML overlay."""
    if not yaml_overlay:
        return DEFAULT_ROSTER
    return AgentRoster.from_yaml(definitions_dir or AGENTS_DIR, aliases=YAML_ALIASES, base=DEFAULT_ROSTER)


__all__ = [
    "AGENTS_DIR",
    "DEFAULT_ROSTER",
    "MCP_SERVERS_DIR",
    "PROMPTS_DIR",
    "RESOURCES",
    "SKILLS_DIR",
    "YAML_ALIASES",
    "arcade_agent_roster",
]
