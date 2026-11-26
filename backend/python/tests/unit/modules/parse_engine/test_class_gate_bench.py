"""Hot-path cost of the gate with a deep queue: the release that hands the next waiter its turn.

Two tests. The unit-tier one counts work, so it holds on any runner: a release must not scan the queue. The timing one is
marked ``stress`` (a wall-clock bound cannot be a unit test on a shared runner) and prints the p99.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.modules.parse_engine import class_gate
from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import CostClass


async def _deep_queue(n: int) -> tuple[ClassGate, list, list]:
    gate = ClassGate("bench", numbers=lambda: AdmissionNumbers(), capacity=lambda: PoolCapacity(8))
    running = [await gate.acquire(GateRequest(CostClass.SMALL, org=f"o{i % 5}"), deadline_s=5) for i in range(8)]
    waiters = [
        asyncio.create_task(
            gate.acquire(GateRequest(CostClass.SMALL if i % 3 else CostClass.LARGE, org=f"o{i % 7}"), deadline_s=120)
        )
        for i in range(n)
    ]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sum(gate.waiting(c) for c in CostClass) == n
    return gate, running, waiters


async def _release_loop(running: list, waiters: list, releases: int, per_release=None) -> list[float]:
    samples: list[float] = []
    for _ in range(releases):
        p = running.pop()
        t0 = time.perf_counter()
        p.release()  # frees a token and pumps: picks and claims the next waiter
        samples.append(time.perf_counter() - t0)
        if per_release is not None:
            per_release()
        await asyncio.sleep(0)
        done = [w for w in waiters if w.done()]
        running.extend(w.result() for w in done)
        waiters[:] = [w for w in waiters if not w.done()]
    return samples


async def test_a_release_with_a_thousand_waiters_does_a_bounded_amount_of_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counts the waiter keys read per release (the unit of every ordering comparison and queue scan): it must not
    grow with the queue. A scan of 1,000 waiters would read at least 1,000 per release."""
    reads = {"n": 0}
    real_key = class_gate._Waiter.key

    def counting_key(self: class_gate._Waiter) -> tuple[float, int]:
        reads["n"] += 1
        return real_key.fget(self)  # type: ignore[union-attr]

    gate, running, waiters = await _deep_queue(1000)
    monkeypatch.setattr(class_gate._Waiter, "key", property(counting_key))
    per_release: list[int] = []
    last = {"n": 0}

    def note() -> None:
        per_release.append(reads["n"] - last["n"])
        last["n"] = reads["n"]

    await _release_loop(running, waiters, 100, per_release=note)
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    assert max(per_release) < 100, max(per_release)  # O(log n) with 1,000 waiters; a queue scan is 1,000 or more


async def test_an_aged_head_check_does_not_scan_the_queue_when_nothing_is_unsplittable(monkeypatch: pytest.MonkeyPatch) -> None:
    gate, running, waiters = await _deep_queue(1000)
    assert gate._unbounded == []  # the age bound keeps its own short list: nothing to look at
    calls = {"n": 0}
    real = gate._aged_unbounded_head

    def counting(*a: object, **k: object) -> object:
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(gate, "_aged_unbounded_head", counting)
    await _release_loop(running, waiters, 20)
    assert calls["n"] == 0  # not even consulted
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)


@pytest.mark.stress
async def test_release_with_a_thousand_waiters_is_cheap() -> None:
    gate, running, waiters = await _deep_queue(1000)
    samples = sorted(await _release_loop(running, waiters, 300))
    p99 = samples[int(len(samples) * 0.99)]
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    print(f"release p99 {p99 * 1e6:.0f} us over {len(samples)} releases")
    # wall-clock-ok: upstream's release-latency bench (#6201): 1 ms is ten times its 100 us target
    assert p99 < 1e-3, f"p99 {p99 * 1e6:.0f} us"  # the target is 100 us; the bound leaves room for a quiet host
