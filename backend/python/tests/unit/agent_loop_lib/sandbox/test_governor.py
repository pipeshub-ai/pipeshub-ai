"""Tests for SandboxResourceGovernor — slot accounting shared by every worker."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    FixedLimit,
    HoldReason,
    LiveView,
    SandboxCapacity,
    hold_reason,
    org_tag,
)
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
    _open_slots,
    message,
)
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotDenied,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def governor() -> SandboxResourceGovernor:
    return SandboxResourceGovernor.in_process()


class TestSandboxResourceGovernor:
    async def test_acquire_and_release(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="o1")
        assert governor.snapshot()["total"] == 1

        await governor.release(lease)
        assert governor.snapshot()["total"] == 0

    async def test_total_limit_enforced(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=2))
        l1 = await gov.acquire()
        l2 = await gov.acquire()
        assert gov.snapshot()["total"] == 2

        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire()

        await gov.release(l1)
        await gov.release(l2)

    async def test_per_org_limit_enforced(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_per_org=1))
        l1 = await gov.acquire(org_id="a")

        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire(org_id="a")

        l2 = await gov.acquire(org_id="b")
        assert gov.snapshot()["total"] == 2

        await gov.release(l1)
        await gov.release(l2)

    async def test_release_is_idempotent(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="x")
        await governor.release(lease)
        await governor.release(lease)
        assert governor.snapshot()["total"] == 0

    async def test_snapshot_shows_per_org_counts(self, governor: SandboxResourceGovernor) -> None:
        lx = await governor.acquire(org_id="x")
        ly = await governor.acquire(org_id="y")

        snap = governor.snapshot()
        assert snap["by_org"] == {org_tag("x").decode(): 1, org_tag("y").decode(): 1}

        await governor.release(lx)
        await governor.release(ly)

    async def test_release_cleans_up_zero_orgs(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="a")
        await governor.release(lease)
        assert "a" not in governor.snapshot()["by_org"]

    async def test_default_limits_leave_the_caps_to_the_environment(self) -> None:
        limits = GovernorLimits()
        assert limits.max_total_sandboxes is None
        assert limits.max_per_org is None

    async def test_none_limits_mean_unlimited(self) -> None:
        gov = SandboxResourceGovernor.in_process(
            GovernorLimits(max_total_sandboxes=None, max_per_org=None)
        )
        leases = [await gov.acquire(org_id="org") for _ in range(100)]
        assert gov.snapshot()["total"] == 100

        for lease in leases:
            await gov.release(lease)

    async def test_governor_lease_release_method(self, governor: SandboxResourceGovernor) -> None:
        lease = await governor.acquire(org_id="z")
        assert governor.snapshot()["total"] == 1

        await lease.release()
        assert governor.snapshot()["total"] == 0

    async def test_concurrent_acquire_release(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=None, max_per_org=None))

        async def _cycle() -> None:
            lease = await gov.acquire(org_id="c")
            await asyncio.sleep(0)
            await gov.release(lease)

        await asyncio.gather(*[_cycle() for _ in range(20)])
        assert gov.snapshot()["total"] == 0

    async def test_acquire_waits_for_a_released_slot(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_per_org=1))
        held = await gov.acquire(org_id="a")
        told: list[bool] = []

        async def on_wait() -> None:
            told.append(True)

        waiter = asyncio.create_task(gov.acquire(org_id="a", wait_s=5, on_wait=on_wait))
        await asyncio.sleep(0.02)
        assert told == [True] and not waiter.done()

        await gov.release(held)
        lease = await asyncio.wait_for(waiter, 1)
        assert gov.snapshot()["total"] == 1
        await gov.release(lease)

    async def test_acquire_wait_times_out(self) -> None:
        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        held = await gov.acquire()
        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire(wait_s=0.05)
        assert gov.snapshot()["total"] == 1
        await gov.release(held)



class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def wait_start(self, reason: HoldReason) -> None:
        self.events.append(("wait_start", reason))

    def wait_reason(self, reason: HoldReason) -> None:
        self.events.append(("wait_reason", reason))

    def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
        self.events.append(("outcome", outcome, reason))

    def outcomes(self) -> list[tuple]:
        return [e for e in self.events if e[0] == "outcome"]


class _Provider(FixedLimit):
    """A limit provider whose limit and floor answers the test sets."""

    def __init__(self, capacity: SandboxCapacity, *, limit: int | None = None) -> None:
        super().__init__(capacity)
        self.limit = limit
        self.floors: list[HoldReason | None] = []
        self.admit_calls = 0
        self.admit_gate: asyncio.Event | None = None
        self.waits: list[HoldReason] = []
        self.ended: list[bool] = []

    def view(self) -> LiveView:
        base = super().view()
        if self.limit is None:
            return base
        return LiveView(**{**base.__dict__, "limit": self.limit, "shrink_cause": "cpu"})

    async def admit_and_charge(self, *, held: int) -> HoldReason | None:
        self.admit_calls += 1
        if self.admit_gate is not None:
            await self.admit_gate.wait()
        return self.floors.pop(0) if self.floors else None

    def waiting(self, reason: HoldReason, waited_s: float) -> None:
        self.waits.append(reason)

    def wait_ended(self, *, granted: bool) -> None:
        self.ended.append(granted)


@pytest.fixture
def opened() -> list[SharedSlots]:
    slots: list[SharedSlots] = []
    yield slots
    for s in slots:
        s.close()


def _gov(
    tmp_path: Path, opened: list[SharedSlots], *, limit: int = 2, org_cap: int | None = None,
    observers=(), provider: type[_Provider] | None = None, poll_s: float = 0.05,
) -> SandboxResourceGovernor:
    capacity = SandboxCapacity.fixed(limit, org_cap, slot_files=8)
    slots = SharedSlots(tmp_path, 8)
    opened.append(slots)
    limit_provider = (provider or FixedLimit)(capacity)
    return SandboxResourceGovernor(
        capacity, slots, limit_provider=limit_provider, observers=observers, poll_s=poll_s,
    )


class TestSharedAcrossWorkers:
    async def test_two_workers_share_the_total(self, tmp_path: Path, opened) -> None:
        a, b = _gov(tmp_path, opened), _gov(tmp_path, opened)
        await a.acquire()
        await b.acquire()
        with pytest.raises(SandboxLimitExceeded) as caught:
            await b.acquire()
        assert caught.value.reason is HoldReason.SLOTS_FULL
        assert a.snapshot()["total"] == 2 and a.snapshot()["shared"] is True

    async def test_two_workers_share_the_org_cap(self, tmp_path: Path, opened) -> None:
        a, b = _gov(tmp_path, opened, limit=6, org_cap=2), _gov(tmp_path, opened, limit=6, org_cap=2)
        await a.acquire(org_id="o")
        await b.acquire(org_id="o")
        with pytest.raises(SandboxLimitExceeded) as caught:
            await a.acquire(org_id="o")
        assert caught.value.reason is HoldReason.ORG_SHARE
        assert str(caught.value).startswith("this organisation's share of code sandboxes is in use (2/2)")
        await a.acquire(org_id="other")

    async def test_an_org_at_its_own_cap_waits_for_its_share_when_all_slots_are_taken(
        self, tmp_path: Path, opened,
    ) -> None:
        rec = _Recorder()
        a = _gov(tmp_path, opened, limit=4, org_cap=2, observers=[rec], provider=_Provider)
        b = _gov(tmp_path, opened, limit=4, org_cap=2)
        for _ in range(2):
            await a.acquire(org_id="a")
            await b.acquire(org_id="b")
        with pytest.raises(SandboxLimitExceeded) as caught:
            await a.acquire(org_id="a", wait_s=0.3)
        # A slot "b" frees would not serve "a": its wait is its own share.
        assert caught.value.reason is HoldReason.ORG_SHARE
        assert a.limit_provider.waits  # type: ignore[attr-defined]
        assert set(a.limit_provider.waits) == {HoldReason.ORG_SHARE}  # type: ignore[attr-defined]
        assert ("outcome", "refused", HoldReason.ORG_SHARE) in rec.events
        assert ("wait_reason", HoldReason.SLOTS_FULL) not in rec.events

    async def test_an_org_cap_equal_to_the_limit_is_still_slots_full(self, tmp_path: Path, opened) -> None:
        a = _gov(tmp_path, opened, limit=2, org_cap=2)
        await a.acquire(org_id="a")
        await a.acquire(org_id="a")
        with pytest.raises(SandboxLimitExceeded) as caught:
            await a.acquire(org_id="a")
        assert caught.value.reason is HoldReason.SLOTS_FULL

    async def test_a_waiter_sees_another_workers_release_within_the_poll(
        self, tmp_path: Path, opened,
    ) -> None:
        a, b = _gov(tmp_path, opened, limit=1), _gov(tmp_path, opened, limit=1)
        lease = await b.acquire()
        waiter = asyncio.create_task(a.acquire(wait_s=5))
        await asyncio.sleep(0.1)
        assert not waiter.done()
        released = time.monotonic()
        await b.release(lease)
        await asyncio.wait_for(waiter, 1)
        assert time.monotonic() - released < 0.05 + 0.04

    async def test_a_local_release_wakes_a_local_waiter_without_the_poll(
        self, tmp_path: Path, opened,
    ) -> None:
        gov = _gov(tmp_path, opened, limit=1, poll_s=30)
        lease = await gov.acquire()
        waiter = asyncio.create_task(gov.acquire(wait_s=10))
        await asyncio.sleep(0.05)
        await gov.release(lease)
        await asyncio.wait_for(waiter, 0.5)

    async def test_capacity_changed_wakes_waiters_when_the_limit_rises(
        self, tmp_path: Path, opened,
    ) -> None:
        gov = _gov(tmp_path, opened, limit=4, provider=_Provider, poll_s=30)
        gov.limit_provider.limit = 1
        await gov.acquire()
        waiter = asyncio.create_task(gov.acquire(wait_s=10))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        gov.limit_provider.limit = None
        threading.Thread(target=gov.capacity_changed).start()
        await asyncio.wait_for(waiter, 0.5)

    async def test_a_lowered_limit_keeps_held_slots_and_holds_new_ones(
        self, tmp_path: Path, opened,
    ) -> None:
        gov = _gov(tmp_path, opened, limit=4, provider=_Provider)
        held = [await gov.acquire() for _ in range(3)]
        gov.limit_provider.limit = 2
        with pytest.raises(SandboxLimitExceeded) as caught:
            await gov.acquire()
        assert caught.value.reason is HoldReason.LIVE_CPU
        assert gov.snapshot()["total"] == 3
        for lease in held:
            await gov.release(lease)


class TestAcquireLoop:
    async def test_on_wait_is_not_called_for_a_busy_mutex(self, tmp_path: Path, opened) -> None:
        gov = _gov(tmp_path, opened)
        answers = [SlotDenied("busy")] * 3
        real = gov._slots.try_acquire_tagged

        def flaky(limit: int, *, tag: bytes, tag_limit: int | None) -> object:
            return answers.pop(0) if answers else real(limit, tag=tag, tag_limit=tag_limit)

        gov._slots.try_acquire_tagged = flaky  # type: ignore[method-assign]
        told: list[bool] = []

        async def on_wait() -> None:
            told.append(True)

        lease = await gov.acquire(wait_s=1, on_wait=on_wait)
        assert told == [] and lease is not None

    async def test_a_mutex_busy_past_the_retries_is_a_wait(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, observers=[rec])
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            with pytest.raises(SandboxLimitExceeded) as caught:
                await gov.acquire(wait_s=0.05)
        finally:
            os.close(mutex)
        assert caught.value.reason is HoldReason.SLOTS_FULL
        # Nothing was known about the slots: not reported as a wait at the limit.
        assert rec.events == [("outcome", "refused", None)]

    async def test_a_busy_mutex_is_a_plain_wait_with_the_real_count(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, observers=[rec], provider=_Provider)
        other = _gov(tmp_path, opened)
        await other.acquire()

        def busy(limit: int, *, tag: bytes, tag_limit: int | None) -> object:
            return SlotDenied("busy")

        gov._slots.try_acquire_tagged = busy  # type: ignore[method-assign]
        with pytest.raises(SandboxLimitExceeded) as caught:
            await gov.acquire(wait_s=0.15)
        assert gov.limit_provider.waits == []  # type: ignore[attr-defined]
        assert caught.value.reason is HoldReason.SLOTS_FULL
        assert "all 2 code sandboxes on this server are busy (1/2)" in str(caught.value)
        assert rec.events == [("outcome", "refused", None)]

    async def test_a_mutex_busy_for_the_whole_wait_gives_no_count(self, tmp_path: Path, opened) -> None:
        gov = _gov(tmp_path, opened, provider=_Provider)
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            with pytest.raises(SandboxLimitExceeded) as caught:
                await gov.acquire(wait_s=0.05)
        finally:
            os.close(mutex)
        assert gov.limit_provider.waits == []  # type: ignore[attr-defined]
        assert "all 2 code sandboxes on this server are busy; retry once one is released" in str(caught.value)

    async def test_a_refusal_carries_its_reason_to_observers(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=1, observers=[rec])
        await gov.acquire()
        with pytest.raises(SandboxLimitExceeded) as caught:
            await gov.acquire(wait_s=0.05)
        assert caught.value.reason is HoldReason.SLOTS_FULL
        assert "the only code sandbox on this server is busy (1/1); retry once it is released" in str(caught.value)
        assert rec.events == [
            ("outcome", "granted", None),
            ("wait_start", HoldReason.SLOTS_FULL),
            ("outcome", "refused", HoldReason.SLOTS_FULL),
        ]

    async def test_a_cancel_while_waiting_holds_no_slot(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=1, observers=[rec])
        await gov.acquire()
        waiter = asyncio.create_task(gov.acquire(wait_s=10))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert gov.snapshot()["total"] == 1
        assert rec.outcomes()[-1] == ("outcome", "cancelled", HoldReason.SLOTS_FULL)
        assert len(rec.outcomes()) == 2

    async def test_a_cancel_in_on_wait_is_one_cancelled_outcome(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=1, observers=[rec])
        await gov.acquire()

        async def on_wait() -> None:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await gov.acquire(wait_s=10, on_wait=on_wait)
        assert rec.outcomes()[1:] == [("outcome", "cancelled", HoldReason.SLOTS_FULL)]

    async def test_a_floor_hold_gives_the_slot_back_and_waits(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=4, observers=[rec], provider=_Provider)
        provider = gov.limit_provider
        provider.floors = [HoldReason.HOST_MEMORY_FLOOR, HoldReason.HOST_MEMORY_FLOOR]
        lease = await gov.acquire(wait_s=5)
        assert provider.admit_calls == 3
        assert provider.waits == [HoldReason.HOST_MEMORY_FLOOR] * 2
        assert provider.ended == [True]
        assert gov.snapshot()["total"] == 1
        assert rec.events[0] == ("wait_start", HoldReason.HOST_MEMORY_FLOOR)
        assert rec.outcomes() == [("outcome", "granted", HoldReason.HOST_MEMORY_FLOOR)]
        await gov.release(lease)

    async def test_a_floor_refusal_has_the_memory_message(self, tmp_path: Path, opened) -> None:
        gov = _gov(tmp_path, opened, limit=4, provider=_Provider)
        gov.limit_provider.floors = [HoldReason.HOST_MEMORY_FLOOR]
        with pytest.raises(SandboxLimitExceeded) as caught:
            await gov.acquire()
        assert caught.value.reason is HoldReason.HOST_MEMORY_FLOOR
        assert "short of memory" in str(caught.value)
        assert gov.snapshot()["total"] == 0

    async def test_admission_is_not_asked_when_the_slot_was_denied(self, tmp_path: Path, opened) -> None:
        gov = _gov(tmp_path, opened, limit=1, provider=_Provider)
        await gov.acquire()
        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire(wait_s=0.1)
        assert gov.limit_provider.admit_calls == 1

    async def test_a_cancel_during_admission_releases_the_slot(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, observers=[rec], provider=_Provider)
        gov.limit_provider.admit_gate = asyncio.Event()
        task = asyncio.create_task(gov.acquire())
        await asyncio.sleep(0.02)
        assert gov.snapshot()["total"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gov.snapshot()["total"] == 0
        assert rec.outcomes() == [("outcome", "cancelled", None)]

    async def test_an_admission_error_is_reported_cancelled_and_raised(
        self, tmp_path: Path, opened,
    ) -> None:
        rec = _Recorder()

        class Broken(_Provider):
            async def admit_and_charge(self, *, held: int) -> HoldReason | None:
                raise RuntimeError("boom")

        gov = _gov(tmp_path, opened, observers=[rec], provider=Broken)
        with pytest.raises(RuntimeError):
            await gov.acquire()
        assert gov.snapshot()["total"] == 0
        assert rec.outcomes() == [("outcome", "cancelled", None)]

    async def test_a_grant_without_waiting_is_one_outcome_only(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, observers=[rec])
        await gov.acquire()
        assert rec.events == [("outcome", "granted", None)]

    async def test_a_grant_after_waiting_reports_the_reason(self, tmp_path: Path, opened) -> None:
        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=1, observers=[rec])
        lease = await gov.acquire()
        waiter = asyncio.create_task(gov.acquire(wait_s=5))
        await asyncio.sleep(0.05)
        await gov.release(lease)
        await waiter
        assert rec.outcomes()[-1] == ("outcome", "granted", HoldReason.SLOTS_FULL)

    async def test_an_observer_that_raises_does_not_affect_the_run(
        self, tmp_path: Path, opened, caplog: pytest.LogCaptureFixture,
    ) -> None:
        class Broken:
            def wait_start(self, reason: HoldReason) -> None:
                raise RuntimeError("x")

            def wait_reason(self, reason: HoldReason) -> None:
                raise RuntimeError("x")

            def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
                raise RuntimeError("x")

        rec = _Recorder()
        gov = _gov(tmp_path, opened, limit=2, observers=[Broken(), rec])
        with caplog.at_level(logging.WARNING):
            await gov.acquire()
            await gov.acquire()
            with pytest.raises(SandboxLimitExceeded):
                await gov.acquire()
        assert len(rec.outcomes()) == 3
        failed = [r for r in caplog.records if "observer" in r.getMessage()]
        assert len(failed) == 2  # outcome and wait_start, once each

    async def test_the_loop_is_never_blocked_by_a_busy_mutex(
        self, tmp_path: Path, opened, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gov = _gov(tmp_path, opened)
        loop_thread = threading.get_ident()
        for name in ("held_count", "held_by_tag"):
            real = getattr(SharedSlots, name)

            def guard(self: SharedSlots, budget: float = 0.0, *, _real: object = real) -> object:
                assert budget == 0.0 or threading.get_ident() != loop_thread
                return _real(self, budget)

            monkeypatch.setattr(SharedSlots, name, guard)
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        fcntl.flock(mutex, fcntl.LOCK_EX)
        gaps: list[float] = []

        async def ticker() -> None:
            last = time.monotonic()
            for _ in range(40):
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        try:
            tick = asyncio.create_task(ticker())
            with pytest.raises(SandboxLimitExceeded):
                await gov.acquire(wait_s=0.4)
            gov.snapshot()
            await tick
        finally:
            os.close(mutex)
        assert max(gaps) < 0.05

    async def test_snapshot_does_not_block_on_a_busy_mutex(self, tmp_path: Path, opened) -> None:
        gov = _gov(tmp_path, opened)
        await gov.acquire(org_id="o")
        assert gov.snapshot()["stale"] is False
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            started = time.monotonic()
            snap = gov.snapshot()
            assert time.monotonic() - started < 0.01
            assert gov.held_total() is None
        finally:
            os.close(mutex)
        assert snap["stale"] is True and snap["total"] == 1
        assert gov.held_total() == 1
        assert set(snap) == {
            "total", "by_org", "stale", "limit", "ceiling", "floor", "mode", "sized",
            "slot_files", "shared", "waiting_here", "live",
        }
        assert snap["live"] is None

    def test_unusable_slots_fall_back_to_per_worker_counting(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        not_a_directory = tmp_path / "file"
        not_a_directory.write_text("")
        with caplog.at_level(logging.WARNING):
            slots = _open_slots(not_a_directory, 4)
        assert isinstance(slots, LocalTaggedSlots)
        assert caplog.text.count("counted per worker") == 1


class TestHoldReason:
    def _view(self, limit: int, ceiling: int, cause=None) -> LiveView:
        return LiveView(
            limit=limit, ceiling=ceiling, floor=2, rule="x", shrink_cause=cause, target=None,
            mode="off", source=None, sampled_age_s=None, mem_total_bytes=None,
            mem_available_bytes=None,
        )

    @pytest.mark.parametrize(
        ("limit", "denied", "floor", "cause", "expected"),
        [
            (6, "tag_full", HoldReason.HOST_MEMORY_FLOOR, None, HoldReason.HOST_MEMORY_FLOOR),
            (6, "tag_full", None, None, HoldReason.ORG_SHARE),
            (6, "full", None, None, HoldReason.SLOTS_FULL),
            (3, "full", None, "cpu", HoldReason.LIVE_CPU),
            (3, "full", None, "memory", HoldReason.LIVE_MEMORY),
            (3, "full", None, None, HoldReason.LIVE_MEMORY),
        ],
    )
    def test_table(self, limit, denied, floor, cause, expected) -> None:
        assert hold_reason(self._view(limit, 6, cause), denied=denied, floor=floor) is expected


class TestMessage:
    @pytest.mark.parametrize("source", ["host", "sidecar"])
    @pytest.mark.parametrize("reason", list(HoldReason))
    def test_every_reason_has_text_and_no_org_id(self, reason: HoldReason, source: str) -> None:
        text = message(reason, held=3, limit=4, tag_held=2, org_limit=2, memory_source=source)
        assert text and "org-secret" not in text
        assert ("2/2" if reason is HoldReason.ORG_SHARE else "3/4") in text

    def test_texts(self) -> None:
        assert message(HoldReason.SLOTS_FULL, held=4, limit=4) == (
            "all 4 code sandboxes on this server are busy (4/4); retry once one is released"
        )
        assert message(HoldReason.ORG_SHARE, held=4, limit=6, tag_held=3, org_limit=3) == (
            "this organisation's share of code sandboxes is in use (3/3); "
            "retry once one of its runs ends"
        )
        assert message(HoldReason.LIVE_MEMORY, held=2, limit=3).startswith(
            "the server is short of memory right now (2/3 code sandboxes in use)"
        )
        assert message(HoldReason.HOST_MEMORY_FLOOR, held=2, limit=3, memory_source="sidecar").startswith(
            "the code runner is short of memory"
        )
        assert message(HoldReason.LIVE_CPU, held=2, limit=3) == (
            "the server's CPU is busy, so fewer code sandboxes run at once (2/3); retry in a minute"
        )
        assert message(HoldReason.LIVE_CPU, held=2, limit=3, memory_source="sidecar").startswith(
            "the code runner's CPU is busy"
        )
