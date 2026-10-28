"""ArtifactSink unit tests (plan 4, stage 6).

Display and promotion decisions come from artifact metadata, not from a list of
known keys.
"""

from __future__ import annotations

import pytest

from app.agent.artifacts.model import Artifact
from app.agent.artifacts.sink import ArtifactSink
from app.agent.runtime.session_state import AgentSessionState


def _state(*, turn_index: int = 1) -> AgentSessionState:
    return AgentSessionState(session_id="s_artifacts", turn_index=turn_index)


def test_apply_records_display_scope_and_turn() -> None:
    state = _state()
    ArtifactSink().apply(
        state,
        [
            Artifact("shops", [{"source_id": 1}], display=True, scope="session"),
            Artifact("route", {"distance_m": 10}, display=True),
            Artifact("total", 1),
        ],
    )

    meta = state.working_memory["artifact_meta"]
    assert meta["shops"]["turn_index"] == 1
    assert meta["shops"]["display"] is True
    assert meta["shops"]["scope"] == "session"
    assert meta["shops"]["promote"] is True
    assert meta["route"]["display"] is True and meta["route"]["scope"] == "turn"
    assert meta["total"]["display"] is False
    assert state.working_memory["artifacts"]["total"] == 1


def test_append_merges_and_deduplicates_source_ids() -> None:
    state = _state()
    sink = ArtifactSink()
    sink.apply(state, [Artifact("search_candidates", [{"source_id": 1}], append=True, promote=False)])
    sink.apply(
        state,
        [Artifact("search_candidates", [{"source_id": 1}, {"source_id": 2}], append=True, promote=False)],
    )

    assert [row["source_id"] for row in state.working_memory["artifacts"]["search_candidates"]] == [1, 2]


def test_append_key_skips_rows_without_the_key() -> None:
    state = _state()
    ArtifactSink().apply(
        state,
        [Artifact(
            "search_candidates",
            [{"source_id": 1}, {"name": "no id"}],
            append=True,
            append_key="source_id",
            promote=False,
        )],
    )

    assert state.working_memory["artifacts"]["search_candidates"] == [{"source_id": 1}]


def test_display_only_artifacts_fail_loudly_until_supported() -> None:
    with pytest.raises(ValueError, match="display-only"):
        ArtifactSink().apply(_state(), [Artifact("shops", [], memory=False)])


def test_turn_snapshot_requires_a_display_artifact_from_this_turn() -> None:
    state = _state()
    sink = ArtifactSink()
    sink.apply(
        state,
        [
            Artifact("shops", [{"source_id": 1}], display=True, scope="session"),
            Artifact("route", {"distance_m": 10}, display=True),
        ],
    )

    assert set(sink.turn_snapshot(state)) == {"shops", "route"}

    state.turn_index = 2
    assert sink.turn_snapshot(state) is None
    sink.apply(state, [Artifact("selected_shops", [], display=True, scope="session")])
    snapshot = sink.turn_snapshot(state)
    assert snapshot is not None
    assert snapshot["shops"] == [{"source_id": 1}]
    assert snapshot["selected_shops"] == []
    assert "route" not in snapshot  # turn-scoped and produced by turn 1


def test_display_snapshot_keeps_session_values_and_latest_turn_details() -> None:
    state = _state()
    sink = ArtifactSink()
    sink.apply(
        state,
        [
            Artifact("shops", [{"source_id": 1}], display=True, scope="session"),
            Artifact("shop", {"source_id": 1}, display=True),
            Artifact("route", {"distance_m": 10}, display=True),
        ],
    )

    state.turn_index = 2
    sink.apply(state, [Artifact("shops", [{"source_id": 2}], display=True, scope="session")])
    snapshot = sink.display_snapshot(state)
    assert snapshot is not None
    assert snapshot["shops"] == [{"source_id": 2}]
    assert "shop" not in snapshot
    assert "route" not in snapshot


def test_new_search_invalidates_previous_selection() -> None:
    state = _state()
    sink = ArtifactSink()
    sink.apply(state, [Artifact("selected_shops", [{"source_id": 99}], display=True, scope="session")])
    sink.apply(
        state,
        [Artifact(
            "shops", [{"source_id": 1}], display=True, scope="session",
            invalidates=("selected_shops", "shop", "destination"),
        )],
    )

    assert "selected_shops" not in state.working_memory["artifacts"]
    assert "selected_shops" not in state.working_memory["artifact_meta"]


