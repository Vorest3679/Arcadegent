"""Discover portable Agent Skills and read bounded, confined text resources."""

from __future__ import annotations

import logging
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any

import yaml

from app.agent.skills.config import SkillConfig

logger = logging.getLogger(__name__)
_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


class SkillError(ValueError):
    """Stable, payload-free error safe to expose in tool observations."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous YAML mappings instead of silently overriding fields."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise SkillError("invalid_frontmatter_keys")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def parse_skill(text: str, directory_name: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillError("missing_frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SkillError("missing_frontmatter_end")
    try:
        metadata = yaml.load("".join(lines[1:end]), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise SkillError("invalid_frontmatter_yaml") from exc
    if not isinstance(metadata, dict) or set(metadata) - _FIELDS:
        raise SkillError("invalid_frontmatter_fields")
    name = metadata.get("name")
    if not isinstance(name, str):
        raise SkillError("invalid_skill_name")
    name = unicodedata.normalize("NFKC", name.strip())
    if (not 1 <= len(name) <= 64 or name != name.lower()
            or name.startswith("-") or name.endswith("-") or "--" in name
            or not all(c.isalnum() or c == "-" for c in name)):
        raise SkillError("invalid_skill_name")
    if name != unicodedata.normalize("NFKC", directory_name):
        raise SkillError("skill_name_directory_mismatch")
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillError("invalid_skill_description")
    for key in ("license", "compatibility", "allowed-tools"):
        if key in metadata and not isinstance(metadata[key], str):
            raise SkillError("invalid_optional_field")
    if "compatibility" in metadata and not 1 <= len(metadata["compatibility"]) <= 500:
        raise SkillError("invalid_compatibility")
    if "metadata" in metadata:
        extra = metadata["metadata"]
        if not isinstance(extra, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in extra.items()
        ):
            raise SkillError("invalid_metadata")
    metadata["name"] = name
    return metadata, "".join(lines[end + 1:]).strip()


@dataclass(frozen=True)
class SkillRecord:
    root: Path
    directory: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SkillResource:
    name: str
    path: str
    content: str
    size_bytes: int


def normalize_resource_path(path: str) -> str:
    relative = PurePosixPath(path)
    if (not path or relative.is_absolute() or ".." in relative.parts
            or "\\" in path or ":" in path or "\x00" in path or relative == PurePosixPath(".")):
        raise SkillError("invalid_resource_path")
    return relative.as_posix()


def _read_text(root: Path, directory: str, path: str, limit: int) -> str:
    """在技能目录内安全地读取一个受大小限制的 UTF-8 文本文件。

    这里没有直接使用 ``Path.read_text()``，而是使用 ``os.open`` 返回的文件
    描述符（file descriptor，简称 fd）逐级打开路径。这样可以在解析已有
    符号链接后，用 ``O_NOFOLLOW`` 拒绝后续新出现的符号链接，降低读取过程
    中路径被替换而越界的风险。
    """
    descriptor: int | None = None
    try:
        # 先解析技能目录和资源路径；resolve() 允许技能内部的链接，但随后
        # 的 is_relative_to() 确保解析结果仍然位于允许的根目录/技能目录内。
        base = (root / directory).resolve(strict=True)
        if base == root or not base.is_relative_to(root):
            raise SkillError("skill_directory_outside_root")
        target = (base / path).resolve(strict=True)
        if not target.is_relative_to(base):
            raise SkillError("resource_outside_skill")

        # 以目录 fd 作为后续相对路径的锚点。O_DIRECTORY 要求打开的对象是
        # 目录，O_NOFOLLOW 防止这一层本身被替换成符号链接。
        parts = target.relative_to(root).parts # 要打开的文件路径
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        # 每次只打开一个路径组件，并把父目录 fd 传给 dir_fd；这样不会重新
        # 从进程当前目录解析整条字符串路径。打开子目录后关闭旧 fd，避免泄漏。
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child

        # 最后一段以非阻塞模式打开：即使技能目录里放入 FIFO/设备文件，也不会
        # 因为读取它而卡住。后续 fstat 还会再次确认它确实是普通文件。
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        os.close(descriptor)
        descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SkillError("resource_not_regular_file")
        if info.st_size > limit:
            raise SkillError("resource_too_large")

        # 用 fdopen 把整数 fd 包装成二进制文件对象。read(limit + 1) 多读一个字节，
        # 可检测文件在 fstat 后变大了；成功交给文件对象管理后将 descriptor 清空，
        # 防止 finally 再次 close 同一个 fd。
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise SkillError("resource_too_large")
        text = data.decode("utf-8")
        if any(ord(c) < 32 and c not in "\t\n\r\f" for c in text) or "\x7f" in text:
            raise SkillError("resource_not_text")
        return text
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise SkillError("resource_unavailable_or_not_utf8") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


class SkillRegistry:
    """Shared catalog; execution snapshots belong to the caller, never the registry."""

    def __init__(self, config: SkillConfig) -> None:
        self.config = config.model_copy(deep=True)
        self._roots = tuple(dict.fromkeys(root.resolve() for root in config.roots))
        self._records: dict[str, SkillRecord] = {}
        self._lock = RLock()
        self.refresh()

    def refresh(self) -> None:
        """Scan all roots and update the skill catalog."""
        with self._lock:
            records: dict[str, SkillRecord] = {}
            collisions: set[str] = set()
            for root_index, root in enumerate(self._roots):
                try:
                    children = sorted(root.iterdir())
                except OSError:
                    logger.warning("skill_root_unavailable root_index=%s", root_index)
                    continue
                for child in children:
                    if child.name.startswith(".") or child.name == "node_modules":
                        continue
                    if not child.is_dir() or not (child / "SKILL.md").exists():
                        continue
                    try:
                        text = _read_text(root, child.name, "SKILL.md", self.config.max_file_bytes)
                        metadata, _ = parse_skill(text, child.name)
                    except SkillError as exc:
                        # Never log file contents, filesystem paths or YAML parser messages.
                        logger.warning("skill_skipped root_index=%s reason=%s", root_index, str(exc))
                        continue
                    name = metadata["name"]
                    if name in records:
                        collisions.add(name)
                    records[name] = SkillRecord(root, child.name, metadata)
            for name in collisions:
                records.pop(name, None)
                logger.warning("skill_name_collision")
            self._records = records

    def is_allowed(self, name: str, agent_name: str) -> bool:
        whitelist = self.config.agent_skills.get(agent_name)
        return name not in self.config.disabled_skills and (whitelist is None or name in whitelist)

    def list_skills(self, *, agent_name: str) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"name": name, "description": record.metadata["description"]}
                for name, record in sorted(self._records.items())
                if self.is_allowed(name, agent_name)
            ]

    def read_skill(self, name: str, path: str = "SKILL.md", *, agent_name: str) -> SkillResource:
        path = normalize_resource_path(path)
        with self._lock:
            record = self._records.get(name)
        if record is None or not self.is_allowed(name, agent_name):
            raise SkillError("skill_unavailable")
        text = _read_text(record.root, record.directory, path, self.config.max_file_bytes)
        size_bytes = len(text.encode("utf-8"))
        if path == "SKILL.md":
            _, text = parse_skill(text, record.directory)
        return SkillResource(name, path, text, size_bytes)
