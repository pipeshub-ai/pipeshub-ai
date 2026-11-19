"""The shared code sandbox cap across real worker processes.

No Docker and no services: each worker is a ``spawn`` process with its own
event loop and governor on one slot directory, as uvicorn's workers are.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import multiprocessing
import os
import random
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Event

    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor

LIMIT = 6
ORG_CAP = 4
SLOT_FILES = 16
_CTX = multiprocessing.get_context("spawn")


class _Outcomes:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def wait_start(self, reason: object) -> None:
        pass

    def wait_reason(self, reason: object) -> None:
        pass

    def outcome(self, outcome: str, waited_s: float, reason: object) -> None:
        self.outcomes.append(outcome)


def _governor(directory: str, observers: tuple[Any, ...] = ()) -> SandboxResourceGovernor:
    from app.agent_loop_lib.sandbox.capacity import FixedLimit, SandboxCapacity
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
    from app.services.resource_governor.shared_slots import SharedSlots

    capacity = SandboxCapacity.fixed(LIMIT, ORG_CAP, slot_files=SLOT_FILES)
    return SandboxResourceGovernor(
        capacity, SharedSlots(Path(directory), SLOT_FILES),
        limit_provider=FixedLimit(capacity), observers=observers, poll_s=0.05,
    )


def _chaos_worker(directory: str, out: str, runs: int, seed: int, ready: Queue[int], go: Event) -> None:
    """Runs that each wait (or not), hold a slot briefly, and are sometimes cancelled."""
    from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded

    recorder = _Outcomes()
    governor = _governor(directory, (recorder,))
    rng = random.Random(seed)
    intervals: list[tuple[float, float, str]] = []
    waits: list[float] = []
    ends: dict[str, int] = {"granted": 0, "refused": 0, "cancelled": 0}

    async def one(org: str) -> None:
        asked = time.monotonic()
        try:
            lease = await governor.acquire(org_id=org, wait_s=rng.choice([0.0, 0.2, 20.0]))
        except SandboxLimitExceeded:
            ends["refused"] += 1
            return
        start = time.monotonic()
        waits.append(start - asked)
        try:
            await asyncio.sleep(rng.uniform(0.01, 0.06))
        finally:
            intervals.append((start, time.monotonic(), org))
            await governor.release(lease)
        ends["granted"] += 1

    async def main() -> None:
        tasks = [asyncio.ensure_future(one(rng.choice(["a", "b", "c"]))) for _ in range(runs)]
        for _ in range(runs // 4):
            await asyncio.sleep(rng.uniform(0, 0.02))
            rng.choice(tasks).cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ends["cancelled"] = sum(isinstance(r, asyncio.CancelledError) for r in results)

    ready.put(os.getpid())
    go.wait(60)
    asyncio.run(main())
    Path(out).write_text(json.dumps({
        "intervals": intervals, "outcomes": recorder.outcomes, "ends": ends, "waits": waits,
        # Held by this worker: the others may still be running.
        "held_after": len(governor._slots._held_here), "waiting_here": governor._waiting_here,
    }))


def _load_worker(directory: str, out: str, runs: int, ready: Queue[int], go: Event) -> None:
    """*runs* local waiters each taking one 50 ms slot; records each one's wait."""
    governor = _governor(directory)
    waits: list[float] = []

    async def one() -> None:
        asked = time.monotonic()
        lease = await governor.acquire(wait_s=60)
        waits.append(time.monotonic() - asked)
        await asyncio.sleep(0.05)
        await governor.release(lease)

    async def main() -> None:
        await asyncio.gather(*(one() for _ in range(runs)))

    ready.put(os.getpid())
    go.wait(60)
    asyncio.run(main())
    Path(out).write_text(json.dumps({"waits": waits}))


def _mutex_holder(directory: str, ready: Queue[int]) -> None:
    """Holds the slot mutex forever, as a worker stopped mid-count would."""
    from app.services.resource_governor.shared_slots import SharedSlots

    slots = SharedSlots(Path(directory), SLOT_FILES)
    fcntl.flock(slots._mutex_fd, fcntl.LOCK_EX)
    ready.put(os.getpid())
    time.sleep(120)


def _holder_with_child(directory: str, ready: Queue[int]) -> None:
    """Holds slots for two orgs, and has started a subprocess that outlives it."""
    governor = _governor(directory)

    async def main() -> None:
        await governor.acquire(org_id="a")
        await governor.acquire(org_id="a")
        await governor.acquire(org_id="b")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ready.put(child.pid)
        await asyncio.sleep(120)

    asyncio.run(main())


def _start(target: Callable[..., None], args: tuple[Any, ...], ready: Queue[int]) -> tuple[Any, int]:
    proc = _CTX.Process(target=target, args=(*args, ready))
    proc.start()
    return proc, ready.get(timeout=60)


def _most_at_once(intervals: list[tuple[float, float, str]]) -> int:
    events = sorted([(s, 1) for s, _, _ in intervals] + [(e, -1) for _, e, _ in intervals])
    now = peak = 0
    for _, step in events:
        now += step
        peak = max(peak, now)
    return peak


