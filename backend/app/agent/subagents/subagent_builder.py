"""Subagent profile registry used by ReAct runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from app.protocol.messages import IntentType

SubAgentName = Literal["main_agent", "search_worker", "navigation_worker"]


@dataclass(frozen=True)
class SubAgentProfile:
    """Execution profile for one subagent."""

    name: SubAgentName
    prompt_file: str
    allowed_tools: list[str]
    skill_files: list[str] = field(default_factory=list)


class SubAgentBuilder:
    """Resolve hub/worker agent profiles by name and routing hints."""

    def __init__(
        self,
        *,
        definitions_dir: Path | None = None,
        enable_yaml_overlay: bool = True,
    ) -> None:
        self._profiles: dict[str, SubAgentProfile] = {
            "main_agent": SubAgentProfile(
                name="main_agent",
                prompt_file="main_agent.md",
                allowed_tools=[
                    "invoke_worker",
                    "db_query_tool",
                    "geo_resolve_tool",
                    "route_plan_tool",
                    "summary_tool",
                    "mcp__*",
                ],
                skill_files=[],
            ),
            "search_worker": SubAgentProfile(
                name="search_worker",
                prompt_file="search_worker.md",
                allowed_tools=["db_query_tool", "mcp__*"],
                skill_files=["search_result_reading.md"],
            ),
            "navigation_worker": SubAgentProfile(
                name="navigation_worker",
                prompt_file="navigation_worker.md",
                allowed_tools=[
                    "db_query_tool",
                    "geo_resolve_tool",
                    "route_plan_tool",
                    "mcp__*",
                ],
                skill_files=["search_result_reading.md", "navigation_result_reading.md"],
            ),
        }
        if enable_yaml_overlay and definitions_dir is not None:
            self._apply_yaml_overlay(definitions_dir)

    def get(self, name: str) -> SubAgentProfile:
        return self._profiles.get(name, self._profiles["main_agent"])

    def resolve_initial(self, intent: IntentType | None) -> SubAgentName:
        _ = intent
        return "main_agent"

    def _apply_yaml_overlay(self, definitions_dir: Path) -> None:
        mapping: dict[str, SubAgentName] = {
            "intent": "main_agent",
            "query": "search_worker",
            "navigation": "navigation_worker",
            "summary": "main_agent",
        }
        if not definitions_dir.exists():
            return
        for path in sorted(definitions_dir.glob("*.yaml")):
            payload = self._read_yaml(path)
            if payload is None:
                continue
            status = self._read_status(payload)
            if status != "active":
                continue
            subagent_name = self._read_subagent_name(payload, mapping=mapping)
            if subagent_name is None:
                continue

            profile = self._profiles[subagent_name]
            prompt_file = self._read_prompt_file(payload, fallback=profile.prompt_file)
            overlay_tools = self._read_allowed_tools(payload)
            allowed_tools_mode = self._read_allowed_tools_mode(payload)
            if allowed_tools_mode == "replace" and overlay_tools:
                allowed_tools = overlay_tools
            else:
                allowed_tools = self._merge_unique(profile.allowed_tools, overlay_tools)
            skill_files = self._merge_unique(profile.skill_files, self._read_skill_files(payload))
            self._profiles[subagent_name] = SubAgentProfile(
                name=profile.name,
                prompt_file=prompt_file,
                allowed_tools=allowed_tools,
                skill_files=skill_files,
            )

    def _read_yaml(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _read_prompt_file(self, payload: dict[str, Any], *, fallback: str) -> str:
        raw = payload.get("prompt_file")
        if not isinstance(raw, str):
            return fallback
        value = raw.strip()
        return value or fallback

    def _read_status(self, payload: dict[str, Any]) -> str:
        raw = payload.get("status")
        if not isinstance(raw, str):
            return "active"
        value = raw.strip().lower()
        if value in {"active", "planned"}:
            return value
        return "active"

    def _read_subagent_name(
        self,
        payload: dict[str, Any],
        *,
        mapping: dict[str, SubAgentName],
    ) -> SubAgentName | None:
        raw_name = payload.get("subagent_name")
        if isinstance(raw_name, str):
            normalized = raw_name.strip()
            if normalized in self._profiles:
                return cast(SubAgentName, normalized)

        raw_id = payload.get("id")
        if isinstance(raw_id, str):
            mapped = mapping.get(raw_id.strip())
            if mapped is not None:
                return mapped
        return None

    def _read_allowed_tools_mode(self, payload: dict[str, Any]) -> str:
        raw = payload.get("allowed_tools_mode")
        if not isinstance(raw, str):
            return "merge"
        value = raw.strip().lower()
        if value in {"merge", "replace"}:
            return value
        return "merge"

    def _read_allowed_tools(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("allowed_tools")
        if not isinstance(raw, list):
            return []
        tools: list[str] = []
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    tools.append(value)
        return tools

    def _read_skill_files(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("skill_files")
        if not isinstance(raw, list):
            return []
        skill_files: list[str] = []
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    skill_files.append(value)
        return skill_files

    def _merge_unique(self, base: list[str], overlay: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*base, *overlay]:
            if item not in merged:
                merged.append(item)
        return merged
