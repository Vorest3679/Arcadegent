"""``ToolSpec``: the complete, validated declaration of one local tool.

A spec answers exactly one question: *what is this tool and how is it run*.
It deliberately does not carry:

* services or configuration — executors resolve shared objects through the
  :class:`ToolContext` the owning provider passes in;
* permissions — whether an agent may see or call a tool is decided by the
  agent profile's ``allowed_tools`` and the tool policy, never by registration;
* the provider name — the provider that holds the spec stamps it on the
  descriptor;
* import strings, environment variables or file paths — the host resolves
  those before building a spec (the manifest loader is one such host-side
  adapter).

Constructing a spec validates it, so an invalid spec cannot exist: the input
schema is checked against JSON Schema 2020-12 and its validator is compiled
once. A spec is immutable; changing a tool means registering a new spec under
the same name.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.agent.tools.base import ExecutionMode, ToolDescriptor, ToolOutcome, ToolValidator
from app.agent.tools.schemas import build_json_schema_validator

# Provider function-name rules (OpenAI-compatible) minus a leading digit, dash
# or underscore. ``mcp__`` is the MCP gateway's namespace.
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MCP_TOOL_PREFIX = "mcp__"

# Kernel-semantic capabilities a tool registered at runtime may not declare:
# worker dispatch must stay behind the host-configured ``invoke_worker`` tool so
# the spawnable list and budgets cannot be bypassed; the other two are reserved,
# not implemented. ``concludes_turn`` is intentionally allowed (plan 5, D8).
RESERVED_CAPABILITIES = frozenset({"spawn_agent", "background", "programmatic"})

ToolKind = Literal["function"]
_EXECUTION_MODES = ("exclusive", "parallel")


class ToolSpecError(ValueError):
    """A tool declaration or a registration request is invalid.

    Messages follow the registry's ``code:tool_name[:detail]`` convention and
    never contain argument values, paths or upstream payloads.
    """


@dataclass(frozen=True)
class ToolContext:
    """What an executor receives besides its arguments: named shared services."""

    resolver: Callable[[str], Any]

    def require(self, service_name: str) -> Any:
        return self.resolver(service_name)

    def get(self, service_name: str) -> Any | None:
        try:
            return self.resolver(service_name)
        except ValueError:
            return None


ToolResult = dict[str, Any] | ToolOutcome
# Sync executors run in a worker thread; async executors run on the loop.
ToolExecutor = Callable[[ToolContext, dict[str, Any]], ToolResult | Awaitable[ToolResult]]
# (raw arguments, read-only session memory) -> (arguments, warnings). Raise
# ``ToolArgumentPreparationError`` for a model-facing, log-safe reason.
ArgumentPreparer = Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], list[str]]]


@dataclass(frozen=True)
class ToolSpec:
    """Declaration of one local tool.

    Fields
    ------
    name
        Unique, model-facing function name: ``[A-Za-z][A-Za-z0-9_-]{0,63}``,
        never starting with ``mcp__``.
    description
        Model-facing description; must not be blank.
    input_schema
        JSON Schema (2020-12) of the arguments; the root must be
        ``{"type": "object", ...}``. Defaults declared in the schema are
        applied before validation. Stored as given; treat it as read-only.
    executor
        ``(context, arguments) -> dict | ToolOutcome``, sync or async. It gets
        arguments that already passed ``input_schema``.
    argument_preparer
        Optional ``(arguments, session_memory) -> (arguments, warnings)``
        hook that runs before validation, e.g. to fill values from memory.
    capabilities
        Free-form tags plus kernel-semantic ones such as ``concludes_turn``.
        Duplicates are removed, order kept. ``RESERVED_CAPABILITIES`` are
        rejected when the spec is registered at runtime (``add_tools``).
    execution_mode
        ``"exclusive"`` (default, safe) or ``"parallel"`` when the tool may
        overlap sibling calls in the same model step.
    kind
        Tool type projected to the provider; only ``"function"`` exists.
    metadata
        Opaque JSON-like data for the host. The runtime never interprets it.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    executor: ToolExecutor
    argument_preparer: ArgumentPreparer | None = None
    capabilities: tuple[str, ...] = ()
    execution_mode: ExecutionMode = "exclusive"
    kind: ToolKind = "function"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    _validator: ToolValidator = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        name = self.name
        if not isinstance(name, str) or not _NAME_PATTERN.match(name):
            raise ToolSpecError(f"invalid_tool_name:{name!s:.64}")
        if name.startswith(MCP_TOOL_PREFIX):
            raise ToolSpecError(f"reserved_tool_prefix:{name}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ToolSpecError(f"invalid_tool_description:{name}")
        if self.kind != "function":
            raise ToolSpecError(f"invalid_tool_kind:{name}")
        if self.execution_mode not in _EXECUTION_MODES:
            raise ToolSpecError(f"invalid_tool_execution_mode:{name}")
        if not callable(self.executor):
            raise ToolSpecError(f"invalid_tool_executor:{name}")
        if self.argument_preparer is not None and not callable(self.argument_preparer):
            raise ToolSpecError(f"invalid_tool_argument_preparer:{name}")

        capabilities = self.capabilities
        if isinstance(capabilities, str) or not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            raise ToolSpecError(f"invalid_tool_capabilities:{name}")
        object.__setattr__(self, "capabilities", tuple(dict.fromkeys(item.strip() for item in capabilities)))

        if not isinstance(self.metadata, Mapping):
            raise ToolSpecError(f"invalid_tool_metadata:{name}")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

        schema = self.input_schema
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ToolSpecError(f"invalid_tool_input_schema:{name}:root_must_be_object")
        schema = dict(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ToolSpecError(f"invalid_tool_input_schema:{name}:{exc.validator}") from None
        object.__setattr__(self, "input_schema", schema)
        object.__setattr__(self, "_validator", build_json_schema_validator(schema, source=name))

    @property
    def reserved_capabilities(self) -> frozenset[str]:
        return RESERVED_CAPABILITIES.intersection(self.capabilities)

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply schema defaults and validate; raises ``ToolInputValidationError``."""
        return self._validator(arguments)

    def to_descriptor(self) -> ToolDescriptor:
        """Project the spec to the provider-neutral descriptor the registry routes on."""
        return ToolDescriptor(
            name=self.name,
            description=self.description,
            input_schema=cast(dict[str, Any], self.input_schema),
            kind=self.kind,
            validator=self._validator,
            capabilities=self.capabilities,
            execution_mode=self.execution_mode,
            metadata=dict(self.metadata),
        )

    @classmethod
    def from_definition(
        cls,
        definition: Mapping[str, Any] | Path,
        *,
        executor: ToolExecutor,
        argument_preparer: ArgumentPreparer | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ToolSpec":
        """Build a spec from a JSON tool definition plus host-supplied callables.

        Reads ``name``, ``description``, ``input_schema``, ``capabilities``,
        ``execution_mode`` and ``kind``. The ``executor`` / ``argument_preparer``
        import strings a manifest definition may contain are ignored here; the
        caller passes the callables. ``metadata`` replaces the definition's.
        """
        data = _read_definition(definition)
        name = str(data.get("name") or "").strip()
        execution_mode = data.get("execution_mode", "exclusive")
        return cls(
            name=name,
            description=str(data.get("description") or "").strip(),
            input_schema=data.get("input_schema"),  # type: ignore[arg-type]
            executor=executor,
            argument_preparer=argument_preparer,
            capabilities=tuple(
                item.strip() for item in data.get("capabilities", []) if isinstance(item, str) and item.strip()
            ),
            execution_mode=execution_mode.strip().lower() if isinstance(execution_mode, str) else execution_mode,
            kind=str(data.get("kind") or "function").strip() or "function",  # type: ignore[arg-type]
            metadata=dict(metadata if metadata is not None else data.get("metadata") or {}),
        )


def _read_definition(definition: Mapping[str, Any] | Path) -> Mapping[str, Any]:
    if isinstance(definition, Mapping):
        return definition
    try:
        data = json.loads(definition.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ToolSpecError(f"unreadable_tool_definition:{definition.name}") from None
    if not isinstance(data, dict):
        raise ToolSpecError(f"invalid_tool_definition:{definition.name}")
    return data