def _run_all(tmp_path: Path, target: Callable[..., None], per_worker: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    ready, go = _CTX.Queue(), _CTX.Event()
    outs = [tmp_path / f"w{i}.json" for i in range(len(per_worker))]
    procs = [
        _CTX.Process(target=target, args=(str(tmp_path / "slots"), str(out), *args, ready, go))
        for out, args in zip(outs, per_worker, strict=True)
    ]
    for proc in procs:
        proc.start()
    try:
        for _ in procs:
            ready.get(timeout=60)
        go.set()
        for proc in procs:
            proc.join(120)
            assert proc.exitcode == 0
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.kill()
    return [json.loads(out.read_text()) for out in outs]


def test_eight_workers_with_cancels_and_timeouts_never_exceed_or_leak(tmp_path: Path) -> None:
    results = _run_all(tmp_path, _chaos_worker, [(40, seed) for seed in range(8)])
    intervals = [tuple(i) for r in results for i in r["intervals"]]
    assert intervals
    assert _most_at_once(intervals) <= LIMIT
    for org in ("a", "b", "c"):
        assert _most_at_once([i for i in intervals if i[2] == org]) <= ORG_CAP
    for r in results:
        assert len(r["outcomes"]) == 40
        assert sum(r["ends"].values()) == 40
        assert r["held_after"] == 0
        assert r["waiting_here"] == 0
    governor = _governor(str(tmp_path / "slots"))
    try:
        assert governor.held_total(1.0) == 0
    finally:
        governor.close()


def test_a_worker_killed_while_holding_the_mutex_frees_it(tmp_path: Path) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded

    ready = _CTX.Queue()
    proc, pid = _start(_mutex_holder, (str(tmp_path / "slots"),), ready)
    governor = _governor(str(tmp_path / "slots"))
    try:
        with pytest.raises(SandboxLimitExceeded) as caught:
            asyncio.run(governor.acquire(wait_s=0.2))
        # Nothing is known about the slots: the message gives no count.
        assert f"/{LIMIT})" not in str(caught.value)
        os.kill(pid, signal.SIGKILL)
        proc.join(10)
        started = time.monotonic()
        lease = asyncio.run(governor.acquire(wait_s=2))
        assert time.monotonic() - started < 0.5
        asyncio.run(governor.release(lease))
    finally:
        governor.close()
        if proc.is_alive():
            proc.kill()


def test_a_stopped_mutex_holder_delays_runs_until_it_resumes(tmp_path: Path) -> None:
    ready = _CTX.Queue()
    proc, pid = _start(_mutex_holder, (str(tmp_path / "slots"),), ready)
    governor = _governor(str(tmp_path / "slots"))
    try:
        os.kill(pid, signal.SIGSTOP)

        async def scenario() -> float:
            task = asyncio.ensure_future(governor.acquire(wait_s=10))
            await asyncio.sleep(0.3)
            assert not task.done()
            os.kill(pid, signal.SIGKILL)  # the stopped holder ends; its lock goes with it
            started = time.monotonic()
            lease = await task
            await governor.release(lease)
            return time.monotonic() - started

        assert asyncio.run(scenario()) < 0.5
    finally:
        governor.close()
        if proc.is_alive():
            proc.kill()


def test_a_killed_workers_subprocess_does_not_keep_its_slots(tmp_path: Path) -> None:
    """Slot files are close-on-exec: a child the worker started (a local run)
    does not inherit the locks, so the slots free when the worker dies even
    while the child lives on."""
    ready = _CTX.Queue()
    proc, child_pid = _start(_holder_with_child, (str(tmp_path / "slots"),), ready)
    governor = _governor(str(tmp_path / "slots"))
    try:
        assert governor.held_total(1.0) == 3
        os.kill(proc.pid, signal.SIGKILL)
        proc.join(10)
        os.kill(child_pid, 0)  # the child still runs
        assert governor.held_total(1.0) == 0
        assert governor.snapshot()["by_org"] == {}
    finally:
        governor.close()
        with contextlib.suppress(ProcessLookupError):
            os.kill(child_pid, signal.SIGKILL)
        if proc.is_alive():
            proc.kill()


def test_cross_worker_waits_stay_comparable(tmp_path: Path) -> None:
    """One busy worker (24 runs) and three light ones (4 runs each) on 6 slots.

    A release in the busy worker wakes its own waiters at once, while the
    light workers' waiters find out on their next poll. This measures the
    skew; it fails only if a light worker's runs wait far longer than the
    busy worker's.
    """
    results = _run_all(tmp_path, _load_worker, [(24,), (4,), (4,), (4,)])
    busy = results[0]["waits"]
    light = [w for r in results[1:] for w in r["waits"]]
    assert len(busy) == 24 and len(light) == 12
    p50 = statistics.median
    summary = {
        "busy_p50": round(p50(busy), 3), "busy_max": round(max(busy), 3),
        "light_p50": round(p50(light), 3), "light_max": round(max(light), 3),
    }
    print("cross-worker waits:", summary)
    assert max(light) < max(busy) * 3 + 0.5, summary
