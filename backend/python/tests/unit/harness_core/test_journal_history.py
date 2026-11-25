"""QA13-4 (JRN-22): each history rule, broken once, is reported as exactly that rule."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.journal import JournalEvent
from harness_core.testing.history import journal_history_violations

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue


def _event(seq: int, type_: str, writer: str = "node", fence: int | None = None, **data: JsonValue) -> JournalEvent:
    return JournalEvent(
        org_id="org",
        session_id="ses",
        stream_id="ses",
        seq=seq,
        run_id="run",
        attempt_id=None if fence is None else f"run.a{fence}",
        fence=fence,
        type=type_,
        critical=True,
        ts="2026-01-01T00:00:00.000Z",
        writer=writer,  # type: ignore[arg-type]
        data=data,
    )


def _claim(seq: int, fence: int) -> JournalEvent:
    return _event(seq, "run.claimed", fence=fence).model_copy(
        update={"data": {"attempt": fence, "fence": fence, "leaseMs": 30_000}}
    )


def _valid() -> list[JournalEvent]:
    return [
        _event(1, "run.admitted", runKind="interactive"),
        _claim(2, 1),
        _event(3, "run.spec_pinned", fence=1),
        _event(4, "tool.intent", "worker", 1, invocationId="t1.0"),
        _claim(5, 2),
        _event(6, "tool.outcome", "worker", 2, invocationId="t1.0"),
        _event(7, "run.completed", fence=2, outcome="answered"),
        _event(8, "session.erased").model_copy(update={"run_id": None}),
    ]


def _replace(seq: int, event: JournalEvent) -> Callable[[list[JournalEvent]], list[JournalEvent]]:
    return lambda events: [event if e.seq == seq else e for e in events]


def _renumber(events: list[JournalEvent]) -> list[JournalEvent]:
    return [e.model_copy(update={"seq": i}) for i, e in enumerate(events, start=1)]


BROKEN: dict[str, tuple[str, Callable[[list[JournalEvent]], list[JournalEvent]]]] = {
    "a gap in seq": ("H1", lambda events: [e for e in events if e.seq != 8] + [events[-1].model_copy(update={"seq": 9})]),
    "a repeated seq": ("H1", lambda events: [*events[:4], events[3], *events[4:]]),
    "a run that does not start with its admission": ("H2", lambda events: _renumber(events[1:])),
    "a second pin": ("H3", lambda events: _renumber([*events[:3], events[2], *events[3:]])),
    "a worker event before the pin": (
        "H3",
        lambda events: _renumber([events[0], events[1], events[3], events[2], *events[4:]]),
    ),
    "a claim that does not raise the fence": ("H4", _replace(5, _claim(5, 1))),
    "a worker event under an older fence": ("H4", _replace(6, _event(6, "tool.outcome", "worker", 1, invocationId="t1.0"))),
    "an outcome without its intent": ("H5", _replace(4, _event(4, "turn.started", "worker", 1, turn=1))),
    # EV-06 (C2.49): every call ends with exactly one outcome.
    "a call left open when the run ends": ("H8", lambda events: _renumber([e for e in events if e.seq != 6])),
    "a second outcome of one call": ("H8", lambda events: _renumber([*events[:6], events[5], *events[6:]])),
    "an event after the end": ("H6", _replace(8, _event(8, "turn.started", "worker", 2, turn=2))),
    "a second end": ("H6", _replace(8, _event(8, "run.failed", fence=2, errorType="x"))),
}


def test_qa13_4_a_valid_history_breaks_no_rule() -> None:
    assert journal_history_violations(_valid()) == []


@pytest.mark.parametrize("case", list(BROKEN))
def test_qa13_4_each_broken_rule_is_reported_as_that_rule(case: str) -> None:
    rule, breaking = BROKEN[case]
    violations = journal_history_violations(breaking(_valid()))
    assert violations, case
    assert {v.rule for v in violations} == {rule}, [str(v) for v in violations]


def test_qa13_4_streams_are_checked_apart_and_a_run_across_streams_is_one_run() -> None:
    child = [
        e.model_copy(update={"stream_id": "child", "run_id": "child"})
        for e in _valid()[:3]
    ]
    assert journal_history_violations([*child, *_valid()]) == []


def test_qa13_4_an_import_before_the_admission_and_an_unrun_call_break_no_rule() -> None:
    """Node journals the session's legacy history with the run, before its admission; a call that never ran
    (denied, cut off, stopped) has an error or cancelled outcome and no intent."""
    imported = [_event(1, "session.imported"), _event(2, "x.pipeshub.history.labelled")]
    events = [*imported, *[e.model_copy(update={"seq": e.seq + 2}) for e in _valid()]]
    unrun = _event(6, "tool.outcome", "worker", 2, invocationId="t1.1", status="error")
    valid = _valid()
    assert journal_history_violations(events) == []
    assert journal_history_violations(_renumber([*valid[:6], unrun, *valid[6:]])) == []


def test_ev06_a_call_ends_with_one_outcome_or_an_unknown_and_a_recall_opens_it_again() -> None:
    """EV-06 (C2.49): an effect whose outcome could not be settled ends with ``tool.unknown``; a re-call after
    ``tool.reconciled`` not_applied opens the call again, and its own outcome ends it."""
    valid = _valid()
    unknown = _event(6, "tool.unknown", "worker", 2, invocationId="t1.0")
    assert journal_history_violations(_replace(6, unknown)(valid)) == []
    recalled = _event(7, "tool.intent", "worker", 2, invocationId="t1.0")
    outcome = _event(8, "tool.outcome", "worker", 2, invocationId="t1.0", status="ok")
    reopened = _renumber([*valid[:5], unknown, recalled, *valid[6:]])
    assert {v.rule for v in journal_history_violations(reopened)} == {"H8"}
    assert journal_history_violations(_renumber([*valid[:5], unknown, recalled, outcome, *valid[6:]])) == []


def test_ev06_node_ending_a_run_closes_its_open_calls_as_cancelled() -> None:
    """A run Node ends itself (no worker holds it) journals a cancelled outcome for each call left open."""
    from harness_core.journal.open_calls import node_closing_outcomes

    valid = _valid()
    open_run = [e for e in valid if e.seq not in (6, 7, 8)]
    closing = node_closing_outcomes(open_run, "run")
    assert [(e.type, e.data["invocationId"], e.data["status"]) for e in closing] == [("tool.outcome", "t1.0", "cancelled")]
    closed = _renumber([*open_run, *(_event(0, e.type, **e.data) for e in closing), _event(0, "run.cancelled")])
    assert journal_history_violations(closed) == []
