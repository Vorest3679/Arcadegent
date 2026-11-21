"""Host-provided core plugin: generic tools every application can rely on.

``CorePlugin`` owns the manifest adapter that loads ``list_skills``,
``read_skill`` and ``invoke_worker``. ``invoke_worker`` declares
``spawn_agent``, so the host must install this plugin as trusted.
"""

from __future__ import annotations

from typing import Any

from app.agent.plugins.spec import Plugin, PluginConfig, PluginInfo
from app.agent.tools.builtin import BuiltinToolProvider


class CorePlugin(Plugin):
    info = PluginInfo(name="core", version="1.0.0", description="Generic host-provided agent tools.")

    def __init__(self, *, skill_registry: Any) -> None:
        self._skill_registry = skill_registry

    def tools(self, config: PluginConfig) -> BuiltinToolProvider:
        return BuiltinToolProvider(services={"skill_registry": self._skill_registry})


__all__ = ["CorePlugin"]