def test_begin_turn_drops_turn_scope_and_keeps_session_scope() -> None:
    from app.agent.runtime.session_state import ensure_working_memory_shape

    state = _state(turn_index=2)
    sink = ArtifactSink()
    sink.apply(
        state,
        [
            Artifact("shop", {"source_id": 1}, display=True, scope="turn"),
            Artifact("route", {"distance_m": 10}, display=True, scope="turn"),
            Artifact("shops", [{"source_id": 1}], display=True, scope="session"),
            Artifact("total", 1, scope="session"),
            Artifact("client_location", {"lng": 116.3}, scope="session"),
        ],
    )
    ensure_working_memory_shape(state.working_memory)["artifact_meta"]["legacy_route"] = {"turn_index": 1}

    sink.begin_turn(state.working_memory, legacy_keys=("legacy_route",))

    assert "shop" not in state.working_memory["artifacts"]
    assert "route" not in state.working_memory["artifacts"]
    assert "legacy_route" not in state.working_memory["artifact_meta"]
    assert state.working_memory["artifacts"]["shops"] == [{"source_id": 1}]
    assert state.working_memory["artifacts"]["total"] == 1
    assert state.working_memory["artifacts"]["client_location"] == {"lng": 116.3}


def test_promoted_search_clears_the_parent_selection() -> None:
    parent = _state(turn_index=2)
    child = _state(turn_index=2)
    sink = ArtifactSink()
    sink.apply(parent, [Artifact("selected_shops", [{"source_id": 99}], display=True, scope="session")])
    sink.apply(
        child,
        [Artifact(
            "shops", [{"source_id": 1}], display=True, scope="session",
            invalidates=("selected_shops", "shop", "destination"),
        )],
    )

    sink.promote(
        child_memory=child.working_memory,
        parent_memory=parent.working_memory,
        turn_index=2,
    )

    assert parent.working_memory["artifacts"]["shops"] == [{"source_id": 1}]
    assert "selected_shops" not in parent.working_memory["artifacts"]


def test_legacy_metadata_without_flags_still_displays() -> None:
    from app.agent.runtime.session_state import ensure_working_memory_shape

    state = _state()
    ensure_working_memory_shape(state.working_memory)["artifacts"]["shops"] = [{"source_id": 1}]
    state.working_memory["artifact_meta"] = {"shops": {"turn_index": 1}}

    snapshot = ArtifactSink().display_snapshot(state)
    assert snapshot is not None
    assert snapshot["shops"] == [{"source_id": 1}]

    # A new (flagged) artifact must not hide the pre-upgrade shops.
    state.turn_index = 2
    ArtifactSink().apply(state, [Artifact("selected_shops", [], display=True, scope="session")])
    mixed = ArtifactSink().display_snapshot(state)
    assert mixed is not None
    assert mixed["shops"] == [{"source_id": 1}]
    assert mixed["selected_shops"] == []


def test_promote_skips_worker_local_values_and_marks_partial() -> None:
    parent = _state(turn_index=2)
    worker = _state(turn_index=2)
    sink = ArtifactSink()
    sink.apply(
        worker,
        [
            Artifact("shops", [{"source_id": 1}], display=True, scope="session"),
            Artifact("search_candidates", [{"source_id": 1}], promote=False, append=True),
        ],
    )

    promoted = sink.promote(
        child_memory=worker.working_memory,
        parent_memory=parent.working_memory,
        turn_index=2,
        status="failed",
    )

    assert set(promoted) == {"shops"}
    assert parent.working_memory["artifacts"]["shops"] == [{"source_id": 1}]
    assert "search_candidates" not in parent.working_memory["artifacts"]
    assert parent.working_memory["artifact_meta"]["shops"]["status"] == "partial"


def test_promote_ignores_values_copied_from_the_parent_without_metadata() -> None:
    from app.agent.runtime.session_state import ensure_working_memory_shape

    parent = _state(turn_index=2)
    worker = _state(turn_index=2)
    ensure_working_memory_shape(worker.working_memory)["artifacts"]["shops"] = [{"source_id": 1}]

    promoted = ArtifactSink().promote(
        child_memory=worker.working_memory,
        parent_memory=parent.working_memory,
        turn_index=2,
    )

    assert promoted == {}
    assert "shops" not in parent.working_memory.get("artifacts", {})
