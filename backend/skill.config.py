"""Project skill configuration. Restart the backend after editing this file."""

from app.agent.skills.config import SkillConfig

config = SkillConfig(
    roots=["app/agent/context/skills"],
    disabled_skills=[],
    # Unlisted agents see all skills; an empty list disables skills for an agent.
    agent_skills={},
    max_file_bytes=64 * 1024,
    max_loaded_bytes=256 * 1024,
)
