"""OPS-14 (ADR-OPS-01 D6): the worker side of an engine version's retire round trip.

Node retires a version by suspending the runs pinned to it mid-turn (``run.suspended {reason: retired}`` with the
notice ``engine_retired``; the attempt is taken back as a lapsed lease is), then resumes them
(``run.resumed {cause: version_available}`` with ``engine_continued``) and a worker claims them again. Both journal
folds must read that as a lapsed-lease takeover: resumable, not terminal, not cancelling, the open turn re-driven.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_run_notice import (
    PipeshubRunNoticeData,
)
from harness_core.contracts.generated.enums import SUSPEND_REASONS
from harness_core.contracts.generated.events.run_resumed import RunResumedData
from harness_core.contracts.generated.events.run_suspended import RunSuspendedData
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.journal import JournalEvent
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.kernel.fold import fold_run
from harness_core.testing.engines.durable import SESSION, EngineWorld

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

RUN = "run-ops14"
NOTICE = "x.pipeshub.run.notice"
DIGEST = "sha256:" + "b" * 64
SINK: dict[str, JsonValue] = {"egress": "external", "argsLeaveBoundary": True}


def _retire_and_resume() -> list[tuple[str, dict[str, JsonValue], bool]]:
    """What Node journals for a retire and its resume, in order, with each event's criticality."""
    return [
        ("run.suspended", {"reason": "retired"}, True),
        (NOTICE, {"code": "engine_retired"}, False),
        ("run.resumed", {"cause": "version_available"}, True),
        (NOTICE, {"code": "engine_continued"}, False),
    ]


def _journal(*, retired: bool) -> list[JournalEvent]:
    """A run claimed, its first turn started with a write in flight; then (``retired``) Node's retire round trip;
    then a second claim. Without it, the same journal is a lapsed lease taken over."""
    events: list[JournalEvent] = []

    def add(type_: str, data: Mapping[str, JsonValue], *, writer: str, attempt: str | None, critical: bool = True) -> None:
        events.append(
            JournalEvent.model_validate(
                {
                    "org_id": "org1",
                    "session_id": "ses1",
                    "stream_id": "ses1",
                    "seq": len(events) + 1,
                    "run_id": RUN,
                    "attempt_id": attempt,
                    "fence": 2 if attempt == "a2" else 1,
                    "type": type_,
                    "schema_version": 1,
                    "critical": critical,
                    "ts": "2026-11-05T00:00:00.000Z",
                    "writer": writer,
                    "data": dict(data),
                }
            )
        )

    add("run.admitted", {"runKind": "interactive", "principalRef": "p_u"}, writer="node", attempt=None)
    add("run.claimed", {"attempt": 1, "fence": 1, "leaseMs": 30000}, writer="node", attempt="a1")
    add("turn.started", {"turn": 1}, writer="worker", attempt="a1")
    add(
        "tool.intent",
        {"invocationId": "t1.0", "toolId": "local@default/tool", "argsDigest": DIGEST, "effect": "write", "sink": SINK},
        writer="worker",
        attempt="a1",
    )
    if retired:
        for type_, data, critical in _retire_and_resume():
            add(type_, data, writer="node", attempt=None, critical=critical)
    add("run.claimed", {"attempt": 2, "fence": 2, "leaseMs": 30000}, writer="node", attempt="a2")
    return events


def test_ops14_the_contracts_carry_the_retire_reason_cause_and_notices() -> None:
    """OPS-14: ``retired``, ``version_available`` and both notice codes validate in the generated models; an
    unknown reason is still refused (the enum is closed)."""
    assert "retired" in SUSPEND_REASONS
    assert RunSuspendedData.model_validate({"reason": "retired"}).reason == "retired"
    assert RunResumedData.model_validate({"cause": "version_available"}).cause == "version_available"
    for code in ("engine_retired", "engine_continued"):
        assert PipeshubRunNoticeData.model_validate({"code": code}).code == code
    with pytest.raises(ValidationError):
        _ = RunSuspendedData.model_validate({"reason": "decommissioned"})


