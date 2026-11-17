"""Tests for LiveLimit's sampler, view and wait stamps on a shared record."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import HoldReason, hold_reason
from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
from app.agent_loop_lib.sandbox.host_reading import LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.services.resource_governor.shared_slots import LocalTaggedSlots

from .live_fakes import FakeClock, FakeGovernor, FakeReader, capacity, sample

if TYPE_CHECKING:
    from pathlib import Path

    from app.agent_loop_lib.sandbox.capacity import SandboxCapacity


def _live(
    tmp_path: Path, clock: FakeClock, reader: FakeReader, governor: object | None = None, *,
    cap: SandboxCapacity | None = None, on: bool = True,
) -> LiveLimit:
    mode = LiveMode(mode="on:host", reader=reader, profile="host", note="test")
    live = LiveLimit(
        cap or capacity(), governor, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
        directory=tmp_path / "state", clock=clock,
    )
    if on:
        live.apply_mode(mode)
    return live


class TestSharedSteps:
    def test_one_step_per_window_across_workers(self, tmp_path: Path) -> None:
        clock = FakeClock()
        ra, rb = FakeReader(), FakeReader()
        a, b = _live(tmp_path, clock, ra), _live(tmp_path, clock, rb)
        assert a.sample_once() is not None
        assert b.sample_once() is None
        assert rb.reads == 0
        clock.now += 12.0
        assert a.sample_once() is None and b.sample_once() is None
        clock.now += 1.0
        assert b.sample_once() is not None
        assert a.sample_once() is None
        assert (ra.reads, rb.reads) == (1, 1)

    def test_cpu_use_is_measured_since_the_last_step_whichever_worker_took_it(self, tmp_path: Path) -> None:
        # Each worker's own previous reading is several steps old when the
        # workers take turns; the record keeps the counters of the last step.
        clock = FakeClock()
        ra = FakeReader(sample(7.0, cpu_util=0.1, cpu_counters=(100, 1000)))
        rb = FakeReader(sample(7.0, cpu_util=0.1, cpu_counters=(1090, 2000)))
        a, b = _live(tmp_path, clock, ra), _live(tmp_path, clock, rb)
        first = a.sample_once()
        assert first is not None and first.cpu_high_streak == 0
        assert (first.cpu_busy, first.cpu_total) == (100, 1000)
        clock.now += 13
        second = b.sample_once()
        assert second is not None and second.cpu_high_streak == 1
        clock.now += 13
        ra.sample = sample(7.0, cpu_util=0.1, cpu_counters=(1190, 3000))
        third = a.sample_once()
        assert third is not None and third.cpu_high_streak == 0

    def test_the_other_worker_sees_the_limit_within_a_second(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(sample(7.0))
        a, b = _live(tmp_path, clock, reader), _live(tmp_path, clock, FakeReader())
        a.sample_once()
        assert b.view().limit == 6
        reader.sample = sample(4.0)
        clock.now += 13
        assert b.view().limit == 6
        assert a.sample_once().limit == 4  # type: ignore[union-attr]
        assert b.view().limit == 6
        clock.now += 1.0
        assert b.view().limit == 4

    def test_view_before_any_step_is_the_ceiling(self, tmp_path: Path) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader())
        view = live.view()
        assert (view.limit, view.ceiling, view.rule, view.mode, view.source) == (6, 6, "start", "on:host", "host")

    def test_off_view_is_the_ceiling(self, tmp_path: Path) -> None:
        live = _live(tmp_path, FakeClock(), FakeReader(), on=False)
        view = live.view()
        assert (view.limit, view.rule, view.mode) == (6, "off", "off:starting")

    @pytest.mark.parametrize(("cpu", "reason"), [(False, HoldReason.LIVE_MEMORY), (True, HoldReason.LIVE_CPU)])
    def test_shrink_cause_picks_the_reason(self, tmp_path: Path, cpu: bool, reason: HoldReason) -> None:
        clock = FakeClock()
        reader = FakeReader(sample(7.0))
        live = _live(tmp_path, clock, reader)
        live.sample_once()
        reader.sample = sample(7.0, cpu_util=0.99) if cpu else sample(4.0)
        for _ in range(2):
            clock.now += 13
            live.sample_once()
        clock.now += 1
        view = live.view()
        assert view.limit < view.ceiling
        assert hold_reason(view, denied="full", floor=None) is reason

    def test_a_rise_wakes_the_governor_once(self, tmp_path: Path) -> None:
        clock = FakeClock()
        governor = FakeGovernor()
        reader = FakeReader(sample(4.0))
        live = _live(tmp_path, clock, reader, governor)
        live.sample_once()
        assert live.view().limit == 3
        other = _live(tmp_path, clock, FakeReader(sample(7.0)))
        with other._ledger.transaction() as txn:
            assert txn is not None
            rec = txn.read()
            assert rec is not None
            txn.write(replace(rec, limit=5))
        clock.now += 1.0
        assert live.view().limit == 5
        assert governor.changed == 1
        clock.now += 1.0
        live.view()
        assert governor.changed == 1

    def test_a_stale_record_starts_afresh(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(sample(4.0))
        live = _live(tmp_path, clock, reader)
        live.sample_once()
        clock.now += 20
        reader.sample = sample(2.0)
        assert live.sample_once().limit == 2  # type: ignore[union-attr]
        reader.sample = sample(7.0)
        clock.now += 121
        rec = live.sample_once()
        assert rec is not None and (rec.limit, rec.rule) == (6, "start")

    def test_a_ceiling_change_starts_afresh(self, tmp_path: Path) -> None:
        clock = FakeClock()
        cap = capacity()
        live = _live(tmp_path, clock, FakeReader(sample(7.0)), cap=cap)
        live.sample_once()
        cap.override(max_total=4, max_per_org=None)
        clock.now += 13
        rec = live.sample_once()
        assert rec is not None and (rec.limit, rec.ceiling, rec.rule) == (4, 4, "start")

    def test_unwritable_directory_keeps_a_limit_per_worker(self, tmp_path: Path) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)
        live = _live(tmp_path, FakeClock(), FakeReader())
        assert not live._ledger.shared
        assert live.sample_once() is not None


class TestWaitStamps:
    def test_stamps_are_rate_limited(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader())
        live.sample_once()
        for _ in range(5):
            live.waiting(HoldReason.SLOTS_FULL, 1.0)
        rec = live._ledger.read()
        assert rec is not None and rec.wait_events == 1 and rec.last_wait_limit_at == clock.now
        clock.now += 1.0
        live.waiting(HoldReason.LIVE_MEMORY, 2.0)
        live.waiting(HoldReason.HOST_MEMORY_FLOOR, 2.0)
        live.waiting(HoldReason.ORG_SHARE, 2.0)
        rec = live._ledger.read()
        assert rec is not None and rec.wait_events == 3
        assert rec.last_wait_limit_at == rec.last_wait_mem_at == clock.now

    def test_waiting_never_blocks_on_a_held_lock(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader())
        live.sample_once()
        holder = _live(tmp_path, clock, FakeReader())
        with holder._ledger.transaction() as txn:
            assert txn is not None
            started = time.monotonic()
            for _ in range(1000):
                live.waiting(HoldReason.SLOTS_FULL, 1.0)
            assert time.monotonic() - started < 0.05
        assert live._ledger.read().wait_events == 0  # type: ignore[union-attr]
        live.waiting(HoldReason.SLOTS_FULL, 1.0)
        assert live._ledger.read().wait_events == 1  # type: ignore[union-attr]

    def test_memory_waits_reach_the_hook(self, tmp_path: Path) -> None:
        clock = FakeClock()
        reader = FakeReader(sample(4.0))
        live = _live(tmp_path, clock, reader)
        live.sample_once()
        calls: list[HoldReason] = []
        live.on_memory_wait = lambda reason, waited: calls.append(reason)
        for reason in HoldReason:
            live.waiting(reason, 4.0)
        assert calls == [HoldReason.LIVE_MEMORY, HoldReason.HOST_MEMORY_FLOOR]


class TestSamplerTask:
    async def test_held_is_read_off_the_event_loop(self, tmp_path: Path) -> None:
        governor = FakeGovernor(held=3)
        reader = FakeReader()
        mode = LiveMode(mode="on:host", reader=reader, profile="host")
        live = LiveLimit(
            capacity(), governor, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
            directory=tmp_path / "state",
        )
        live.start()
        for _ in range(100):
            if reader.reads:
                break
            await asyncio.sleep(0.01)
        await live.stop()
        assert reader.reads == 1 and live.mode.mode == "on:host"
        assert governor.held_threads and threading.main_thread() not in governor.held_threads

    async def test_a_rise_lets_a_local_waiter_in_without_polling(self, tmp_path: Path) -> None:
        clock = FakeClock()
        cap = capacity()
        reader = FakeReader(sample(3.0))
        governor = SandboxResourceGovernor(cap, LocalTaggedSlots(cap.slot_files), limit_provider=None, poll_s=5.0)  # type: ignore[arg-type]
        live = _live(tmp_path, clock, reader, governor, cap=cap)
        governor.limit_provider = live
        live.sample_once()
        assert live.view().limit == 2
        leases = [await governor.acquire(), await governor.acquire()]
        waiter = asyncio.create_task(governor.acquire(wait_s=10.0))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        with live._ledger.transaction() as txn:
            assert txn is not None
            txn.write(replace(txn.read(), limit=3))  # type: ignore[type-var]
        clock.now += 1.0
        started = time.monotonic()
        assert live.view(fresh=True).limit == 3
        lease = await asyncio.wait_for(waiter, 1.0)
        assert time.monotonic() - started < 1.0
        for held in [*leases, lease]:
            await governor.release(held)
