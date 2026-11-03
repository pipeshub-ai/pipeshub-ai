"""Which retrieval layer found each record, kept for the retrieval_context trace."""

from __future__ import annotations

from app.modules.retrieval.provenance import RETRIEVED_BY_KEY, note_retrieved_by


def test_each_layer_is_kept_once_and_the_first_identity_wins() -> None:
    state = {"include_retrieval_context": True}
    note_retrieved_by(state, "vector", [("vr-1", "rec-1", "Runbook"), (None, "rec-x", None)])
    note_retrieved_by(state, "pattern_match", [("vr-1", "rec-other", None)])
    note_retrieved_by(state, "vector", [("vr-1", None, None)])

    (entry,) = state[RETRIEVED_BY_KEY].values()
    assert entry.layers == ["vector", "pattern_match"]
    assert (entry.record_id, entry.record_name) == ("rec-1", "Runbook")


def test_nothing_is_kept_unless_the_trace_was_asked_for() -> None:
    state: dict = {}
    note_retrieved_by(state, "vector", [("vr-1", "rec-1", None)])

    assert state == {}
