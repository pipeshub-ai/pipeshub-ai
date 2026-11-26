"""P4.7 MP-8 (fold): the route a run is on is derived from the journal; a checkpoint without it is refolded."""

from __future__ import annotations

from typing import Any

from harness_core.journal.types import Checkpoint, JournalEvent
from harness_core.kernel.fold import (
    ModelRouteRecord,
    checkpoint_is_valid,
    fold_run,
    fold_session,
    make_checkpoint,
    restore_checkpoint,
)

RUN, SESSION = "run1", "ses1"


def _event(seq: int, type_: str, data: dict[str, Any], *, critical: bool = True) -> JournalEvent:
    return JournalEvent(
        org_id="org1",
        session_id=SESSION,
        stream_id=SESSION,
        seq=seq,
        run_id=RUN,
        type=type_,
        critical=critical,
        ts="2026-10-05T00:00:00Z",
        writer="worker",
        data=data,
    )


def _fallback(seq: int, to: str, reason: str = "5xx") -> JournalEvent:
    return _event(
        seq,
        "model.fallback",
        {"from": {"provider": "a", "model": "a-1"}, "to": {"provider": to, "model": f"{to}-1"}, "reason": reason},
        critical=False,
    )


def test_mp8_the_last_fallback_is_the_route() -> None:
    events = [_fallback(1, "b"), _fallback(2, "c", "timeout")]
    assert fold_run(events, run_id=RUN).model_route == ModelRouteRecord(provider="c", model="c-1")
    assert fold_run([], run_id=RUN).model_route is None


def test_mp8_an_unreadable_fallback_from_a_newer_writer_is_ignored_not_fatal() -> None:
    odd = _event(1, "model.fallback", {"from": {}, "to": {}, "reason": "slow"}, critical=False)
    state = fold_run([odd], run_id=RUN)
    assert state.model_route is None and state.ignored_events == 1


def test_mp8_checkpoint_round_trips_the_route_and_an_older_shape_is_refolded() -> None:
    events = [_fallback(1, "b")]
    run = fold_run(events, run_id=RUN)
    session = fold_session(events, stream_id=SESSION)
    current = make_checkpoint(run, session)
    assert checkpoint_is_valid(current, run_id=RUN, head_seq=1, ref="c", referenced_ref="c")
    assert restore_checkpoint(current)[0].model_route == run.model_route
    older = dict(current.state["run"])  # type: ignore[arg-type]
    del older["model_route"]
    stale = Checkpoint(
        run_id=current.run_id,
        stream_id=current.stream_id,
        fold_version=current.fold_version,
        seq=current.seq,
        state={**current.state, "run": older},
    )
    assert not checkpoint_is_valid(stale, run_id=RUN, head_seq=1, ref="c", referenced_ref="c")