def test_ops14_the_loop_fold_reads_a_retired_round_trip_as_a_lapsed_lease_takeover() -> None:
    """OPS-14: ``fold_run`` parses Node's critical events (a payload it could not read would raise
    ``FoldIncompatible``), skips the non-critical notices, and ends where a lapsed-lease takeover does: running
    on attempt 2, no suspension, no cancel, the open write still to settle."""
    retired = fold_run(_journal(retired=True), run_id=RUN)
    lapsed = fold_run(_journal(retired=False), run_id=RUN)
    assert retired.status == "running"
    assert retired.status not in TERMINAL_STATUSES
    assert (retired.attempt, retired.fence) == (2, 2)
    assert retired.suspension is None
    assert not retired.cancel_requested
    assert retired.ignored_events == lapsed.ignored_events + 2
    assert retired.cursor.next_action not in ("done", "suspended")
    skip = {"seq", "ignored_events"}
    assert retired.model_dump(exclude=skip) == lapsed.model_dump(exclude=skip)


def test_ops14_the_engine_fold_reads_a_retired_round_trip_as_a_lapsed_lease_takeover() -> None:
    """OPS-14: ``fold_engine_run`` ends running with no suspension and no cancel, the started turn open (the
    kernel re-drives it, ``_next``) and the in-flight write ``intent`` (settled as a dead attempt's), exactly as
    after a lapsed lease."""
    retired = fold_engine_run(_journal(retired=True), run_id=RUN)
    lapsed = fold_engine_run(_journal(retired=False), run_id=RUN)
    assert retired.status == "running"
    assert not retired.terminal
    assert retired.suspension is None
    assert not retired.cancel_requested
    last = retired.last_turn
    assert last is not None and (last.index, last.completed) == (1, False)
    assert retired.invocations["t1.0"].state == "intent"
    assert dataclasses.replace(retired, seq=0) == dataclasses.replace(lapsed, seq=0)


def test_ops14_a_fold_stopped_at_the_retire_is_suspended_not_waiting_on_an_interaction() -> None:
    """OPS-14: between the retire and the resume the run is suspended for ``retired`` with no interaction: no
    approval for a worker to wait on, and its cursor is ``suspended`` (no worker holds it then)."""
    events = _journal(retired=True)
    held = [e for e in events if e.seq <= next(e.seq for e in events if e.type == "run.suspended")]
    loop = fold_run(held, run_id=RUN)
    assert loop.status == "suspended"
    assert loop.suspension is not None
    assert (loop.suspension.reason, loop.suspension.interaction_id) == ("retired", None)
    assert loop.cursor.next_action == "suspended"
    engine = fold_engine_run(held, run_id=RUN)
    assert (engine.status, engine.suspension) == ("suspended", "retired")
    assert engine.interactions == {}


async def test_ops14_a_run_retired_mid_turn_completes_on_its_next_claim() -> None:
    """OPS-14 end to end on the durable engine kernel: the attempt dies mid-turn (taken back), Node journals the
    retire round trip, the next claim re-drives the open turn and the run completes, with no effect twice."""
    world = EngineWorld(approvals={"create": "allow"})
    world.admit(RUN, 'tool create {"name": "ticket"}\nwrite out/report.md done\nsay finished')
    assert await world.crash_at(RUN, "worker.tool.after_effect")
    for type_, data, critical in _retire_and_resume():
        _ = world.journal.inject(SESSION, {"runId": RUN, "type": type_, "writer": "node", "critical": critical, "data": data})
    assert (await world.attempt(RUN)).status == "completed"
    assert world.journal.status(RUN) == "completed"
    assert world.duplicates() == {}
    assert world.effects[next(k for k in world.effects if k[0] == "create")] == 1
    types = world.types(RUN)
    assert types.count("run.claimed") == 2
    assert types.index("run.resumed") < len(types) - 1 - types[::-1].index("run.claimed")
    assert [e.data["turn"] for e in world.events(RUN) if e.type == "turn.completed"] == [1]
