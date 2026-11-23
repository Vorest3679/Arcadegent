"""Host policy for portable skill packages.

The policy (disabled skills, per-agent allow lists and byte limits) lives in
the ``core`` plugin configuration. Roots come from the installed plugins'
``PluginResources.skills`` declarations, so a skill directory always travels
with the plugin that owns it. None of these values is read from skill files.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SkillSettings(BaseModel):
    """Skill discovery and loading limits, independent of where the roots live."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disabled_skills: list[str] = Field(default_factory=list)
    agent_skills: dict[str, list[str]] = Field(default_factory=dict)
    max_file_bytes: int = Field(default=64 * 1024, gt=0, strict=True)
    max_loaded_bytes: int = Field(default=256 * 1024, gt=0, strict=True)


class SkillConfig(SkillSettings):
    """A registry's complete configuration: host policy plus resolved roots."""

    roots: list[Path] = Field(default_factory=list)


__all__ = ["SkillConfig", "SkillSettings"]
