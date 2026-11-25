"""P6.4 EC-1..EC-4: E0's controller on a child's fork, one writer per workspace,
and parallel `code_task` children merged into their parent."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import override

import pytest

from harness_core.engines.controller import WRITER_LEASE_UNRENEWED
from harness_core.journal.types import AttemptToken
from harness_core.testing.clock import ManualClock
from harness_core.workspace.forks import (
    JoinedChild,
    StoreChildWorkspaces,
    child_fork_id,
)
from harness_core.workspace.memory import InMemoryWorkspaceWriters
from harness_core.workspace.types import (
    ObjectScope,
    WorkspaceStoreUnavailable,
    WorkspaceWriterBusy,
    WriterLease,
)
from tests.unit.harness_core.engines.test_controller import (
    ORG,
    RUN,
    SCOPE,
    SESSION,
    Ticks,
    World,
    _StubAdapter,  # pyright: ignore[reportPrivateUsage]
    task,
)

WS = f"ws:{SESSION}"


@dataclass
class ForkWorld(World):
    writers: InMemoryWorkspaceWriters = field(default_factory=InMemoryWorkspaceWriters)  # pyright: ignore[reportIncompatibleVariableOverride]

    def child(self, child_run_id: str):  # noqa: ANN201
        return self.run(
            run_id=child_run_id,
            attempt_id=f"{child_run_id}.a1",
            workspace=child_fork_id(SESSION, child_run_id),
        )

    async def fork(self, child_run_id: str) -> str:
        head = await self.store.head(ObjectScope(ORG), WS)
        fork_id = child_fork_id(SESSION, child_run_id)
        _ = await self.store.fork(ObjectScope(ORG), WS, at_revision=head.revision, fork_id=fork_id)
        return fork_id

    async def files(self, workspace: str = WS) -> dict[str, bytes]:
        head = await self.store.head(ObjectScope(ORG), workspace)
        return {p: await self.objects.get(SCOPE, e.content) for p, e in head.files.items()}

    async def merge(self, child_run_id: str):  # noqa: ANN201
        merger = StoreChildWorkspaces(lambda _token: (self.store, self.writers))
        return await merger.merge(
            AttemptToken(RUN, f"{RUN}.a1", 1),
            JoinedChild(ORG, SESSION, RUN, WS, child_run_id, "answered"),
        )


@pytest.fixture
def world() -> ForkWorld:
    return ForkWorld()


async def test_ec1_a_child_turn_works_on_its_fork_in_the_sessions_partition(world: ForkWorld) -> None:
    await world.seed({"README.md": b"base\n"})
    fork = await world.fork("child-1")
    outcome = await world.controller.run_turn(world.child("child-1"), task(0, "write notes.md from-child\nsay ok"))
    assert outcome.reason == "completed"
    assert await world.files(fork) == {"README.md": b"base\n", "notes.md": b"from-child"}
    assert await world.files() == {"README.md": b"base\n"}
    assert {s.manifest.workspace_id for s in world.sink.saves} == {fork}
    assert world.child("child-1").object_scope == SCOPE
    [(spec, _)] = world.broker.acquired
    assert spec.labels["runId"] == "child-1"


async def test_ec2_a_second_writer_of_one_workspace_is_refused_before_any_sandbox(
    world: ForkWorld, caplog: pytest.LogCaptureFixture
) -> None:
    first = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    second = dataclasses.replace(world.run(run_id="code-task-2", attempt_id="code-task-2.a1"))
    with caplog.at_level(logging.WARNING), pytest.raises(WorkspaceWriterBusy) as busy:
        await world.controller.run_turn(second, task(0, "write b.md y\nsay ok"))
    assert busy.value.holder == RUN
    assert len(world.broker.acquired) == 1
    refusals = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.run.refused"]
    assert [r.harness["reasonCode"] for r in refusals] == ["workspace_writer_busy"]  # type: ignore[attr-defined]
    world.gateway.release()
    assert (await asyncio.wait_for(first, 5)).reason == "completed"
    third = await world.controller.run_turn(second, task(0, "write b.md y\nsay ok"))
    assert third.reason == "completed"
    assert await world.files() == {"a.md": b"x", "b.md": b"y"}


async def test_ec3_parallel_children_on_forks_merge_disjoint_and_report_overlap(world: ForkWorld) -> None:
    await world.seed({"shared.md": b"base"})
    await world.fork("child-a")
    await world.fork("child-b")
    outcomes = await asyncio.gather(
        world.controller.run_turn(world.child("child-a"), task(0, "write a.md from-a\nwrite shared.md A\nsay ok")),
        world.controller.run_turn(world.child("child-b"), task(0, "write b.md from-b\nwrite shared.md B\nsay ok")),
    )
    assert [o.reason for o in outcomes] == ["completed", "completed"]
    first = await world.merge("child-a")
    second = await world.merge("child-b")
    assert first is not None and second is not None
    assert (first.status, first.merged) == ("merged", ("a.md", "shared.md"))
    assert (second.status, second.merged, second.conflicts) == ("partial", ("b.md",), (("shared.md", "both_changed"),))
    assert await world.files() == {"shared.md": b"A", "a.md": b"from-a", "b.md": b"from-b"}
    theirs = await world.store.revision(ObjectScope(ORG), second.fork_id, second.fork_revision or 0)
    assert await world.objects.get(SCOPE, theirs.files["shared.md"].content) == b"B"


def _lease(world: ForkWorld) -> WriterLease | None:
    return world.writers._leases.get((ORG, WS))  # pyright: ignore[reportPrivateUsage]


async def test_ec4_the_lease_is_renewed_while_the_turn_runs_and_released_after(world: ForkWorld) -> None:
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "hold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    taken = _lease(world)
    assert taken is not None and taken.holder == RUN
    world.clock.advance(world.config.writer_lease_ms / 1000 / 2)
    for _ in range(1000):
        current = _lease(world)
        if current is not None and current.expires_at_ms > taken.expires_at_ms:
            break
        await asyncio.sleep(0.002)
    current = _lease(world)
    assert current is not None and current.expires_at_ms > taken.expires_at_ms
    world.gateway.release()
    assert (await asyncio.wait_for(runner, 5)).reason == "completed"
    assert _lease(world) is None


async def test_ec4_the_lease_is_released_when_the_turn_fails() -> None:
    world = ForkWorld(adapter=_StubAdapter(None, fail=True))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await world.controller.run_turn(world.run(), task(0, "x"))
    assert _lease(world) is None and len(world.broker.released) == 1


async def test_ec4_a_lost_lease_stops_the_turn(world: ForkWorld, caplog: pytest.LogCaptureFixture) -> None:
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    # The lease lapsed (a stalled worker) and another writer took it, as the store would let it.
    world.writers._leases[(ORG, WS)] = WriterLease(WS, "usurper", 10**15)  # pyright: ignore[reportPrivateUsage]
    world.clock.advance(world.config.writer_lease_ms / 1000 / 2)
    with caplog.at_level(logging.WARNING):
        for _ in range(1000):
            if any(getattr(r, "harness_event", "") == "engine.turn.writer_lost" for r in caplog.records):
                break
            await asyncio.sleep(0.002)
        world.gateway.release()
        outcome = await asyncio.wait_for(runner, 5)
    assert any(getattr(r, "harness_event", "") == "engine.turn.writer_lost" for r in caplog.records)
    assert outcome.reason != "completed" or outcome.final_text != "ok"
    assert world.sink.saves == []
    assert (await world.store.head(ObjectScope(ORG), WS)).revision == 0
    held = _lease(world)
    assert held is not None and held.holder == "usurper"


class FlakyWriters(InMemoryWorkspaceWriters):
    """The writer lease store; after the turn took its lease, each renewal succeeds or finds the store
    unreachable as ``renewals`` says (later ones succeed)."""

    def __init__(self, *, clock: ManualClock, renewals: list[bool]) -> None:
        super().__init__(clock=clock)
        self.renewals = renewals
        self.acquired = 0
        self.refused = 0

    @override
    async def acquire(self, scope: ObjectScope, workspace_id: str, *, holder: str, ttl_ms: int) -> WriterLease:
        self.acquired += 1
        if self.acquired > 1 and self.renewals and not self.renewals.pop(0):
            self.refused += 1
            raise WorkspaceStoreUnavailable("lease store unreachable")
        return await super().acquire(scope, workspace_id, holder=holder, ttl_ms=ttl_ms)


def _flaky_world(renewals: list[bool]) -> tuple[ForkWorld, FlakyWriters, Ticks]:
    clock = ManualClock()
    writers = FlakyWriters(clock=clock, renewals=renewals)
    ticks = Ticks(clock)
    return ForkWorld(clock=clock, writers=writers, sleep=ticks.sleep), writers, ticks


def _now_ms(world: ForkWorld) -> int:
    return int(world.clock.now().timestamp() * 1000)


async def test_fx3_1_a_renewal_that_fails_keeps_the_writer_and_a_later_one_extends_the_lease() -> None:
    world, writers, ticks = _flaky_world([False, True, False])
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    await ticks.parked()
    taken = _lease(world)
    assert taken is not None and taken.holder == RUN
    third = world.config.writer_lease_ms / 3000
    await ticks.advance(third)
    assert writers.refused == 1 and _lease(world) == taken
    await ticks.advance(third)
    renewed = _lease(world)
    assert renewed is not None and renewed.expires_at_ms > taken.expires_at_ms
    # Counted from the renewal that succeeded, the next failure leaves time for another try.
    await ticks.advance(third)
    assert writers.refused == 2
    world.gateway.release()
    outcome = await asyncio.wait_for(runner, 5)
    assert outcome.reason == "completed"
    # The writer was never lost: the two minutes' autosave still reached the workspace.
    assert [s.reason for s in world.sink.saves] == ["autosave"]
    assert await world.files() == {"a.md": b"x"}


async def test_fx3_2_renewals_failing_until_the_lease_could_lapse_stop_the_turn_while_it_is_still_held(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world, writers, ticks = _flaky_world([False] * 10)
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    await ticks.parked()
    taken = _lease(world)
    assert taken is not None
    third = world.config.writer_lease_ms / 3000
    with caplog.at_level(logging.WARNING):
        await ticks.advance(third)
        assert not any(getattr(r, "harness_event", "") == "engine.turn.writer_lost" for r in caplog.records)
        _ = world.clock.advance(third)
        outcome = await asyncio.wait_for(runner, 5)
    assert writers.refused == 2
    lost = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.turn.writer_lost"]
    assert [r.harness["reasonCode"] for r in lost] == [WRITER_LEASE_UNRENEWED]  # type: ignore[attr-defined]
    # Stopped before the lease could lapse, so no other writer could have taken the workspace meanwhile.
    assert _now_ms(world) < taken.expires_at_ms
    assert outcome.reason != "completed" or outcome.final_text != "ok"
    assert world.sink.saves == []
    assert (await world.store.head(ObjectScope(ORG), WS)).revision == 0
