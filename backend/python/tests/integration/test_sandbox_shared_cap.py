"""Several query workers share one code sandbox cap.

Each worker is a real process with its own event loop and governor on one
slot directory, as uvicorn's workers are in one container. Runs record when
they held a slot, and the merged timeline must never exceed the cap.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import random
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Event

    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor

LIMIT = 6
ORG_CAP = 3
SLOT_FILES = 8


class _Outcomes:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, str | None]] = []

    def wait_start(self, reason: HoldReason) -> None:
        pass

    def wait_reason(self, reason: HoldReason) -> None:
        pass

    def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
        self.outcomes.append((outcome, reason.value if reason is not None else None))


def _governor(
    directory: str, observers: tuple[_Outcomes, ...] = (), org_share: float | None = None,
) -> SandboxResourceGovernor:
    from app.agent_loop_lib.sandbox.capacity import FixedLimit, SandboxCapacity
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
    from app.services.resource_governor.shared_slots import SharedSlots

    if org_share is None:
        capacity = SandboxCapacity.fixed(LIMIT, ORG_CAP, slot_files=SLOT_FILES)
    else:
        from app import edition

        # The per-org cap comes from the edition's share of the limit.
        edition.sandbox_org_share = lambda: org_share
        capacity = SandboxCapacity.fixed(LIMIT, slot_files=SLOT_FILES)
    return SandboxResourceGovernor(
        capacity, SharedSlots(Path(directory), SLOT_FILES),
        limit_provider=FixedLimit(capacity), observers=observers, poll_s=0.05,
    )


def _worker(
    directory: str, out: str, runs: int, orgs: list[str], wait_s: float,
    ready: Queue[int], go: Event, org_share: float | None = None,
) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded

    recorder = _Outcomes()
    governor = _governor(directory, (recorder,), org_share)
    rng = random.Random(os.getpid())
    intervals: list[tuple[float, float, str]] = []
    refusals: list[str] = []

    async def one(org: str) -> None:
        try:
            lease = await governor.acquire(org_id=org, wait_s=wait_s)
        except SandboxLimitExceeded as exc:
            refusals.append(exc.reason.value)
            return
        start = time.monotonic()
        await asyncio.sleep(rng.uniform(0.1, 0.3))
        intervals.append((start, time.monotonic(), org))
        await governor.release(lease)

    async def main() -> None:
        await asyncio.gather(*(one(orgs[i % len(orgs)]) for i in range(runs)))

    ready.put(os.getpid())
    go.wait(30)
    asyncio.run(main())
    Path(out).write_text(json.dumps({
        "intervals": intervals, "refusals": refusals,
        "outcomes": recorder.outcomes, "acquires": runs,
    }))


def _holder(directory: str, held: int, ready: Queue[int]) -> None:
    governor = _governor(directory)

    async def main() -> None:
        for _ in range(held):
            await governor.acquire()
        ready.put(os.getpid())
        await asyncio.sleep(60)

    asyncio.run(main())


def _run_workers(
    tmp_path: Path, *, workers: int, runs: int, orgs: list[str], wait_s: float,
    org_share: float | None = None,
) -> list[dict[str, Any]]:
    ctx = multiprocessing.get_context("spawn")
    ready, go = ctx.Queue(), ctx.Event()
    outs = [tmp_path / f"w{i}.json" for i in range(workers)]
    procs = [
        ctx.Process(
            target=_worker,
            args=(str(tmp_path / "slots"), str(out), runs, orgs, wait_s, ready, go, org_share),
        )
        for out in outs
    ]
    for proc in procs:
        proc.start()
    try:
        for _ in procs:
            ready.get(timeout=60)
        go.set()
        for proc in procs:
            proc.join(60)
            assert proc.exitcode == 0
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.kill()
    return [json.loads(out.read_text()) for out in outs]


def _most_at_once(intervals: list[tuple[float, float, str]]) -> int:
    events = sorted([(s, 1) for s, _, _ in intervals] + [(e, -1) for _, e, _ in intervals])
    now = peak = 0
    for _, step in events:
        now += step
        peak = max(peak, now)
    return peak


def test_forty_runs_over_four_workers_never_exceed_the_cap(tmp_path: Path) -> None:
    results = _run_workers(tmp_path, workers=4, runs=10, orgs=[""], wait_s=30)
    intervals = [tuple(i) for r in results for i in r["intervals"]]
    assert len(intervals) == 40
    assert _most_at_once(intervals) <= LIMIT
    assert _most_at_once(intervals) >= 2  # the workers really ran side by side
    for r in results:
        assert len(r["outcomes"]) == r["acquires"]


def test_no_org_exceeds_its_cap_across_workers(tmp_path: Path) -> None:
    results = _run_workers(tmp_path, workers=4, runs=10, orgs=["a", "b"], wait_s=30, org_share=0.5)
    intervals = [tuple(i) for r in results for i in r["intervals"]]
    assert len(intervals) == 40
    for org in ("a", "b"):
        assert _most_at_once([i for i in intervals if i[2] == org]) <= ORG_CAP
    for r in results:
        assert len(r["outcomes"]) == r["acquires"]


def test_refusals_under_saturation_carry_their_reason(tmp_path: Path) -> None:
    results = _run_workers(tmp_path, workers=4, runs=10, orgs=[""], wait_s=0.2)
    refusals = [reason for r in results for reason in r["refusals"]]
    assert refusals and set(refusals) == {"slots_full"}
    for r in results:
        refused = [o for o in r["outcomes"] if o[0] == "refused"]
        assert refused == [["refused", "slots_full"]] * len(r["refusals"])
        assert len(r["outcomes"]) == r["acquires"]


def test_a_killed_workers_slots_free_themselves(tmp_path: Path) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    holder = ctx.Process(target=_holder, args=(str(tmp_path / "slots"), ORG_CAP, ready))
    holder.start()
    governor = _governor(str(tmp_path / "slots"))
    try:
        ready.get(timeout=60)

        async def take(n: int, wait_s: float) -> list[Any]:
            return [await governor.acquire(wait_s=wait_s) for _ in range(n)]

        mine = asyncio.run(take(LIMIT - ORG_CAP, 0))
        with pytest.raises(SandboxLimitExceeded):
            asyncio.run(take(1, 0))
        os.kill(holder.pid, signal.SIGKILL)
        holder.join(10)
        started = time.monotonic()
        more = asyncio.run(take(ORG_CAP, 1.0))
        assert time.monotonic() - started < 1.0
        assert len(mine) + len(more) == LIMIT
    finally:
        governor.close()
        if holder.is_alive():
            holder.kill()
