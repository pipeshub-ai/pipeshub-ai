"""Tests for the per-run free-memory check (LiveLimit.admit_and_charge)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.agent_loop_lib.sandbox.host_reading import HostSample, LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_record import LiveRecord
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.services.resource_governor.shared_slots import LocalTaggedSlots

from .live_fakes import FakeClock, FakeReader, capacity, sample

if TYPE_CHECKING:
    from pathlib import Path

M = 512 * 2**20


def _live(tmp_path: Path, clock: FakeClock, reader: FakeReader, *, on: bool = True) -> LiveLimit:
    mode = LiveMode(mode="on:host", reader=reader, profile="host")
    live = LiveLimit(
        capacity(), None, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
        directory=tmp_path / "state", clock=clock,
    )
    if on:
        live.apply_mode(mode)
    return live


class TestAdmit:
    async def test_above_the_floor_is_admitted_and_charged(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        assert await live.admit_and_charge(held=0) is None
        rec = live._ledger.read()
        assert rec is not None and rec.charges == ((clock.now + 5.0, M),)
        assert not rec.initialised

    async def test_below_the_floor_is_held_and_not_charged(self, tmp_path: Path) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(sample(1.4)))
        assert await live.admit_and_charge(held=0) is HoldReason.HOST_MEMORY_FLOOR
        assert live._ledger.read() is None

    async def test_the_burst_window_counts_recent_runs(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(2.2)))
        assert await live.admit_and_charge(held=0) is None
        assert await live.admit_and_charge(held=1) is None
        assert await live.admit_and_charge(held=2) is HoldReason.HOST_MEMORY_FLOOR
        clock.now += 5.1
        assert await live.admit_and_charge(held=2) is None

    async def test_steady_starts_are_charged_only_within_their_own_window(self, tmp_path: Path) -> None:
        # One start a second on a flat 8 GiB reading: each charge ends 5 s
        # after its own run, so at most five runs are charged at a time and
        # none is held back.
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(8.0)))
        for held in range(30):
            assert await live.admit_and_charge(held=held) is None
            rec = live._ledger.read()
            assert rec is not None and sum(n for _, n in rec.charges) <= 5 * M
            clock.now += 1.0

    async def test_more_starts_than_slots_still_expire(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(16.0, total_gib=32)))
        for _ in range(20):
            assert await live.admit_and_charge(held=0) is None
            clock.now += 0.1
        rec = live._ledger.read()
        # Each check counts at most one run's memory per slot (the ceiling's
        # runs can be alive at once, however many started in the window), so
        # what is stored may be less than every start.
        assert rec is not None
        assert live._ceiling(1.0) * M <= sum(n for _, n in rec.charges) <= 20 * M
        assert max(until for until, _ in rec.charges) == pytest.approx(clock.now - 0.1 + 5.0)
        clock.now += 5.0
        assert await live.admit_and_charge(held=0) is None
        assert live._ledger.read().charges == ((clock.now + 5.0, M),)  # type: ignore[union-attr]

    async def test_a_charge_from_before_a_clock_restart_is_dropped(self, tmp_path: Path) -> None:
        # The record outlives a host reboot in the container's /tmp, but the
        # monotonic clock starts again near zero.
        clock = FakeClock(now=50.0)
        live = _live(tmp_path, clock, FakeReader(sample(2.2)))
        with live._ledger.transaction() as txn:
            assert txn is not None
            txn.write(LiveRecord(charges=((90_000.0, 4 * M),)))
        assert await live.admit_and_charge(held=0) is None
        assert live._ledger.read().charges == ((clock.now + 5.0, M),)  # type: ignore[union-attr]

    async def test_a_charge_from_a_worker_whose_clock_read_was_later_still_counts(self, tmp_path: Path) -> None:
        # Another worker read the clock a moment after this one, then took
        # the lock first: its charge ends just past this worker's window.
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(2.2)))
        with live._ledger.transaction() as txn:
            assert txn is not None
            txn.write(LiveRecord(charges=((clock.now + 5.01, M), (clock.now + 5.02, M))))
        assert await live.admit_and_charge(held=0) is HoldReason.HOST_MEMORY_FLOOR

    async def test_two_workers_share_the_burst(self, tmp_path: Path) -> None:
        clock = FakeClock()
        a = _live(tmp_path, clock, FakeReader(sample(2.2)))
        b = _live(tmp_path, clock, FakeReader(sample(2.2)))
        results = [await w.admit_and_charge(held=0) for w in (a, b, a, b)]
        assert results == [None, None, HoldReason.HOST_MEMORY_FLOOR, HoldReason.HOST_MEMORY_FLOOR]

    async def test_a_held_lock_admits_without_charging(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(1.4)))
        holder = _live(tmp_path, clock, FakeReader())
        with holder._ledger.transaction() as txn:
            assert txn is not None
            started = time.monotonic()
            assert await live.admit_and_charge(held=0) is None
            assert time.monotonic() - started < 0.5
        assert live._ledger.read() is None

    async def test_off_mode_never_reads_or_starts_a_thread(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        reader = FakeReader(sample(0.1))
        live = _live(tmp_path, FakeClock(), reader, on=False)

        async def no_thread(*args: object, **kwargs: object) -> None:
            raise AssertionError("no thread hop when off")

        monkeypatch.setattr(asyncio, "to_thread", no_thread)
        assert await live.admit_and_charge(held=0) is None
        assert reader.memory_reads == 0

    async def test_an_unreadable_floor_admits(self, tmp_path: Path) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(HostSample()))
        assert await live.admit_and_charge(held=0) is None

    async def test_the_reading_is_cached_briefly(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(sample(8.0))
        live = _live(tmp_path, clock, reader)
        reader.memory_reads = 0
        for _ in range(3):
            await live.admit_and_charge(held=0)
        assert reader.memory_reads == 1
        clock.now += 0.3
        await live.admit_and_charge(held=0)
        assert reader.memory_reads == 2

    async def test_the_loop_keeps_running_while_the_lock_is_tried(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(4.0)))
        holder = _live(tmp_path, clock, FakeReader())
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.001)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        with holder._ledger.transaction() as txn:
            assert txn is not None
            await live.admit_and_charge(held=0)
        stop.set()
        await task
        assert max(gaps) < 0.03


class TestGovernorWithFloor:
    async def test_floor_refusal_uses_the_memory_wording(self, tmp_path: Path) -> None:
        clock = FakeClock()
        cap = capacity()
        reader = FakeReader(sample(1.2))
        governor = SandboxResourceGovernor(cap, LocalTaggedSlots(cap.slot_files), limit_provider=None)  # type: ignore[arg-type]
        mode = LiveMode(mode="on:host", reader=reader, profile="host")
        live = LiveLimit(cap, governor, settings=LiveSettings(), resolver=lambda: mode, directory=tmp_path / "s", clock=clock)
        live.apply_mode(mode)
        governor.limit_provider = live
        with pytest.raises(SandboxLimitExceeded) as caught:
            await governor.acquire(wait_s=0.0)
        assert caught.value.reason is HoldReason.HOST_MEMORY_FLOOR
        assert "the server is short of memory right now (0/6" in str(caught.value)
        assert governor.held_total() == 0
        reader.sample = sample(4.0)
        clock.now += 1.0
        lease = await governor.acquire(wait_s=0.0)
        await governor.release(lease)
