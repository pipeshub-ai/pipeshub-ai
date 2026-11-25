"""Waiting through one poller per event loop.

The slot files are scanned once per poll for all of a loop's waiters, not
once per waiter; free slots wake waiters oldest first; a local release wakes
exactly one; an org held back by its share does not block the others.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    FixedLimit,
    HoldReason,
    LiveView,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.services.resource_governor.shared_slots import SharedSlots, SlotDenied
from tests.support.settle import settle

if TYPE_CHECKING:
    from collections import Counter
    from pathlib import Path

    from app.services.resource_governor.shared_slots import SlotHold


class _CountingSlots(SharedSlots):
    def __init__(self, directory: Path, capacity: int) -> None:
        super().__init__(directory, capacity)
        self.tries = 0
        self.looks = 0
        self.busy = False

    def try_acquire_tagged(
        self, limit: int, *, tag: bytes, tag_limit: int | None,
    ) -> SlotHold | SlotDenied:
        self.tries += 1
        if self.busy:
            return SlotDenied("busy")
        return super().try_acquire_tagged(limit, tag=tag, tag_limit=tag_limit)

    def held_by_tag(self, budget: float = 0.0) -> Counter[bytes]:
        self.looks += 1
        return super().held_by_tag(budget)


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def wait_start(self, reason: HoldReason) -> None:
        self.events.append(("wait_start", reason))

    def wait_reason(self, reason: HoldReason) -> None:
        self.events.append(("wait_reason", reason))

    def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
        self.events.append(("outcome", outcome, reason))


class _Provider(FixedLimit):
    def __init__(self, capacity: SandboxCapacity) -> None:
        super().__init__(capacity)
        self.limit: int | None = None
        self.waits: list[HoldReason] = []

    def view(self) -> LiveView:
        base = super().view()
        if self.limit is None:
            return base
        return LiveView(**{**base.__dict__, "limit": self.limit, "shrink_cause": "cpu"})

    def waiting(self, reason: HoldReason, waited_s: float) -> None:
        self.waits.append(reason)


def _gov(
    tmp_path: Path, opened: list, *, limit: int, org_cap: int | None = None, poll_s: float = 0.02,
    observers: tuple = (),
) -> tuple[SandboxResourceGovernor, _CountingSlots]:
    slots = _CountingSlots(tmp_path / "slots", 16)
    opened.append(slots)
    capacity = SandboxCapacity.fixed(limit, org_cap, slot_files=16)
    governor = SandboxResourceGovernor(
        capacity, slots, limit_provider=_Provider(capacity), observers=observers, poll_s=poll_s,
        rng=random.Random(1),
    )
    return governor, slots



@pytest.fixture
def opened() -> list:
    slots: list = []
    yield slots
    for s in slots:
        s.close()


async def test_two_hundred_waiters_cost_one_scan_per_poll(tmp_path: Path, opened) -> None:
    governor, slots = _gov(tmp_path, opened, limit=2, poll_s=0.05)
    held = [await governor.acquire() for _ in range(2)]
    tasks = [asyncio.ensure_future(governor.acquire(wait_s=30)) for _ in range(200)]
    await asyncio.sleep(0.2)  # every waiter has made its first try
    tries, looks = slots.tries, slots.looks
    await asyncio.sleep(1.0)
    # About 27 polls a second at 0.05 s with jitter; no per-waiter retries.
    assert slots.tries - tries == 0
    assert 10 <= slots.looks - looks <= 45
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for lease in held:
        await governor.release(lease)
    await asyncio.sleep(0.1)
    assert governor._queues == {}  # the poller stopped with its last waiter


async def test_a_local_release_wakes_one_waiter_in_arrival_order(tmp_path: Path, opened) -> None:
    governor, slots = _gov(tmp_path, opened, limit=1, poll_s=30.0)
    held = await governor.acquire()
    order: list[int] = []

    async def waiter(n: int) -> None:
        lease = await governor.acquire(wait_s=10)
        order.append(n)
        await asyncio.sleep(0.01)
        await governor.release(lease)

    tasks = []
    for n in range(5):
        tasks.append(asyncio.ensure_future(waiter(n)))
        await asyncio.sleep(0.01)
    tries = slots.tries
    await governor.release(held)
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert order == [0, 1, 2, 3, 4]
    # One try per handoff: nobody else was woken to fail.
    assert slots.tries - tries == 5


async def test_a_freed_slot_skips_a_waiter_held_by_its_org_share(tmp_path: Path, opened) -> None:
    governor, _ = _gov(tmp_path, opened, limit=3, org_cap=1, poll_s=0.02)
    other, _ = _gov(tmp_path, opened, limit=3, org_cap=1)
    a_held = await governor.acquire(org_id="a")
    blockers = [await other.acquire(org_id="b"), await other.acquire()]
    first = asyncio.ensure_future(governor.acquire(org_id="a", wait_s=5))  # held back by its share
    await asyncio.sleep(0.01)
    second = asyncio.ensure_future(governor.acquire(org_id="c", wait_s=5))  # held back by the total
    await asyncio.sleep(0.05)
    await other.release(blockers[1])  # freed in another worker: seen by the poll
    lease = await settle(second)
    assert not first.done()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    for item in (lease, a_held, blockers[0]):
        await (governor if item.governor is governor else other).release(item)


async def test_a_woken_waiter_that_is_cancelled_passes_its_turn(tmp_path: Path, opened) -> None:
    governor, _ = _gov(tmp_path, opened, limit=1, poll_s=30.0)
    held = await governor.acquire()
    first = asyncio.ensure_future(governor.acquire(wait_s=10))
    await asyncio.sleep(0.01)
    second = asyncio.ensure_future(governor.acquire(wait_s=10))
    await asyncio.sleep(0.01)
    await governor.release(held)
    first.cancel()  # woken, but cancelled before it ran again
    lease = await settle(second)
    await governor.release(lease)
    assert governor.held_total() == 0


async def test_the_poller_keeps_telling_the_provider_and_updates_the_reason(tmp_path: Path, opened) -> None:
    recorder = _Recorder()
    governor, slots = _gov(tmp_path, opened, limit=2, observers=(recorder,))
    provider = governor.limit_provider
    held = [await governor.acquire() for _ in range(2)]
    recorder.events.clear()
    waiter = asyncio.ensure_future(governor.acquire(wait_s=5))
    await asyncio.sleep(0.15)
    tries = slots.tries
    assert provider.waits.count(HoldReason.SLOTS_FULL) >= 3  # from the poller, not retries
    provider.limit = 1  # the live limit shrinks for CPU while it waits
    await asyncio.sleep(0.1)
    assert slots.tries == tries
    assert recorder.events[:2] == [
        ("wait_start", HoldReason.SLOTS_FULL), ("wait_reason", HoldReason.LIVE_CPU),
    ]
    assert HoldReason.LIVE_CPU in provider.waits
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert recorder.events[-1] == ("outcome", "cancelled", HoldReason.LIVE_CPU)
    for lease in held:
        await governor.release(lease)


async def test_a_busy_mutex_wait_is_not_a_wait_at_the_limit(tmp_path: Path, opened) -> None:
    recorder = _Recorder()
    governor, slots = _gov(tmp_path, opened, limit=2, observers=(recorder,))
    provider = governor.limit_provider
    slots.busy = True
    waiter = asyncio.ensure_future(governor.acquire(wait_s=5))
    await asyncio.sleep(0.1)
    slots.busy = False  # the poll finds free slots and wakes it
    lease = await settle(waiter)
    assert recorder.events == [("outcome", "granted", None)]
    assert provider.waits == []
    await governor.release(lease)


async def test_a_refusal_after_waiting_still_has_one_outcome(tmp_path: Path, opened) -> None:
    recorder = _Recorder()
    governor, _ = _gov(tmp_path, opened, limit=1, observers=(recorder,))
    held = await governor.acquire()
    recorder.events.clear()
    try:
        await governor.acquire(wait_s=0.1)
    except SandboxLimitExceeded as exc:
        assert exc.reason is HoldReason.SLOTS_FULL
    outcomes = [e for e in recorder.events if e[0] == "outcome"]
    assert outcomes == [("outcome", "refused", HoldReason.SLOTS_FULL)]
    await governor.release(held)
