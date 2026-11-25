"""SL-05 (C2.02): the run state machine of ``InMemoryJournal`` (the reference for Node's journal) as a table.

The durable unit is the run and the conversation is the session (WP-01 ADR), so the table is over the run's
statuses: queued, running, cancelling (running with a cancel requested), suspended for an approval or for input,
and the ended ones. Every cell names what an operation must answer from that status: accepted (and the status it
leaves), or refused with a typed error and nothing changed — status, fence, open interactions, queued input and
the stream. The expectations are written out, not derived from the code; Node's side of the same machine is
``backend/nodejs/apps/tests/modules/agent-harness/sl05-run-state-machine.test.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.journal import InMemoryJournal, db_timestamp
from harness_core.journal.types import (
    AttemptToken,
    Cancelled,
    CommitRejected,
    CommitRequest,
    Complete,
    Fail,
    InteractionNotOpen,
    NewEvent,
    OwnershipLost,
    Quarantine,
    RunNotClaimable,
    Suspend,
    Yield,
)
from harness_core.testing import ManualClock
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.journal.types import Transition

RUN, SESSION = "run1", "ses1"
LEASE_MS = 30_000
IX = "ix1"


@dataclass
class World:
    clock: ManualClock
    journal: InMemoryJournal
    token: AttemptToken | None = None
    commands: int = 0

    @staticmethod
    def new() -> World:
        clock = ManualClock()
        journal = InMemoryJournal(clock=clock, lease_ms=LEASE_MS)
        journal.admit(RUN, org_id="org1", session_id=SESSION, spec=minimal_run_spec(), input_text="hello")
        return World(clock, journal)

    def status(self) -> str:
        """The run's status as the table names it."""
        status = self.journal.status(RUN)
        if status == "running" and self.journal._runs[RUN].cancel_requested:  # pyright: ignore[reportPrivateUsage]
            return "cancelling"
        if status == "suspended":
            return "waiting_approval" if self.journal.open_interactions(RUN) else "paused"
        return status

    def view(self) -> tuple[object, ...]:
        run = self.journal._runs[RUN]  # pyright: ignore[reportPrivateUsage]
        return (
            self.status(),
            run.fence,
            self.journal.open_interactions(RUN),
            self.journal.pending_inputs(RUN),
            [(e.seq, e.type) for e in self.journal.events(SESSION)],
        )

    async def claim(self) -> None:
        claim = await self.journal.claim(RUN, worker_id="w1")
        assert claim is not None
        self.token = AttemptToken(claim.run_id, claim.attempt_id, claim.fence)

    def _token(self) -> AttemptToken:
        assert self.token is not None, "every start holds an attempt token"
        return self.token

    async def commit(self, *events: NewEvent, transition: Transition | None = None) -> None:
        self.commands += 1
        request = CommitRequest(f"cmd-{self.commands}", self._token(), events, transition)
        _ = await self.journal.commit(request)

    async def renew(self) -> None:
        _ = await self.journal.renew(self._token())

    async def release(self) -> None:
        await self.journal.release(self._token(), reason="drain")

    async def lapse(self) -> None:
        """The attempt's lease runs out and the sweeper passes."""
        _ = self.clock.advance(LEASE_MS / 1000 + 1)
        _ = self.journal.sweep()


def asked(interaction_id: str = IX) -> NewEvent:
    return NewEvent(
        "interaction.requested",
        {"interactionId": interaction_id, "kind": "approval", "bindingDigest": "sha256:" + "1" * 64},
    )


def _now(w: World) -> str:
    return db_timestamp(w.clock.now())


type Step = Callable[[World], Awaitable[None]]


async def _claim(w: World) -> None:
    await w.claim()


async def _yield(w: World) -> None:
    await w.commit(transition=Yield(_now(w), "drain"))


async def _ask(w: World) -> None:
    await w.commit(asked())


async def _cancel(w: World) -> None:
    w.journal.request_cancel(RUN)


# How each status is reached; every start holds an attempt token. Ended runs keep an interaction they opened, so
# a late decision on it is judged too.
SETUP: dict[str, list[Step]] = {
    "queued": [_claim, _yield],
    "running": [_claim],
    "cancelling": [_claim, _cancel],
    "waiting_approval": [_claim, lambda w: w.commit(asked(), transition=Suspend("approval", IX))],
    "paused": [_claim, lambda w: w.commit(transition=Suspend("input"))],
    "completed": [_claim, _ask, lambda w: w.commit(transition=Complete())],
    "failed": [_claim, _ask, lambda w: w.commit(transition=Fail("worker_failed"))],
    "cancelled": [_claim, _ask, lambda w: w.commit(transition=Cancelled())],
    "quarantined": [_claim, _ask, lambda w: w.commit(transition=Quarantine(attempts=3))],
}

