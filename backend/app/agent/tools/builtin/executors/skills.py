"""Manifest-registered skill tools backed by the injected catalog service."""

from app.agent.skills.execution import current_skill_invocation
from app.agent.skills.registry import SkillRegistry
from app.agent.tools.builtin.provider import BuiltinToolContext


def list_skills(context: BuiltinToolContext, args: dict) -> dict:
    invocation = current_skill_invocation()
    registry: SkillRegistry = context.require("skill_registry")
    registry.refresh()
    return {"skills": registry.list_skills(agent_name=invocation.agent_name)}


def read_skill(context: BuiltinToolContext, args: dict) -> dict:
    invocation = current_skill_invocation()
    registry: SkillRegistry = context.require("skill_registry")
    return invocation.execution.load(
        registry, agent_name=invocation.agent_name,
        name=args["name"], path=args.get("path", "SKILL.md"),
    )
