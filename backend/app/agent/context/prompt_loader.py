"""Prompt loading for the context builder.

The generic context layer receives a prompt source; it never computes a
project root or assumes a prompt directory relative to its own source file.
Plugin installation resolves every agent's prompt inside the owning plugin's
prompt directories and hands the resulting files to :class:`FilePromptLoader`;
:class:`DirectoryPromptLoader` serves single-directory hosts and tests.

Both loaders fail with :class:`PromptError` instead of running an agent with
an empty prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from app.agent.subagents.roster import AgentDefinition


class PromptError(ValueError):
    """A prompt could not be found; the message never contains a path."""


class PromptLoader(Protocol):
    """Prompt text for the shared base prompt and for one agent."""

    def base_prompt(self) -> str: ...

    def agent_prompt(self, agent: AgentDefinition) -> str: ...


def resolve_prompt_file(directories: Sequence[Path], name: str) -> Path | None:
    """First ``directory / name`` that is a file; absolute or escaping names never match."""
    relative = Path(name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        return None
    for directory in directories:
        path = directory / relative
        if path.is_file():
            return path
    return None


class _TextCache:
    def __init__(self) -> None:
        self._texts: dict[Path, str] = {}

    def read(self, path: Path) -> str:
        text = self._texts.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
            self._texts[path] = text
        return text


class FilePromptLoader:
    """Prompts already resolved to files: one per agent name plus an optional base."""

    def __init__(self, *, agents: Mapping[str, Path], base: Path | None = None) -> None:
        self._agents = dict(agents)
        self._base = base
        self._cache = _TextCache()

    def base_prompt(self) -> str:
        return self._cache.read(self._base) if self._base is not None else ""

    def agent_prompt(self, agent: AgentDefinition) -> str:
        path = self._agents.get(agent.name)
        if path is None:
            raise PromptError(f"prompt_not_found:{agent.name}")
        return self._cache.read(path)


class DirectoryPromptLoader:
    """Resolve prompt names against ordered directories, with a cache."""

    def __init__(self, directories: Sequence[Path], *, base_prompt: str | None = None) -> None:
        self._directories = tuple(dict.fromkeys(Path(item) for item in directories))
        self._base = base_prompt
        self._cache = _TextCache()

    def base_prompt(self) -> str:
        return self.load(self._base) if self._base else ""

    def agent_prompt(self, agent: AgentDefinition) -> str:
        return self.load(agent.prompt)

    def load(self, name: str) -> str:
        path = resolve_prompt_file(self._directories, name)
        if path is None:
            raise PromptError("prompt_not_found")
        return self._cache.read(path)


__all__ = [
    "DirectoryPromptLoader",
    "FilePromptLoader",
    "PromptError",
    "PromptLoader",
    "resolve_prompt_file",
]