OPS: dict[str, Step] = {
    "claim": _claim,
    "renew": lambda w: w.renew(),
    "release": lambda w: w.release(),
    "lapse": lambda w: w.lapse(),
    "commit:progress": lambda w: w.commit(NewEvent("turn.started", {"turn": 1})),
    "commit:ask": lambda w: w.commit(asked("ix2")),
    "commit:suspend_approval": lambda w: w.commit(asked("ix2"), transition=Suspend("approval", "ix2")),
    "commit:suspend_input": lambda w: w.commit(transition=Suspend("input")),
    "commit:yield": _yield,
    "commit:complete": lambda w: w.commit(transition=Complete()),
    "commit:fail": lambda w: w.commit(transition=Fail("worker_failed")),
    "commit:cancelled": lambda w: w.commit(transition=Cancelled()),
    "commit:quarantine": lambda w: w.commit(transition=Quarantine(attempts=3)),
    "cancel": _cancel,
    "approve": lambda w: _resolve(w, IX),
    "approve_not_asked": lambda w: _resolve(w, "ix-never-asked"),
    "message": lambda w: _queue(w),
}


async def _resolve(w: World, interaction_id: str) -> None:
    w.journal.resolve_interaction(RUN, interaction_id, "approved")


async def _queue(w: World) -> None:
    _ = w.journal.queue_input(RUN, "one more thing")


type Cell = str | type[Exception]
LOST = OwnershipLost
INVALID = CommitRejected

IDLE_WORKER: dict[str, Cell] = {
    "renew": LOST,
    "release": LOST,
    **{op: LOST for op in OPS if op.startswith("commit:")},
}


def _ended(status: str) -> dict[str, Cell]:
    return {
        "claim": RunNotClaimable,
        **IDLE_WORKER,
        "lapse": status,
        # An ended run takes no cancel: nothing is written (Node answers INVALID_TRANSITION, sl05 TS table).
        "cancel": status,
        "approve": InteractionNotOpen,
        "approve_not_asked": InteractionNotOpen,
        "message": RunNotClaimable,
    }


TABLE: dict[str, dict[str, Cell]] = {
    "queued": {
        "claim": "running",
        **IDLE_WORKER,
        "lapse": "queued",
        "cancel": "cancelled",
        "approve": InteractionNotOpen,
        "approve_not_asked": InteractionNotOpen,
        "message": "queued",
    },
    "running": {
        "claim": RunNotClaimable,
        "renew": "running",
        "release": "queued",
        "lapse": "queued",
        "commit:progress": "running",
        "commit:ask": "running",
        "commit:suspend_approval": "waiting_approval",
        "commit:suspend_input": "paused",
        "commit:yield": "queued",
        "commit:complete": "completed",
        "commit:fail": "failed",
        "commit:cancelled": "cancelled",
        "commit:quarantine": "quarantined",
        "cancel": "cancelling",
        "approve": InteractionNotOpen,
        "approve_not_asked": InteractionNotOpen,
        "message": "running",
    },
    # A cancel was requested of the running attempt: it may still report progress, and it may only end the run.
    "cancelling": {
        "claim": RunNotClaimable,
        "renew": "cancelling",
        "release": INVALID,
        # Nobody holds the run any more: the cancel completes.
        "lapse": "cancelled",
        "commit:progress": "cancelling",
        "commit:ask": "cancelling",
        "commit:suspend_approval": INVALID,
        "commit:suspend_input": INVALID,
        "commit:yield": INVALID,
        "commit:complete": INVALID,
        "commit:fail": "failed",
        "commit:cancelled": "cancelled",
        "commit:quarantine": "quarantined",
        "cancel": "cancelling",
        "approve": InteractionNotOpen,
        "approve_not_asked": InteractionNotOpen,
        "message": RunNotClaimable,
    },
    "waiting_approval": {
        "claim": RunNotClaimable,
        **IDLE_WORKER,
        "lapse": "waiting_approval",
        "cancel": "cancelled",
        "approve": "queued",
        "approve_not_asked": InteractionNotOpen,
        "message": "waiting_approval",
    },
    "paused": {
        "claim": RunNotClaimable,
        **IDLE_WORKER,
        "lapse": "paused",
        "cancel": "cancelled",
        "approve": InteractionNotOpen,
        "approve_not_asked": InteractionNotOpen,
        "message": "paused",
    },
    "completed": _ended("completed"),
    "failed": _ended("failed"),
    "cancelled": _ended("cancelled"),
    "quarantined": _ended("quarantined"),
}


def test_sl05_the_table_covers_every_operation_from_every_status() -> None:
    assert set(TABLE) == set(SETUP)
    for start, row in TABLE.items():
        assert set(row) == set(OPS), start


CASES = [(start, op) for start in TABLE for op in OPS]


async def _world_at(start: str) -> World:
    world = World.new()
    for step in SETUP[start]:
        await step(world)
    assert world.status() == start
    return world


@pytest.mark.parametrize(("start", "op"), CASES, ids=[f"{s}+{o}" for s, o in CASES])
async def test_sl05_every_operation_from_every_run_status(start: str, op: str) -> None:
    world = await _world_at(start)
    cell = TABLE[start][op]
    before = world.view()
    if isinstance(cell, str):
        await OPS[op](world)
        assert world.status() == cell
        return
    with pytest.raises(cell):
        await OPS[op](world)
    assert world.view() == before, "a refused operation changes nothing"


async def test_sl05_an_ended_run_with_an_open_interaction_takes_no_decision() -> None:
    """The illegal transition the table found: a decision on an interaction the run opened before it ended."""
    world = await _world_at("failed")
    assert world.journal.open_interactions(RUN) == {IX: "approval"}
    before = world.view()
    with pytest.raises(InteractionNotOpen):
        world.journal.resolve_interaction(RUN, IX, "approved")
    assert world.view() == before
