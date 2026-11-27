"""The governor under load, cancels at every await, and odd slot errors.

Invariants checked everywhere: never more holders than the limit (or an
org's cap), every slot given back (``held_total() == 0`` at the end),
exactly one observer outcome per acquire, and no waiter left counted.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import random
import threading
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    FixedLimit,
    HoldReason,
    LiveView,
    SandboxCapacity,
    org_tag,
)
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotDenied,
)
from tests.support.settle import settle

if TYPE_CHECKING:
    from pathlib import Path

    from app.agent_loop_lib.sandbox.capacity import MachineSize
    from app.services.resource_governor.shared_slots import SlotHold


class _Outcomes:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, HoldReason | None]] = []
        self.starts = 0

    def wait_start(self, reason: HoldReason) -> None:
        self.starts += 1

    def wait_reason(self, reason: HoldReason) -> None:
        pass

    def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
        self.outcomes.append((outcome, reason))


class _Provider(FixedLimit):
    """Counts ``waiting``/``wait_ended`` pairs, and can hold ``admit_and_charge``."""

    def __init__(self, capacity: SandboxCapacity) -> None:
        super().__init__(capacity)
        self.limit: int | None = None
        self.admit_gate: asyncio.Event | None = None
        self.admitting = asyncio.Event()
        self.waited_runs = 0
        self.ended = 0

    def view(self) -> LiveView:
        base = super().view()
        if self.limit is None:
            return base
        return LiveView(**{**base.__dict__, "limit": self.limit, "shrink_cause": "memory"})

    async def admit_and_charge(self, *, held: int) -> HoldReason | None:
        if self.admit_gate is not None:
            self.admitting.set()
            await self.admit_gate.wait()
        return None

    def wait_ended(self, *, granted: bool) -> None:
        self.ended += 1


@pytest.fixture
def opened() -> list[Any]:
    slots: list[Any] = []
    yield slots
    for s in slots:
        s.close()


def _gov(
    slots: SharedSlots | LocalTaggedSlots, *, limit: int, org_cap: int | None = None, poll_s: float = 0.02,
    observers: tuple[Any, ...] = (), provider: type[FixedLimit] = FixedLimit,
) -> SandboxResourceGovernor:
    capacity = SandboxCapacity.fixed(limit, org_cap, slot_files=slots.capacity)
    return SandboxResourceGovernor(
        capacity, slots, limit_provider=provider(capacity), observers=observers, poll_s=poll_s,
        rng=random.Random(7),
    )


class _Tally:
    """Holders right now, overall and per org, with the peaks seen."""

    def __init__(self) -> None:
        self.now = 0
        self.peak = 0
        self.by_org: Counter[str] = Counter()
        self.org_peak: Counter[str] = Counter()

    def enter(self, org: str) -> None:
        self.now += 1
        self.by_org[org] += 1
        self.peak = max(self.peak, self.now)
        self.org_peak[org] = max(self.org_peak[org], self.by_org[org])

    def leave(self, org: str) -> None:
        self.now -= 1
        self.by_org[org] -= 1


async def _storm(
    governors: list[SandboxResourceGovernor], *, runs: int, orgs: list[str], seed: int,
) -> tuple[_Tally, Counter[str]]:
    rng = random.Random(seed)
    tally = _Tally()
    ends: Counter[str] = Counter()

    async def one(i: int) -> None:
        governor = governors[i % len(governors)]
        org = orgs[i % len(orgs)]
        try:
            lease = await governor.acquire(org_id=org or None, wait_s=rng.choice([0.0, 0.03, 2.0]))
        except SandboxLimitExceeded:
            ends["refused"] += 1
            return
        tally.enter(org)
        try:
            await asyncio.sleep(rng.uniform(0, 0.01))
        finally:
            tally.leave(org)
            await governor.release(lease)
            if rng.random() < 0.2:
                await governor.release(lease)  # a second release is a no-op
        ends["granted"] += 1

    tasks = [asyncio.ensure_future(one(i)) for i in range(runs)]
    for _ in range(runs // 3):
        await asyncio.sleep(rng.uniform(0, 0.005))
        victim = rng.choice(tasks)
        victim.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ends["cancelled"] = sum(isinstance(r, asyncio.CancelledError) for r in results)
    assert not [r for r in results if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)]
    return tally, ends


class TestStorm:
    @pytest.mark.parametrize("seed", [1, 2, 3])
    async def test_hundreds_of_acquires_with_cancels_leak_nothing(
        self, tmp_path: Path, opened, seed: int,
    ) -> None:
        recorders = [_Outcomes(), _Outcomes()]
        governors = []
        for recorder in recorders:
            slots = SharedSlots(tmp_path / "slots", 16)
            opened.append(slots)
            governors.append(_gov(slots, limit=5, org_cap=3, observers=(recorder,)))

        tally, ends = await _storm(governors, runs=300, orgs=["a", "b", "c", ""], seed=seed)

        assert tally.peak <= 5
        assert all(peak <= 3 for org, peak in tally.org_peak.items() if org)
        assert sum(ends.values()) == 300
        assert ends["granted"] > 0 and ends["cancelled"] > 0
        assert governors[0].held_total() == 0
        assert all(g._waiting_here == 0 for g in governors)
        assert sum(len(r.outcomes) for r in recorders) == 300
        seen = Counter(o for r in recorders for o, _ in r.outcomes)
        assert seen["refused"] == ends["refused"]
        # A cancel can land after the grant, while the run holds its slot: the
        # observer saw a grant, the run ended cancelled.
        assert seen["granted"] >= ends["granted"]
        assert seen["granted"] + seen["cancelled"] == ends["granted"] + ends["cancelled"]

    async def test_the_in_process_slots_hold_the_same_invariants(self) -> None:
        recorder = _Outcomes()
        governor = _gov(LocalTaggedSlots(16), limit=4, org_cap=2, observers=(recorder,))

        tally, ends = await _storm([governor], runs=300, orgs=["a", "b"], seed=5)

        assert tally.peak <= 4
        assert max(tally.org_peak.values()) <= 2
        assert governor.held_total() == 0
        assert governor._waiting_here == 0
        assert len(recorder.outcomes) == 300


class _ScriptedSlots(LocalTaggedSlots):
    """Answers ``busy`` while ``busy`` is set, then counts in process."""

    def __init__(self, capacity: int) -> None:
        super().__init__(capacity)
        self.busy = False
        self.error: OSError | None = None

    def try_acquire_tagged(
        self, limit: int, *, tag: bytes, tag_limit: int | None,
    ) -> SlotHold | SlotDenied:
        if self.error is not None:
            raise self.error
        if self.busy:
            return SlotDenied("busy")
        return super().try_acquire_tagged(limit, tag=tag, tag_limit=tag_limit)


class TestCancelAtEveryAwait:
    """A cancel at each await point of ``acquire`` leaves nothing held or counted."""

    async def _check(self, governor, recorder, provider, task, *, waited: bool) -> None:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert governor.held_total() == 0
        assert governor._waiting_here == 0
        assert recorder.outcomes == [("cancelled", recorder.outcomes[0][1])]
        assert provider.ended == (1 if waited else 0)

    def _make(
        self, limit: int = 1,
    ) -> tuple[SandboxResourceGovernor, _ScriptedSlots, _Outcomes, _Provider]:
        recorder = _Outcomes()
        slots = _ScriptedSlots(4)
        governor = _gov(slots, limit=limit, observers=(recorder,), provider=_Provider)
        return governor, slots, recorder, governor.limit_provider

    async def test_while_the_first_call_waits_for_sizing(self) -> None:
        recorder = _Outcomes()
        sizing = asyncio.Event()

        async def never_answers() -> tuple[MachineSize, None]:
            await sizing.wait()

        capacity = SandboxCapacity("compose", machine_reader=never_answers, slot_files=4)
        governor = SandboxResourceGovernor(
            capacity, LocalTaggedSlots(4), limit_provider=_Provider(capacity), observers=(recorder,),
        )
        task = asyncio.ensure_future(governor.acquire())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert governor.held_total() == 0
        assert recorder.outcomes == [("cancelled", None)]
        # The next call does not wait for sizing again.
        lease = await settle(governor.acquire())
        await governor.release(lease)
        capacity._task.cancel()

    async def test_during_a_busy_retry(self) -> None:
        governor, slots, recorder, provider = self._make()
        slots.busy = True
        task = asyncio.ensure_future(governor.acquire(wait_s=5))
        await asyncio.sleep(0.003)
        await self._check(governor, recorder, provider, task, waited=False)

    async def test_during_admission(self) -> None:
        governor, slots, recorder, provider = self._make()
        provider.admit_gate = asyncio.Event()
        task = asyncio.ensure_future(governor.acquire(wait_s=5))
        await provider.admitting.wait()
        assert governor.held_total() == 1
        await self._check(governor, recorder, provider, task, waited=False)

    async def test_during_the_wait_note(self) -> None:
        governor, slots, recorder, provider = self._make()
        held = await governor.acquire()
        in_note = asyncio.Event()

        async def on_wait() -> None:
            in_note.set()
            await asyncio.sleep(10)

        task = asyncio.ensure_future(governor.acquire(wait_s=5, on_wait=on_wait))
        await in_note.wait()
        await governor.release(held)
        recorder.outcomes.clear()
        await self._check(governor, recorder, provider, task, waited=True)

    async def test_during_the_poll_sleep(self) -> None:
        governor, slots, recorder, provider = self._make()
        held = await governor.acquire()
        recorder.outcomes.clear()
        task = asyncio.ensure_future(governor.acquire(wait_s=5))
        await asyncio.sleep(0.05)
        await governor.release(held)
        # The release wakes the waiter; cancel lands before it runs again.
        await self._check(governor, recorder, provider, task, waited=True)

    async def test_admission_cancelled_after_a_wait(self) -> None:
        governor, slots, recorder, provider = self._make()
        held = await governor.acquire()
        recorder.outcomes.clear()
        provider.admit_gate = asyncio.Event()
        task = asyncio.ensure_future(governor.acquire(wait_s=5))
        await asyncio.sleep(0.05)
        await governor.release(held)
        await provider.admitting.wait()
        await self._check(governor, recorder, provider, task, waited=True)


class TestSlotErrors:
    async def test_a_slot_error_is_a_refusal_once_and_holds_nothing(self) -> None:
        recorder = _Outcomes()
        slots = _ScriptedSlots(4)
        governor = _gov(slots, limit=2, observers=(recorder,), provider=_Provider)
        slots.error = OSError(errno.ENOSPC, "No space left on device")
        with pytest.raises(SandboxLimitExceeded) as caught:
            await governor.acquire(wait_s=1)
        assert str(caught.value) == "code sandbox capacity is unavailable: No space left on device"
        assert caught.value.reason is None
        assert isinstance(caught.value.__cause__, OSError)
        assert recorder.outcomes == [("refused", None)]
        assert governor._waiting_here == 0
        slots.error = None
        lease = await governor.acquire()
        await governor.release(lease)

    async def test_no_space_to_write_the_tag_holds_no_slot(
        self, tmp_path: Path, opened, monkeypatch, caplog,
    ) -> None:
        from app.services.resource_governor import shared_slots

        slots = SharedSlots(tmp_path / "slots", 4)
        opened.append(slots)
        governor = _gov(slots, limit=2)

        def no_space(*args: object) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(shared_slots.os, "pwrite", no_space)
        with caplog.at_level(logging.WARNING):
            for _ in range(2):
                with pytest.raises(SandboxLimitExceeded, match="capacity is unavailable"):
                    await governor.acquire(org_id="o")
        monkeypatch.undo()
        # Logged once, with the directory.
        lines = [r.getMessage() for r in caplog.records if "cannot be used" in r.getMessage()]
        assert len(lines) == 1 and str(tmp_path / "slots") in lines[0]
        assert governor.held_total() == 0
        # The mutex was given back: the next call is served.
        lease = await governor.acquire(org_id="o")
        assert governor.snapshot()["by_org"] == {org_tag("o").decode(): 1}
        await governor.release(lease)

    async def test_releasing_after_close_is_harmless(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path / "slots", 4)
        governor = _gov(slots, limit=2)
        lease = await governor.acquire()
        governor.close()
        await governor.release(lease)
        assert governor.held_total() == 0
        with pytest.raises(SandboxLimitExceeded, match="unavailable: shared slots are closed"):
            await governor.acquire()


class TestWakeups:
    async def test_capacity_changed_from_another_thread_wakes_a_waiter_at_once(self) -> None:
        governor = _gov(LocalTaggedSlots(4), limit=4, poll_s=30.0, provider=_Provider)
        provider = governor.limit_provider
        provider.limit = 1
        held = await governor.acquire()
        waiter = asyncio.ensure_future(governor.acquire(wait_s=20))
        await asyncio.sleep(0.05)
        assert not waiter.done()

        def raise_limit() -> None:
            provider.limit = 2
            governor.capacity_changed()

        started = time.monotonic()
        thread = threading.Thread(target=raise_limit)
        thread.start()
        lease = await asyncio.wait_for(waiter, 2)
        thread.join()
        # wall-clock-ok: missed, the waiter waits for the 30 s poll or its 20 s wait; 1 s is a twentieth of that
        assert time.monotonic() - started < 1.0
        await governor.release(lease)
        await governor.release(held)

    async def test_capacity_changed_with_waiters_on_two_loops(self) -> None:
        governor = _gov(LocalTaggedSlots(4), limit=4, poll_s=30.0, provider=_Provider)
        provider = governor.limit_provider
        provider.limit = 1
        held = await governor.acquire()
        got: list[float] = []
        ready = threading.Event()

        def other_loop() -> None:
            async def wait() -> None:
                ready.set()
                lease = await governor.acquire(wait_s=20)
                got.append(time.monotonic())
                await governor.release(lease)

            asyncio.run(wait())

        thread = threading.Thread(target=other_loop)
        thread.start()
        ready.wait(2)
        await asyncio.sleep(0.05)
        provider.limit = 2
        started = time.monotonic()
        governor.capacity_changed()
        thread.join(5)
        # wall-clock-ok: missed, the other loop's waiter waits for the 30 s poll or its 20 s wait; 1 s is a twentieth of that
        assert got and got[0] - started < 1.0
        await governor.release(held)

    async def test_a_waker_after_its_loop_closed_is_ignored(self) -> None:
        governor = _gov(LocalTaggedSlots(4), limit=1, poll_s=30.0)
        held = await governor.acquire()

        async def abandon() -> None:
            task = asyncio.ensure_future(governor.acquire(wait_s=20))
            await asyncio.sleep(0.01)
            task.cancel()

        thread = threading.Thread(target=lambda: asyncio.run(abandon()))
        thread.start()
        thread.join(5)
        governor.capacity_changed()  # must not raise
        await governor.release(held)
        assert governor.held_total() == 0


class TestCaps:
    @pytest.mark.parametrize("total", [-5, -1])
    async def test_a_negative_explicit_total_is_the_largest_cap_not_a_refusal(self, total: int) -> None:
        governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=total))
        assert governor.capacity.ceiling() >= 1
        lease = await governor.acquire()
        await governor.release(lease)

    async def test_e2b_counts_up_to_its_fixed_ceiling(self, tmp_path: Path, opened) -> None:
        from app.agent_loop_lib.sandbox.capacity import CapacitySettings

        capacity = SandboxCapacity("e2b", CapacitySettings(), slot_files=256)
        assert capacity.sized and capacity.ceiling() == 100
        slots = SharedSlots(tmp_path / "slots", 256)
        opened.append(slots)
        governor = SandboxResourceGovernor(capacity, slots, limit_provider=FixedLimit(capacity))
        leases = [await governor.acquire(org_id="o") for _ in range(100)]
        with pytest.raises(SandboxLimitExceeded) as caught:
            await governor.acquire(org_id="o")
        assert caught.value.reason is HoldReason.SLOTS_FULL
        assert "all 100 code sandboxes" in str(caught.value)
        for lease in leases:
            await governor.release(lease)
        assert governor.held_total() == 0
