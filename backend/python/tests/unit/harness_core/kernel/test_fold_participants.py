"""SS1: session events on the root stream and `by` on `input.queued`, as the fold reads them.

PE-8  Participant and fork events (no run) change no fold; a build that does not know them ignores them.
QB-4  `input.queued{by}` folds to the same entries as without `by`.
QB-5  Why `by` needs the `steering_by` feature: a critical event whose data has a field the build's
      model does not know fails the fold closed, which is what a pre-SS1 worker would do with `by`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.driver.capabilities import CORE_FEATURES
from harness_core.journal import JournalEvent
from harness_core.kernel.fold import FoldIncompatible, SessionState, fold_session

if TYPE_CHECKING:
    from pydantic import JsonValue

TEXT: dict[str, JsonValue] = {
    "ref": "inline:aGk",
    "hash": "sha256:8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
    "size": 2,
}


def _event(
    seq: int,
    type_: str,
    data: dict[str, JsonValue],
    *,
    run_id: str | None = "run1",
    writer: str = "node",
    critical: bool = True,
    visibility: str = "owner",
) -> JournalEvent:
    return JournalEvent.model_validate(
        {
            "org_id": "org1",
            "session_id": "ses1",
            "stream_id": "ses1",
            "seq": seq,
            "run_id": run_id,
            "type": type_,
            "critical": critical,
            "ts": "2026-10-03T00:00:00.000Z",
            "writer": writer,
            "visibility": visibility,
            "data": data,
        }
    )


def _steered(queued: dict[str, JsonValue]) -> list[JournalEvent]:
    return [
        _event(1, "run.admitted", {"runKind": "interactive", "principalRef": "p_u1"}),
        _event(2, "input.queued", queued),
        _event(3, "input.consumed", {"messageSeq": 1}, writer="worker"),
    ]


def _session_events(first_seq: int) -> list[JournalEvent]:
    return [
        _event(
            first_seq,
            "session.participant_changed",
            {"by": "p_u1", "subject": "p_u2", "role": "viewer", "prevRole": "none"},
            run_id=None,
            critical=False,
            visibility="shared",
        ),
        _event(
            first_seq + 1,
            "session.forked",
            {"by": "p_u2", "fromSessionId": "ses0", "atSeq": 4},
            run_id=None,
            critical=False,
            visibility="shared",
        ),
    ]


def _renumbered(events: list[JournalEvent]) -> list[JournalEvent]:
    return [event.model_copy(update={"seq": index + 1}) for index, event in enumerate(events)]


def _content(state: SessionState) -> dict[str, object]:
    """The folded state, positions aside: inserting events before an entry moves its seq only."""
    return state.model_dump(exclude={"seq": True, "ignored_events": True, "entries": {"__all__": {"seq"}}})


def test_pe8_session_events_change_no_fold() -> None:
    plain = _steered({"messageSeq": 1, "textRef": TEXT})
    shared_first = _renumbered([*_session_events(1), *plain])
    shared_after = _renumbered([*plain, *_session_events(1)])
    expected = _content(fold_session(plain, stream_id="ses1"))
    assert expected["entries"], "the steered message is an entry"
    assert _content(fold_session(shared_first, stream_id="ses1")) == expected
    assert _content(fold_session(shared_after, stream_id="ses1")) == expected


def test_pe8_a_build_that_does_not_know_a_session_event_ignores_it() -> None:
    unknown = _event(
        1,
        "session.participant_renamed",
        {"by": "p_u1"},
        run_id=None,
        critical=False,
        visibility="shared",
    )
    state = fold_session([unknown], stream_id="ses1")
    assert state.ignored_events == 1
    assert state.entries == []


def test_qb4_by_on_input_queued_folds_like_without_it() -> None:
    without = fold_session(_steered({"messageSeq": 1, "textRef": TEXT}), stream_id="ses1")
    with_by = fold_session(_steered({"messageSeq": 1, "textRef": TEXT, "by": "p_u1"}), stream_id="ses1")
    assert _content(with_by) == _content(without)


def test_qb4_the_worker_advertises_steering_by() -> None:
    assert "steering_by" in CORE_FEATURES


def test_qb5_a_critical_event_with_a_field_the_build_does_not_know_fails_closed() -> None:
    with pytest.raises(FoldIncompatible, match="payload fails its schema"):
        _ = fold_session(_steered({"messageSeq": 1, "textRef": TEXT, "sender": "p_u1"}), stream_id="ses1")
