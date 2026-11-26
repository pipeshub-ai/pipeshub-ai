"""E7: `session.erased` on the root stream changes no fold, and a build that predates it ignores it."""

from __future__ import annotations

from harness_core.contracts.generated.event_catalog import EVENT_WRITERS
from harness_core.journal import JournalEvent
from harness_core.kernel.fold import fold_session


def _erased(seq: int, type_: str = "session.erased") -> JournalEvent:
    return JournalEvent.model_validate(
        {
            "org_id": "org1",
            "session_id": "ses1",
            "stream_id": "ses1",
            "seq": seq,
            "run_id": None,
            "type": type_,
            "critical": False,
            "ts": "2026-10-03T00:00:00.000Z",
            "writer": "node",
            "visibility": "audit",
            "data": {"by": "p_u1", "reason": "user_delete", "counts": {"events": 3}},
        }
    )


def test_session_erased_is_node_written() -> None:
    assert EVENT_WRITERS["session.erased"] == frozenset({"node"})


def test_session_erased_folds_to_nothing() -> None:
    state = fold_session([_erased(1)], stream_id="ses1")
    assert state.entries == []


def test_a_build_that_does_not_know_it_ignores_it() -> None:
    state = fold_session([_erased(1, "session.erased_v9")], stream_id="ses1")
    assert state.ignored_events == 1
    assert state.entries == []
