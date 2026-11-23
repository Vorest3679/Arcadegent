"""Install plugins into one immutable ``PluginSet``.

:func:`load_plugins` is the host's single entry point: it reads the plugins
file, keeps the enabled plugins in file order, validates their configs, checks
required plugins and installs them. :func:`install_plugins` is the step that
takes already-chosen entries (tests and embedders that build configs in code).

Installation is a pure function: build members run, the results are checked
against each other, and either a complete ``PluginSet`` is returned or
``PluginError("plugin:<name>:<code>[:detail]")`` is raised. Nothing is
registered anywhere until the caller hands the set to the registries, so a
failure leaves no partial state. A future reload builds a new set the same way
and swaps the reference.

Trust is decided by the host per plugin: only a trusted plugin's tools may
declare reserved capabilities (``spawn_agent`` and friends). Runtime writes to
every plugin tool provider are locked on success, so a plugin cannot add tools
after the checks.

Agent prompts resolve inside the owning plugin's ``PluginResources.prompts``
only, and every prompt must exist at installation. Aliases may only name the
plugin's own agents and never shadow a real agent name.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from app.agent.context.prompt_loader import FilePromptLoader, resolve_prompt_file
from app.agent.plugins.composite import CompositeTurnDomain
from app.agent.plugins.config import read_plugins_file, resolve_plugin_configs, secret_values
from app.agent.plugins.resources import resolve_resources
from app.agent.plugins.spec import Plugin, PluginConfig, PluginError, PluginInfo
from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.subagents.roster import AgentDefinition, AgentRoster, AgentRosterError
from app.agent.tools.local import LocalToolProvider

_T = TypeVar("_T")
_V = TypeVar("_V")


def _empty_paths() -> Mapping[str, Path]:
    return MappingProxyType({})


@dataclass(frozen=True)
class PluginEntry:
    """One plugin to install: an instance built with its dependencies, its config and trust."""

    plugin: Plugin
    config: PluginConfig | None = None  # ``None``: the plugin's default config
    trusted: bool = False


@dataclass(frozen=True)
class InstalledPlugin:
    """One installed plugin and the objects it contributed."""

    plugin: Plugin
    config: PluginConfig
    trusted: bool
    domain: BaseTurnDomain | None
    tools: LocalToolProvider | None
    agents: AgentRoster = field(default_factory=AgentRoster)
    # Each agent's prompt file, resolved in this plugin's prompt directories.
    prompt_files: Mapping[str, Path] = field(default_factory=_empty_paths)
    base_prompt_file: Path | None = None

    @property
    def name(self) -> str:
        return self.plugin.info.name

    def health(self) -> dict[str, Any]:
        """Public, payload-free description for ``/health``."""
        return {
            "name": self.name,
            "version": self.plugin.info.version,
            "trusted": self.trusted,
            "contributions": {
                "hooks": self.domain is not None,
                "tools": sorted(self.tools.entries()) if self.tools is not None else [],
                "agents": [agent.name for agent in self.agents.agents],
            },
            "secrets": {
                path: "set" if value is not None and value.get_secret_value() else "missing"
                for path, value in secret_values(self.config).items()
            },
        }


@dataclass(frozen=True)
class PluginSet:
    """Immutable result of an installation, handed to the runtime and the HTTP layer."""

    installed: tuple[InstalledPlugin, ...]
    domain: CompositeTurnDomain
    agents: AgentRoster = field(default_factory=AgentRoster)
    prompt_files: Mapping[str, Path] = field(default_factory=_empty_paths)
    base_prompt_file: Path | None = None

    @property
    def tool_providers(self) -> Mapping[str, LocalToolProvider]:
        """Plugin tool providers keyed by plugin name, in installation order."""
        return MappingProxyType({
            item.name: item.tools for item in self.installed if item.tools is not None
        })

    def prompt_loader(self) -> FilePromptLoader:
        """A loader over the prompts resolved at installation."""
        return FilePromptLoader(agents=self.prompt_files, base=self.base_prompt_file)

    def health(self) -> list[dict[str, Any]]:
        return [item.health() for item in self.installed]


def install_plugins(entries: Sequence[PluginEntry]) -> PluginSet:
    """Build every entry, check them together, and return the set or raise."""
    installed: dict[str, InstalledPlugin] = {}
    tool_owners: dict[str, str] = {}
    for entry in entries:
        item = _build(entry)
        if item.name in installed:
            raise PluginError(f"plugin:{item.name}:duplicate_plugin")
        for tool_name in item.tools.entries() if item.tools is not None else ():
            previous = tool_owners.setdefault(tool_name, item.name)
            if previous != item.name:
                raise PluginError(f"plugin:{item.name}:duplicate_tool:{tool_name}")
        installed[item.name] = item
    items = tuple(installed.values())
    domain = CompositeTurnDomain([(item.name, item.domain) for item in items if item.domain is not None])
    agents, base_prompt_file = _merge_agent_rosters(items)
    for item in items:
        if item.tools is not None:
            item.tools.lock_runtime_writes(trusted=item.trusted)
    return PluginSet(
        installed=items,
        domain=domain,
        agents=agents,
        prompt_files=MappingProxyType({
            name: path for item in items for name, path in item.prompt_files.items()
        }),
        base_prompt_file=base_prompt_file,
    )


def load_plugin_configs(
    path: Path,
    *,
    plugins: Mapping[str, type[Plugin]],
    environ: Mapping[str, str],
) -> dict[str, PluginConfig]:
    """Read and validate a plugins file without constructing plugin instances."""
    return resolve_plugin_configs(
        read_plugins_file(path),
        base_dir=path.parent,
        models={name: plugin.config_model for name, plugin in plugins.items()},
        environ=environ,
    )


def load_plugins(
    path: Path,
    *,
    plugins: Mapping[str, Plugin],
    environ: Mapping[str, str],
    trusted: Collection[str] = (),
    required: Collection[str] = (),
) -> PluginSet:
    """Read the plugins file at ``path`` and install what it enables.

    ``plugins`` are the host's plugin instances by name (the only names the
    file may use); ``trusted`` and ``required`` are host decisions the file
    cannot change. ``environ`` supplies placeholder values.
    """
    configs = load_plugin_configs(path, plugins={name: type(item) for name, item in plugins.items()}, environ=environ)
    return install_configured(plugins, configs, trusted=trusted, required=required)


def require_enabled_plugins(configs: Mapping[str, PluginConfig], required: Collection[str]) -> None:
    """Fail when a host-required plugin is missing or disabled."""
    for name in required:
        if name not in configs:
            # A disabled required plugin would silently drop tools or hooks
            # the host relies on, so refuse instead.
            raise PluginError(f"plugin:{name}:required_plugin_disabled")


def install_configured(
    plugins: Mapping[str, Plugin],
    configs: Mapping[str, PluginConfig],
    *,
    trusted: Collection[str] = (),
    required: Collection[str] = (),
) -> PluginSet:
    """Install the plugins named by ``configs``, in its order, with those configs."""
    for name in configs:
        if name not in plugins:
            raise PluginError(f"plugin:{name}:unknown_plugin")
    require_enabled_plugins(configs, required)
    return install_plugins([
        PluginEntry(plugin=plugins[name], config=config, trusted=name in trusted)
        for name, config in configs.items()
    ])


def _build(entry: PluginEntry) -> InstalledPlugin:
    if not isinstance(entry, PluginEntry) or not isinstance(entry.plugin, Plugin):
        raise PluginError("plugin:invalid_entry")
    plugin = entry.plugin
    info = getattr(plugin, "info", None)
    if not isinstance(info, PluginInfo):
        raise PluginError("plugin:invalid_info")
    name = info.name
    model = plugin.config_model
    if not (isinstance(model, type) and issubclass(model, PluginConfig)):
        raise PluginError(f"plugin:{name}:invalid_config_model")
    config = entry.config if entry.config is not None else model()
    if not isinstance(config, model):
        raise PluginError(f"plugin:{name}:invalid_config_type")
    resources = resolve_resources(name, type(plugin), config)

    domain = _member(name, "hooks", plugin.hooks, config)
    if domain is not None and not isinstance(domain, BaseTurnDomain):
        raise PluginError(f"plugin:{name}:invalid_hook_domain")
    tools = _member(name, "tools", plugin.tools, config)
    if tools is not None:
        if not isinstance(tools, LocalToolProvider):
            raise PluginError(f"plugin:{name}:invalid_tool_provider")
        for tool_name, tool in tools.entries().items():
            if tool.origin != "plugin":
                raise PluginError(f"plugin:{name}:untrusted_tool_origin:{tool_name}")
            if not entry.trusted and tool.spec.reserved_capabilities:
                capabilities = ",".join(sorted(tool.spec.reserved_capabilities))
                raise PluginError(f"plugin:{name}:reserved_capability:{tool_name}:{capabilities}")
    agents = _member(name, "agents", plugin.agents, config)
    if not isinstance(agents, AgentRoster):
        raise PluginError(f"plugin:{name}:invalid_agent_roster")
    prompt_files, base_prompt_file = _resolve_prompts(name, agents, resources.prompts)
    return InstalledPlugin(
        plugin=plugin,
        config=config,
        trusted=entry.trusted,
        domain=domain,
        tools=tools,
        agents=agents,
        prompt_files=prompt_files,
        base_prompt_file=base_prompt_file,
    )


def _resolve_prompts(
    name: str,
    roster: AgentRoster,
    directories: Sequence[Path],
) -> tuple[Mapping[str, Path], Path | None]:
    """Resolve the roster's prompts in this plugin's directories; a missing file fails."""
    files: dict[str, Path] = {}
    for agent in roster.agents:
        path = resolve_prompt_file(directories, agent.prompt)
        if path is None:
            raise PluginError(f"plugin:{name}:prompt_not_found:{agent.name}")
        files[agent.name] = path
    base: Path | None = None
    if roster.base_prompt is not None:
        base = resolve_prompt_file(directories, roster.base_prompt)
        if base is None:
            raise PluginError(f"plugin:{name}:base_prompt_not_found")
    return MappingProxyType(files), base


def _claim(table: dict[str, tuple[str, _V]], key: str, value: _V, owner: str, code: str) -> None:
    """Record ``owner`` as the only contributor of ``key`` (one hash lookup)."""
    claimed = (owner, value)
    current = table.setdefault(key, claimed)
    if current is not claimed:
        raise PluginError(f"plugin:{owner}:{code}:{key}:plugins:{current[0]},{owner}")


def _merge_agent_rosters(installed: Sequence[InstalledPlugin]) -> tuple[AgentRoster, Path | None]:
    """Merge rosters in one pass per table, rejecting ambiguous names, entries and aliases.

    Every table maps a key to ``(owner plugin, value)``, so a conflict names
    both plugins without a second lookup. Work is linear in the number of
    contributed agents and aliases.
    """
    agents: dict[str, tuple[str, AgentDefinition]] = {}
    entry_owner: str | None = None
    base: tuple[str, str, Path] | None = None  # (owner, prompt name, file)
    for item in installed:
        for agent in item.agents.agents:
            _claim(agents, agent.name, agent, item.name, "duplicate_agent")
            if agent.role == "entry":
                if entry_owner is not None:
                    raise PluginError(f"plugin:{item.name}:duplicate_entry_agent:plugins:{entry_owner},{item.name}")
                entry_owner = item.name
        if item.base_prompt_file is not None and item.agents.base_prompt is not None:
            if base is None:
                base = (item.name, item.agents.base_prompt, item.base_prompt_file)
            elif base[2] != item.base_prompt_file:
                raise PluginError(f"plugin:{item.name}:base_prompt_conflict:plugins:{base[0]},{item.name}")
    if agents and entry_owner is None:
        raise PluginError(f"plugin:{next(iter(agents.values()))[0]}:missing_entry_agent")

    # Aliases are checked after every real name is known, so a later plugin's
    # agent cannot be shadowed by an earlier plugin's alias either.
    aliases: dict[str, tuple[str, str]] = {}
    for item in installed:
        own = {agent.name for agent in item.agents.agents}
        for alias, target in item.agents.aliases.items():
            if alias in agents:
                raise PluginError(f"plugin:{item.name}:alias_shadows_agent:{alias}")
            if target not in own:
                raise PluginError(f"plugin:{item.name}:unknown_alias_target:{alias}:{target}")
            _claim(aliases, alias, target, item.name, "alias_conflict")

    for owner, agent in agents.values():
        for worker in agent.workers:
            claimed = agents.get(worker)
            if claimed is None or claimed[1].role != "worker":
                raise PluginError(f"plugin:{owner}:unknown_worker:{agent.name}:{worker}")

    roster = AgentRoster(
        agents=tuple(agent for _owner, agent in agents.values()),
        base_prompt=base[1] if base is not None else None,
        aliases={alias: target for alias, (_owner, target) in aliases.items()},
    )
    return roster, base[2] if base is not None else None


def _member(name: str, member: str, build: Callable[[PluginConfig], _T], config: PluginConfig) -> _T:
    try:
        return build(config)
    except PluginError as exc:
        message = str(exc)
        raise PluginError(message if message.startswith("plugin:") else f"plugin:{name}:{message}") from exc
    except AgentRosterError as exc:
        # Roster codes are payload-free by contract, so keep them actionable.
        raise PluginError(f"plugin:{name}:{exc}") from exc
    except Exception as exc:
        # The original exception may carry configuration values or paths;
        # keep it as the cause but never put its text in the public code.
        raise PluginError(f"plugin:{name}:build_failed:{member}") from exc


__all__ = [
    "InstalledPlugin",
    "PluginEntry",
    "PluginSet",
    "install_configured",
    "install_plugins",
    "load_plugin_configs",
    "load_plugins",
    "require_enabled_plugins",
]
