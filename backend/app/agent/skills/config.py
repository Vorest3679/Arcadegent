"""Trusted project configuration, separate from portable skill packages."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SkillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    roots: list[Path] = Field(default_factory=lambda: [Path("app/agent/context/skills")])
    disabled_skills: list[str] = Field(default_factory=list)
    agent_skills: dict[str, list[str]] = Field(default_factory=dict)
    max_file_bytes: int = Field(default=64 * 1024, gt=0, strict=True)
    max_loaded_bytes: int = Field(default=256 * 1024, gt=0, strict=True)


def load_skill_config(path: Path) -> SkillConfig:
    """Load the administrator-owned Python config once at application startup."""
    spec = spec_from_file_location("arcadegent_skillconfig", path)
    if spec is None or spec.loader is None:
        raise ValueError("invalid_skill_config_module")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    config = getattr(module, "config", None)
    if not isinstance(config, SkillConfig):
        raise ValueError("skill.config.py must export config = SkillConfig(...)")
    return config.model_copy(update={
        "roots": [(path.parent / root).resolve() for root in config.roots],
    })
