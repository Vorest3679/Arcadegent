"""Store, display and promote generic artifacts.

``ArtifactSink`` is the only place that translates an :class:`Artifact` policy
into working-memory metadata. Display consumers read the metadata flags instead
of keeping their own list of keys:

* ``apply`` writes one result's artifacts (and metadata) into a state;
* ``promote`` merges a finished worker's artifacts into its parent;
* ``turn_snapshot`` returns the display artifacts a turn produced;
* ``display_snapshot`` returns the session's current display, for responses and
  session details (turn-scoped artifacts come from the latest display turn).

The sink never interprets artifact values.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.artifacts.model import Artifact
from app.agent.runtime.session_state import (
    AgentSessionState,
    ensure_working_memory_shape,
    get_working_memory_artifact,
    set_working_memory_artifact,
)


def _append_values(existing: Any, value: Any, *, append_key: str | None) -> Any:
    """Append list rows, de-duplicating by ``append_key`` or by equality."""
    if not isinstance(value, list):
        return value
    merged = list(existing) if isinstance(existing, list) else []
    if append_key is None:
        for item in value:
            if item not in merged:
                merged.append(item)
        return merged
    known_ids = {
        item.get(append_key)
        for item in merged
        if isinstance(item, dict) and type(item.get(append_key)) is int
    }
    for item in value:
        if not isinstance(item, dict) or type(item.get(append_key)) is not int:
            continue
        if item[append_key] in known_ids:
            continue
        known_ids.add(item[append_key])
        merged.append(item)
    return merged


def _display_flag(entry: dict[str, Any]) -> bool:
    """Legacy entries without metadata are treated as displayed."""
    return bool(entry.get("display", True))


def _scope(entry: dict[str, Any]) -> str:
    """Legacy entries have no scope; they are session values, not turn values."""
    return str(entry.get("scope") or "session")


def _remove_artifact(memory: dict[str, Any], key: str) -> None:
    memory["artifacts"].pop(key, None)
    memory.pop(key, None)
    meta = memory.get("artifact_meta")
    if isinstance(meta, dict):
        meta.pop(key, None)


class ArtifactSink:
    """Applies artifact policies to ``working_memory`` (stateless)."""

    def apply(self, state: AgentSessionState, artifacts: tuple[Artifact, ...] | list[Artifact]) -> None:
        """Write a result's artifacts into ``state`` with display metadata."""
        if not artifacts:
            return
        memory = ensure_working_memory_shape(state.working_memory)
        for artifact in artifacts:
            if not isinstance(artifact, Artifact) or not artifact.key:
                continue
            if not artifact.memory:
                raise ValueError(
                    "display-only artifacts are not supported yet; set memory=True"
                )
            value = deepcopy(artifact.value)
            if artifact.append:
                value = _append_values(
                    get_working_memory_artifact(memory, artifact.key),
                    value,
                    append_key=artifact.append_key,
                )
            set_working_memory_artifact(
                memory,
                artifact.key,
                value,
                turn_index=state.turn_index,
                display=artifact.display,
                scope=artifact.scope,
                promote=artifact.promote,
                status=None,
                invalidates=artifact.invalidates,
            )
        batch_keys = {artifact.key for artifact in artifacts if isinstance(artifact, Artifact)}
        for artifact in artifacts:
            if not isinstance(artifact, Artifact):
                continue
            for key in artifact.invalidates:
                if key not in batch_keys:
                    _remove_artifact(memory, key)

    def begin_turn(self, memory: dict[str, Any], *, legacy_keys: tuple[str, ...] = ()) -> None:
        """Drop artifacts whose scope ended with the previous turn.

        ``legacy_keys`` exists only for sessions persisted before ``scope`` was
        recorded: those entries have no scope, so the domain names the keys that
        historically belonged to a single turn.
        """
        memory = ensure_working_memory_shape(memory)
        meta = memory.get("artifact_meta")
        if isinstance(meta, dict):
            for key, entry in list(meta.items()):
                if isinstance(entry, dict) and entry.get("scope") == "turn":
                    _remove_artifact(memory, key)
            for key in legacy_keys:
                entry = meta.get(key)
                if isinstance(entry, dict) and entry.get("scope") is not None:
                    continue
                _remove_artifact(memory, key)
        else:
            for key in legacy_keys:
                _remove_artifact(memory, key)

    def promote(
        self,
        *,
        child_memory: dict[str, Any],
        parent_memory: dict[str, Any],
        turn_index: int,
        status: str = "completed",
    ) -> dict[str, Any]:
        """Merge a finished worker's artifacts into the parent.

        Only artifacts the worker itself produced are eligible: values copied
        from the parent during the mount snapshot have no metadata. Artifacts
        with ``promote=False`` stay worker-local (for example candidate rows).
        A non-completed worker still contributes what it produced, marked
        ``partial`` so readers can distinguish it from a complete result.
        """
        child = ensure_working_memory_shape(child_memory)
        parent = ensure_working_memory_shape(parent_memory)
        meta = child.get("artifact_meta")
        if not isinstance(meta, dict):
            return {}
        promote_status = None if status == "completed" else "partial"
        promoted: dict[str, Any] = {}
        for key, entry in meta.items():
            if not isinstance(entry, dict) or entry.get("promote", True) is False:
                continue
            value = get_working_memory_artifact(child, key)
            if value is None:
                continue
            set_working_memory_artifact(
                parent,
                key,
                value,
                turn_index=turn_index,
                display=_display_flag(entry),
                scope=_scope(entry),
                promote=True,
                status=promote_status,
                invalidates=tuple(entry.get("invalidates") or ()),
            )
            promoted[key] = deepcopy(value)
        for key in promoted:
            entry = meta.get(key)
            if not isinstance(entry, dict):
                continue
            for invalidated in entry.get("invalidates") or ():
                if invalidated not in promoted:
                    _remove_artifact(parent, invalidated)
        return promoted

    def turn_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        """Display artifacts of the current turn, or None when it produced none.

        Session-scoped artifacts are included whenever they exist so a map from
        an earlier search survives a follow-up reply; turn-scoped artifacts are
        included only when this turn updated them. The snapshot exists only if
        this turn updated at least one displayed artifact.
        """
        memory = ensure_working_memory_shape(state.working_memory)
        meta = memory.get("artifact_meta")
        if not isinstance(meta, dict):
            return None
        snapshot: dict[str, Any] = {}
        fresh = False
        for key, entry in meta.items():
            if not isinstance(entry, dict) or not _display_flag(entry):
                continue
            updated = entry.get("turn_index") == state.turn_index
            fresh = fresh or updated
            if _scope(entry) == "session" or updated:
                snapshot[key] = deepcopy(get_working_memory_artifact(memory, key))
        if not fresh:
            return None
        return snapshot or None

    def display_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        """The session's current display artifacts, or None without metadata.

        Turn-scoped artifacts are taken from the latest display turn as a group,
        so a new search's cards never mix with a previous turn's detail/route.
        """
        memory = ensure_working_memory_shape(state.working_memory)
        meta = memory.get("artifact_meta")
        if not isinstance(meta, dict):
            return None
        display_entries = [
            (key, entry)
            for key, entry in meta.items()
            if isinstance(entry, dict) and _display_flag(entry)
        ]
        turn_indexes = [
            entry.get("turn_index")
            for _key, entry in display_entries
            if type(entry.get("turn_index")) is int
        ]
        if not turn_indexes:
            return None
        latest_turn = max(turn_indexes)
        snapshot: dict[str, Any] = {}
        for key, entry in display_entries:
            if _scope(entry) == "session" or entry.get("turn_index") == latest_turn:
                snapshot[key] = deepcopy(get_working_memory_artifact(memory, key))
        return snapshot or None
