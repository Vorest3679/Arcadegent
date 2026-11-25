"""Plugin declarations: packaging and assembly units for the generic agent layer.

A plugin groups what used to be wired into the container item by item:
lifecycle hooks, local tools, skills, MCP server directories, prompt
directories and agent definitions. The host builds plugin instances with
their dependencies and installs them; the generic layer never imports a
business plugin.

Resource paths are declarations, so the host can read them before constructing
services that depend on them (the skill registry or the MCP gateway). Build
members are pure constructors and must not mutate host registries.

Only declarations live here; installation is :mod:`app.agent.plugins.host`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.subagents.roster import AgentRoster
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.mcp.ports import MCPResultNormalizer, MCPToolSelector

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
class PluginResources:
    """Directories a plugin contributes to host registries.

    ``skills`` holds skill roots, ``mcp_servers`` holds directories of MCP
    server JSON files and ``prompts`` holds the directories this plugin's agent
    prompts resolve against. Prompt lookups never cross plugins. A plugin may
    override the contents in :meth:`Plugin.resolved_resources` using its
    validated configuration.

    ``mcp_selectors`` and ``mcp_normalizers`` are keyed by selector name and
    MCP server name respectively. The host collects them into the MCP gateway;
    names are globally unique across installed plugins.
    """

    skills: tuple[Path, ...] = ()
    mcp_servers: tuple[Path, ...] = ()
    prompts: tuple[Path, ...] = ()
    mcp_selectors: Mapping[str, MCPToolSelector] = field(default_factory=dict)
    mcp_normalizers: Mapping[str, MCPResultNormalizer] = field(default_factory=dict)


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

    Subclasses set :attr:`info`, optionally :attr:`config_model` and
    :attr:`resources`, receive their dependencies through ``__init__`` and
    override only the members they contribute. Build members are pure
    constructors: they return ready objects and must not mutate host
    registries.

    Every member receives a validated instance of :attr:`config_model`; the
    host checks the type before calling. Members are typed ``config: Any`` here
    so a subclass annotates its own model directly (``config: MyConfig``)
    without casts or asserts.
    """

    info: ClassVar[PluginInfo]
    config_model: ClassVar[type[PluginConfig]] = EmptyConfig
    resources: ClassVar[PluginResources] = PluginResources()

    @classmethod
    def resolved_resources(cls, config: Any) -> PluginResources:
        """Return this plugin's resource directories for one validated config."""
        return cls.resources

    def hooks(self, config: Any) -> BaseTurnDomain | None:
        return None

    def tools(self, config: Any) -> LocalToolProvider | None:
        return None

    def agents(self, config: Any) -> AgentRoster:
        return AgentRoster()


__all__ = [
    "EmptyConfig",
    "Plugin",
    "PluginConfig",
    "PluginError",
    "PluginInfo",
    "PluginResources",
]
