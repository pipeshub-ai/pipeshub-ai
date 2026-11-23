"""Many query workers on one live record, as real processes with fake readings.

- Eight workers stepping and admitting at the same moment: exactly the runs
  that fit above the hard floor are admitted, and every admitted run's burst
  charge is still in the record (no step or admission overwrote another's).
- A worker stopped (SIGSTOP) while it holds ``live.lock``: the others admit
  within the lock budget, skip their step, and their event loop never waits;
  once it is killed (SIGKILL) the kernel drops its lock and steps resume.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Barrier, Event

    from app.agent_loop_lib.sandbox.host_reading import HostSample
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit

pytest.importorskip("fcntl")
pytestmark = [pytest.mark.integration, pytest.mark.chaos, pytest.mark.timeout(60)]

GiB = 2**30
MiB = 2**20
M = 512 * MiB


class _FileReader:
    source = "host"

    def __init__(self, path: str) -> None:
        self._path = path

    def _avail(self) -> int | None:
        try:
            return int(json.loads(Path(self._path).read_text())["avail"])
        except (OSError, ValueError, KeyError):
            return None

    def read(self) -> HostSample:
        from app.agent_loop_lib.sandbox.host_reading import HostSample

        return HostSample(mem_total=16 * GiB, mem_available=self._avail(), cpu_util=0.1, source="host")

    def memory(self) -> tuple[int | None, int | None]:
        return self._avail(), 16 * GiB


def _live(directory: str, reader_file: str, interval_s: float) -> LiveLimit:
    from app.agent_loop_lib.sandbox.capacity import (
        CapacitySettings,
        MachineSize,
        SandboxCapacity,
    )
    from app.agent_loop_lib.sandbox.host_reading import LiveMode
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings

    capacity = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
    capacity._apply(MachineSize(16 * GiB, 4.0, None, "docker_info"), None)
    mode = LiveMode(mode="on:host", reader=_FileReader(reader_file), profile="host")  # type: ignore[arg-type]
    live = LiveLimit(
        capacity, None, settings=LiveSettings(interval_s=interval_s), resolver=lambda: mode,
        directory=Path(directory),
    )
    live.apply_mode(mode)
    return live


def _step_and_admit(directory: str, reader_file: str, tries: int, barrier: Barrier, out: Queue) -> None:
    # Steps as often as the record allows (every 85 ms) between admissions.
    live = _live(directory, reader_file, interval_s=0.1)
    barrier.wait()
    admitted = steps = 0
    for _ in range(tries):
        if live.sample_once() is not None:
            steps += 1
        if asyncio.run(live.admit_and_charge(held=0)) is None:
            admitted += 1
        time.sleep(0.003)
    out.put((os.getpid(), admitted, steps))


def _hold_the_lock(directory: str, reader_file: str, ready: Event) -> None:
    live = _live(directory, reader_file, interval_s=0.1)
    with live._ledger.transaction(5.0) as txn:
        assert txn is not None
        ready.set()
        time.sleep(600)


def _write(path: Path, avail: int) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"avail": avail}))
    tmp.replace(path)


def test_eight_workers_stepping_and_admitting_lose_no_charge(tmp_path: Path) -> None:
    from app.agent_loop_lib.sandbox.live_record import LiveLedger

    reader_file = tmp_path / "host.json"
    # The 1 GiB hard floor plus 4.5 runs: four fit, the fifth would not.
    _write(reader_file, GiB + int(4.5 * M))
    ctx = multiprocessing.get_context("spawn")
    workers, tries = 8, 12
    barrier, out = ctx.Barrier(workers), ctx.Queue()
    procs = [
        ctx.Process(target=_step_and_admit, args=(str(tmp_path / "state"), str(reader_file), tries, barrier, out))
        for _ in range(workers)
    ]
    started = time.monotonic()
    for proc in procs:
        proc.start()
    try:
        results = [out.get(timeout=40) for _ in range(workers)]
    finally:
        for proc in procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()
    # Every charge lasts 5 s from its admission; the check below needs them all live.
    record = LiveLedger(tmp_path / "state").read()
    elapsed = time.monotonic() - started
    assert sum(admitted for _, admitted, _ in results) == 4, results
    assert sum(steps for _, _, steps in results) >= 1
    assert record is not None and record.initialised
    if elapsed < 4.5:
        assert sum(size for _, size in record.charges) == 4 * M


def test_a_stopped_lock_holder_never_blocks_the_others(tmp_path: Path) -> None:
    from app.agent_loop_lib.sandbox.capacity import HoldReason

    reader_file = tmp_path / "host.json"
    _write(reader_file, 4 * GiB)
    state = str(tmp_path / "state")
    live = _live(state, str(reader_file), interval_s=0.1)
    first = live.sample_once()
    assert first is not None
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(target=_hold_the_lock, args=(state, str(reader_file), ready))
    holder.start()
    try:
        assert ready.wait(30)
        os.kill(holder.pid, signal.SIGSTOP)
        time.sleep(0.15)

        async def on_loop() -> tuple[float, float, Any]:
            lags: list[float] = []

            async def ticker() -> None:
                last = time.perf_counter()
                for _ in range(200):
                    await asyncio.sleep(0.001)
                    now = time.perf_counter()
                    lags.append(now - last)
                    last = now

            tick = asyncio.create_task(ticker())
            t0 = time.perf_counter()
            reason = await live.admit_and_charge(held=0)
            admit_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            live.view(fresh=True)
            live.waiting(HoldReason.SLOTS_FULL, 1.0)
            loop_s = time.perf_counter() - t0
            await tick
            return admit_s, max(loop_s, max(lags)), reason

        admit_s, loop_s, reason = asyncio.run(on_loop())
        # Admitted (fail open) after about the 50 ms budget, loop never held.
        assert reason is None and 0.04 <= admit_s < 0.5
        assert loop_s < 0.03
        t0 = time.perf_counter()
        assert live.sample_once() is None
        assert time.perf_counter() - t0 < 0.3
        assert live.view(fresh=True).limit == first.limit

        os.kill(holder.pid, signal.SIGKILL)
        holder.join(10)
        _write(reader_file, int(1.2 * GiB))
        deadline = time.monotonic() + 5
        stepped = None
        while stepped is None and time.monotonic() < deadline:
            stepped = live.sample_once()
            time.sleep(0.02)
        assert stepped is not None and stepped.limit < first.limit
    finally:
        if holder.is_alive():
            os.kill(holder.pid, signal.SIGKILL)
            holder.join(10)
