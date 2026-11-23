"""Indexing slows down while code sandbox runs wait for memory.

A real ``LiveLimit`` reading a fake host writes the yield signal through
``IndexingYield``; a real ``ResourceGovernor`` with real gates reads it from
the same state directory. Only the probe, the host reading and the clocks are
fake. The multi-process cases use real processes: a writer that is killed,
and two indexing workers acking the same signal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.host_reading import LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import IndexingYield
from app.services.resource_governor import policy
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.feedback import DownstreamFeedback
from app.services.resource_governor.models import Pool
from app.services.resource_governor.policy import YIELD_GROW_CONFIRM_SAMPLES
from app.services.resource_governor.yield_signal import YieldSignal, read_yield_state
from tests.integration.resource_governor_helpers import (
    ManualClock,
    ScriptedProbe,
    make_snapshot,
)
from tests.unit.agent_loop_lib.sandbox.live_fakes import FakeReader, capacity, sample

if TYPE_CHECKING:
    from multiprocessing.queues import Queue

pytest.importorskip("fcntl")
pytestmark = pytest.mark.timeout(90)

INTERVAL = 15.0
INDEX_POOLS = (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)


async def _hold_gate(gate, timeout: float | None = 5.0) -> None:
    async with gate.slot(timeout=timeout):
        await asyncio.sleep(3600)


def _governor(signal_: YieldSignal | None, clock: ManualClock, name: str = "test.yield") -> ResourceGovernor:
    return ResourceGovernor(
        logger=logging.getLogger(name),
        probe=ScriptedProbe([make_snapshot(mem_pressure=0.1)]),
        feedback=DownstreamFeedback(),
        sample_interval=INTERVAL,
        clock=clock,
        yield_signal=signal_,
    )


def _floor(governor: ResourceGovernor, pool: Pool) -> int:
    return policy.pressure_floor(pool, governor.ceiling(pool))


class _Writer:
    """A query worker whose run waits for host memory."""

    def __init__(self, directory: Path, clock: ManualClock) -> None:
        mode = LiveMode(mode="on:host", reader=FakeReader(sample(1.4)), profile="host")
        settings = LiveSettings()
        self.live = LiveLimit(
            capacity(), None, settings=settings, resolver=lambda: mode, directory=directory, clock=clock,
        )
        self.live.apply_mode(mode)
        self.yield_ = IndexingYield(self.live, YieldSignal(directory, clock=clock), settings, clock=clock)
        self.live.on_memory_wait = self.yield_.on_memory_wait
        self.waited = 0.0

    def wait(self, seconds: float) -> None:
        self.waited += seconds
        self.live.waiting(HoldReason.HOST_MEMORY_FLOOR, self.waited)


@pytest.mark.asyncio
class TestBackOffAndRecovery:
    async def test_index_pools_shrink_to_the_floor_hold_then_creep_back(self, tmp_path: Path) -> None:
        clock = ManualClock()
        clock.now = 1000.0
        state = tmp_path / "state"
        writer = _Writer(state, clock)
        governor = _governor(YieldSignal(state, clock=clock), clock)
        gates = {pool: governor.gate(pool) for pool in INDEX_POOLS}
        start = {pool: gate.limit for pool, gate in gates.items()}
        holders = [
            asyncio.create_task(_hold_gate(gate, timeout=None))
            for pool, gate in gates.items() for _ in range(start[pool] + 4)
        ]
        await asyncio.sleep(0)
        try:
            assert all(gates[pool].in_use == start[pool] for pool in INDEX_POOLS)

            # Runs wait for host memory: indexing shrinks, sample by sample, to its floor.
            writer.wait(3.0)
            assert read_yield_state(state, now=clock.now).active
            previous = dict(start)
            for _ in range(12):
                clock.now += INTERVAL
                writer.wait(INTERVAL)
                await governor._sample_once()
                for pool in INDEX_POOLS:
                    assert gates[pool].limit <= previous[pool]
                    previous[pool] = gates[pool].limit
            for pool in INDEX_POOLS:
                assert gates[pool].limit == _floor(governor, pool)
                # Permits already out are never revoked; nothing new is admitted.
                assert gates[pool].in_use == start[pool]
            backed_off = read_yield_state(state, now=clock.now)
            assert backed_off.ackers == 1 and backed_off.index_at_floor

            # The run is granted: no more refreshes, and the signal lapses by its TTL.
            clock.now += INTERVAL
            await governor._sample_once()
            assert governor.stats()["yield_to_sandboxes"]["active"]
            clock.now += INTERVAL
            await governor._sample_once()
            assert not governor.stats()["yield_to_sandboxes"]["active"]
            floors = {pool: gates[pool].limit for pool in INDEX_POOLS}
            for _ in range(YIELD_GROW_CONFIRM_SAMPLES - 1):
                clock.now += INTERVAL
                await governor._sample_once()
                assert {pool: gates[pool].limit for pool in INDEX_POOLS} == floors
            for _ in range(3):
                before = {pool: gates[pool].limit for pool in INDEX_POOLS}
                clock.now += INTERVAL
                await governor._sample_once()
                for pool in INDEX_POOLS:
                    assert gates[pool].limit - before[pool] == 1, pool
        finally:
            for task in holders:
                task.cancel()
            for task in holders:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            governor.close()

    async def test_a_garbage_file_leaves_indexing_alone(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        clock_a, clock_b = ManualClock(), ManualClock()
        with_signal = _governor(YieldSignal(state, clock=clock_a), clock_a, "test.yield.garbage")
        (state / "yield").write_bytes(os.urandom(256))
        without = _governor(None, clock_b, "test.yield.none")
        try:
            for _ in range(6):
                clock_a.now += INTERVAL
                clock_b.now += INTERVAL
                await with_signal._sample_once()
                await without._sample_once()
            assert {p: with_signal.limit(p) for p in Pool} == {p: without.limit(p) for p in Pool}
        finally:
            with_signal.close()
            without.close()


def _refresher(directory: str, ready: Any) -> None:  # noqa: ANN401
    sig = YieldSignal(Path(directory))
    sig.refresh(reason="host_memory_floor", ttl_s=60, budget=1.0)
    ready.set()
    while True:
        time.sleep(0.2)


@pytest.mark.asyncio
async def test_a_killed_writer_releases_indexing_within_ten_seconds(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    proc = ctx.Process(target=_refresher, args=(str(state), ready))
    proc.start()
    try:
        assert ready.wait(30)
        skew = [0.0]
        reader = YieldSignal(state, clock=lambda: time.monotonic() + skew[0])
        clock = ManualClock()
        governor = _governor(reader, clock, "test.yield.killed")
        os.kill(proc.pid, signal.SIGKILL)
        proc.join(10)
        # Within ten seconds of its last write a dead writer still counts.
        assert reader.read_state().active
        clock.now += INTERVAL
        await governor._sample_once()
        shrunk = governor.limit(Pool.INDEX_HEAVY)
        skew[0] = 11.0
        assert not reader.read_state().active
        for _ in range(YIELD_GROW_CONFIRM_SAMPLES):
            clock.now += INTERVAL
            await governor._sample_once()
            assert governor.limit(Pool.INDEX_HEAVY) == shrunk
        assert not governor.stats()["yield_to_sandboxes"]["active"]
        governor.close()
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)


def _indexing_worker(directory: str, commands: Queue, out: Queue) -> None:
    """One indexing worker: samples on command, reporting at floor or not."""
    from app.services.resource_governor import controller

    real_floor = controller.pressure_floor
    clock = ManualClock()
    governor = _governor(YieldSignal(Path(directory)), clock, f"test.yield.worker.{os.getpid()}")
    while True:
        command = commands.get(timeout=60)
        if command == "exit":
            return
        samples, above_floor = command
        # Hold this worker's report above its floor, as a pool that cannot shrink further would.
        controller.pressure_floor = (lambda *a, **k: -1) if above_floor else real_floor
        for _ in range(samples):
            clock.now += INTERVAL
            asyncio.run(governor._sample_once())
        out.put(os.getpid())


def test_two_indexing_workers_must_both_be_at_their_floor(tmp_path: Path) -> None:
    state = tmp_path / "state"
    writer = YieldSignal(state)
    writer.refresh(reason="host_memory_floor", ttl_s=90)
    ctx = multiprocessing.get_context("spawn")
    out: Queue = ctx.Queue()
    queues = [ctx.Queue(), ctx.Queue()]
    procs = [ctx.Process(target=_indexing_worker, args=(str(state), q, out)) for q in queues]
    for proc in procs:
        proc.start()
    try:
        queues[0].put((30, False))
        queues[1].put((30, True))
        assert {out.get(timeout=60), out.get(timeout=60)} == {p.pid for p in procs}
        mixed = read_yield_state(state)
        assert mixed.active and mixed.ackers == 2 and not mixed.index_at_floor

        queues[1].put((1, False))
        assert out.get(timeout=60) == procs[1].pid
        both = read_yield_state(state)
        assert both.ackers == 2 and both.index_at_floor
    finally:
        for q in queues:
            q.put("exit")
        for proc in procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()
        writer.close()
