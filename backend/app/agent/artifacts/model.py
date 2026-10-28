"""Generic artifacts a tool or domain can attach to one execution result.

An artifact is an opaque ``key``/``value`` pair plus the policy the kernel
needs to store, display and promote it. The kernel never interprets the value;
domain code decides which keys exist. Metadata recorded in
``working_memory["artifact_meta"]`` is what display and worker promotion read,
so no consumer keeps a hard-coded list of display keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ArtifactScope = Literal["turn", "session"]
"""How long an artifact survives.

``turn`` artifacts are removed by ``ArtifactSink.begin_turn`` when the next turn
starts; ``session`` artifacts stay until a newer value replaces them or domain
code removes them.
"""


@dataclass(frozen=True)
class Artifact:
    """One opaque value produced by a tool or domain result."""

    key: str
    value: Any
    # Display-only artifacts are not supported yet; ``False`` raises in apply.
    memory: bool = True
    # Whether the value contributes to the turn/session display snapshot.
    display: bool = False
    scope: ArtifactScope = "turn"
    # Whether a finished worker's value may replace the parent's artifact.
    promote: bool = True
    # Append the value to an existing list instead of replacing it.
    append: bool = False
    # Field used to de-duplicate appended dict rows; None keeps equal rows only
    # once (domain code supplies the field name, e.g. "source_id").
    append_key: str | None = None
    # Artifacts that a new value makes stale (for example a fresh search
    # invalidates the previous selection). Removed unless the same batch
    # produces them again.
    invalidates: tuple[str, ...] = ()
