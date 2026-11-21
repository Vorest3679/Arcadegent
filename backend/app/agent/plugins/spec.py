"""Plugin declarations: packaging and assembly units for the generic agent layer.

A plugin groups what used to be wired into the container item by item:
lifecycle hooks and local tools today; skills, MCP servers, context sections
and agent definitions in later stages. The host builds plugin instances with
their dependencies and installs them; the generic layer never imports a
business plugin.

Only declarations live here; installation is :mod:`app.agent.plugins.host`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.tools.local import LocalToolProvider

_PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class PluginError(ValueError):
    """A plugin declaration or installation failed.

    Messages follow ``plugin:<name>:<code>[:detail]`` and never contain
    configuration values, paths or upstream payloads.
    """


class PluginConfig(BaseModel):
    """Base class for plugin configuration models: unknown keys fail, values are frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyConfig(PluginConfig):
    """Configuration of a plugin that declares none."""


@dataclass(frozen=True)
class PluginInfo:
    """Identity of one plugin."""

    name: str
    version: str
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _PLUGIN_NAME.fullmatch(self.name):
            raise PluginError("plugin:invalid_name")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise PluginError(f"plugin:{self.name}:invalid_version")


class Plugin:
    """Base class for a plugin.

    Subclasses set :attr:`info`, optionally :attr:`config_model`, receive their
    dependencies through ``__init__`` and override only the build members they
    contribute. Build members are pure constructors: they return ready objects
    and must not mutate host registries.

    Later stages add ``skills``/``mcp_servers`` resources, ``context`` sections
    and ``agents`` here, together with the adapters that install them.
    """

    info: ClassVar[PluginInfo]
    config_model: ClassVar[type[PluginConfig]] = EmptyConfig

    def hooks(self, config: PluginConfig) -> BaseTurnDomain | None:
        return None

    def tools(self, config: PluginConfig) -> LocalToolProvider | None:
        return None


__all__ = ["EmptyConfig", "Plugin", "PluginConfig", "PluginError", "PluginInfo"]
