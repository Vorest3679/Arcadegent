"""Host-provided core plugin: generic tools and skill policy.

``CorePlugin`` owns the manifest adapter that loads ``list_skills``,
``read_skill`` and ``invoke_worker``, and the host-wide skill policy. Its
``skills`` config controls disabled skills, per-agent allow lists and read
limits; skill roots come from ``PluginResources.skills``. ``invoke_worker``
declares ``spawn_agent``, so the host must install this plugin as trusted.
"""

from __future__ import annotations

from typing import Any

from app.agent.plugins.spec import Plugin, PluginConfig, PluginInfo
from app.agent.skills.config import SkillSettings
from app.agent.tools.builtin import BuiltinToolProvider


class CoreSkillsConfig(SkillSettings):
    """Skill policy contributed by the core plugin (same fields as ``SkillSettings``)."""


class CoreConfig(PluginConfig):
    """Core plugin configuration; only the host-wide skill policy is configurable."""

    skills: CoreSkillsConfig = CoreSkillsConfig()


class CorePlugin(Plugin):
    info = PluginInfo(name="core", version="1.0.0", description="Generic host-provided agent tools.")
    config_model = CoreConfig

    def __init__(self, *, skill_registry: Any) -> None:
        self._skill_registry = skill_registry

    def tools(self, config: CoreConfig) -> BuiltinToolProvider:
        return BuiltinToolProvider(services={"skill_registry": self._skill_registry})


__all__ = ["CoreConfig", "CorePlugin", "CoreSkillsConfig"]
