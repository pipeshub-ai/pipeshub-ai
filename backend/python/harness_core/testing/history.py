"""Journal history checks (TEST-STRATEGY §2.14, JRN-22): is a journal a valid history of its runs?

Cheap Elle-style rules over stored events, run after every test that wrote a journal and inside the
state machines after each step. The same rules, numbered the same, are in Node's
``tests/support/harness/journal-history.ts``; H7 (v1 ``agentEvents``) exists only there.

- H1: ``seq`` is 1, 2, 3 … per stream, with no gap or repeat.
- H2: a run's first event is ``run.admitted``, after the ``session.imported`` and ``x.pipeshub.history.labelled``
  Node journals with it.
- H3: at most one ``run.spec_pinned`` per run, and no worker event of the run comes before it.
- H4: ``run.claimed`` fences strictly increase, and an event carrying a fence carries the current claim's.
- H5: an ``ok`` or ``timeout`` ``tool.outcome`` (the call ran, or may have) follows a ``tool.intent`` or
  ``tool.denied`` of the same invocation in the same run. An ``error`` or ``cancelled`` one may report a
  call that never ran: denied, cut off in a truncated response, or stopped by a cancel.
- H6: at most one terminal event per run, and nothing of the run follows it.
- H8: every tool call ends with exactly one outcome (EV-06, C2.49): no invocation has a second ``tool.outcome``,
  and when the run ends every ``tool.intent`` or ``tool.denied`` has its ``tool.outcome`` (a result, an error,
  a denial or a cancellation) or a ``tool.unknown`` (the effect may have happened and could not be settled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from harness_core.journal.memory import InMemoryJournal
    from harness_core.journal.types import JournalEvent

TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.quarantined"})
# What admission journals with an imported history, ahead of the run's ``run.admitted``.
ADMISSION_PREFIX = frozenset({"run.admitted", "session.imported", "x.pipeshub.history.labelled"})


@dataclass(frozen=True)
class HistoryViolation:
    rule: str
    stream_id: str
    seq: int
    run_id: str | None
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.rule} {self.stream_id}#{self.seq} run={self.run_id}: {self.message}"


@dataclass
class _Run:
    events: int = 0
    admitted: bool = False
    pinned: bool = False
    fence: int | None = None
    intents: set[str] = field(default_factory=set[str])
    ended_at: int | None = None
    # H8: each opened call and whether it has ended (an outcome or ``tool.unknown``); calls with an outcome.
    open_calls: dict[str, bool] = field(default_factory=dict[str, bool])
    outcomes: set[str] = field(default_factory=set[str])


def _gaps(stream: Sequence[JournalEvent]) -> list[HistoryViolation]:
    return [
        HistoryViolation("H1", event.stream_id, event.seq, event.run_id, f"expected seq {expected}")
        for expected, event in enumerate(stream, start=1)
        if event.seq != expected
    ]


def _run_rules(event: JournalEvent, run: _Run) -> list[tuple[str, str]]:
    broken: list[tuple[str, str]] = []
    if not run.admitted and event.type not in ADMISSION_PREFIX:
        broken.append(("H2", f"the run starts with {event.type}, not run.admitted"))
    if run.ended_at is not None:
        broken.append(("H6", f"{event.type} after the run ended at seq {run.ended_at}"))
    if event.type == "run.spec_pinned":
        if run.pinned:
            broken.append(("H3", "a second run.spec_pinned"))
        run.pinned = True
    elif event.writer == "worker" and not run.pinned:
        broken.append(("H3", f"worker event {event.type} before run.spec_pinned"))
    if event.type == "run.claimed":
        claimed = event.data.get("fence")
        if not isinstance(claimed, int) or (run.fence is not None and claimed <= run.fence):
            broken.append(("H4", f"claim fence {claimed} does not rise above {run.fence}"))
        if isinstance(claimed, int):
            run.fence = claimed
    if event.fence is not None and event.fence != run.fence:
        broken.append(("H4", f"{event.type} carries fence {event.fence}, the current claim's is {run.fence}"))
    invocation = event.data.get("invocationId")
    if event.type in ("tool.intent", "tool.denied") and isinstance(invocation, str):
        run.intents.add(invocation)
    ran = event.data.get("status") not in ("error", "cancelled")
    if event.type == "tool.outcome" and ran and invocation not in run.intents:
        broken.append(("H5", f"tool.outcome of {invocation} without a tool.intent or tool.denied"))
    broken.extend(_outcome_rule(event, run, invocation))
    if event.type == "run.admitted":
        run.admitted = True
    if event.type in TERMINAL_EVENTS and run.ended_at is None:
        run.ended_at = event.seq
    run.events += 1
    return broken


def _outcome_rule(event: JournalEvent, run: _Run, invocation: object) -> list[tuple[str, str]]:
    broken: list[tuple[str, str]] = []
    if isinstance(invocation, str):
        if event.type in ("tool.intent", "tool.denied"):
            # A re-call after ``tool.reconciled`` not_applied opens the call again.
            run.open_calls[invocation] = invocation in run.outcomes
        elif event.type == "tool.outcome":
            if invocation in run.outcomes:
                broken.append(("H8", f"a second tool.outcome of {invocation}"))
            run.outcomes.add(invocation)
            run.open_calls[invocation] = True
        elif event.type == "tool.unknown":
            run.open_calls[invocation] = True
    if event.type in TERMINAL_EVENTS and run.ended_at is None:
        broken.extend(
            ("H8", f"{event.type} with tool call {call} left without an outcome")
            for call, ended in run.open_calls.items()
            if not ended
        )
    return broken


def journal_history_violations(events: Iterable[JournalEvent]) -> list[HistoryViolation]:
    """Every rule each stream in ``events`` (each in seq order, streams in any order) breaks."""
    streams: dict[str, list[JournalEvent]] = {}
    for event in events:
        streams.setdefault(event.stream_id, []).append(event)
    violations: list[HistoryViolation] = []
    runs: dict[str, _Run] = {}
    for stream in streams.values():
        violations.extend(_gaps(stream))
        for event in stream:
            if event.run_id is None:
                continue
            for rule, message in _run_rules(event, runs.setdefault(event.run_id, _Run())):
                violations.append(HistoryViolation(rule, event.stream_id, event.seq, event.run_id, message))
    return violations


def journal_events(journal: InMemoryJournal) -> list[JournalEvent]:
    """Every stream of ``journal``, each in seq order."""
    return [event for stream_id in journal.stream_ids() for event in journal.events(stream_id)]


def assert_journal_history(events: Iterable[JournalEvent]) -> None:
    violations = journal_history_violations(events)
    if violations:
        raise AssertionError("invalid journal history:\n" + "\n".join(f"  {v}" for v in violations))


__all__ = [
    "TERMINAL_EVENTS",
    "HistoryViolation",
    "assert_journal_history",
    "journal_events",
    "journal_history_violations",
]
