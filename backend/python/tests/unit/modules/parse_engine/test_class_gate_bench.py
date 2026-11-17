"""Hot-path cost of the gate with a deep queue: the release that hands the next waiter its turn."""
from __future__ import annotations

import asyncio
import time

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import CostClass


async def test_release_with_a_thousand_waiters_is_cheap() -> None:
    gate = ClassGate("bench", numbers=lambda: AdmissionNumbers(), capacity=lambda: PoolCapacity(8))
    running = [await gate.acquire(GateRequest(CostClass.SMALL, org=f"o{i % 5}"), deadline_s=5) for i in range(8)]
    waiters = [
        asyncio.create_task(
            gate.acquire(GateRequest(CostClass.SMALL if i % 3 else CostClass.LARGE, org=f"o{i % 7}"), deadline_s=120)
        )
        for i in range(1000)
    ]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sum(gate.waiting(c) for c in CostClass) == 1000
    samples: list[float] = []
    for _ in range(300):
        p = running.pop()
        t0 = time.perf_counter()
        p.release()  # frees a token and pumps: picks and claims the next waiter
        samples.append(time.perf_counter() - t0)
        await asyncio.sleep(0)
        done = [w for w in waiters if w.done()]
        running.extend(w.result() for w in done)
        waiters = [w for w in waiters if not w.done()]
    samples.sort()
    p99 = samples[int(len(samples) * 0.99)]
    for w in waiters:
        w.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    print(f"release p99 {p99 * 1e6:.0f} us over {len(samples)} releases")
    assert p99 < 1e-3, f"p99 {p99 * 1e6:.0f} us"  # the target is 100 us; the CI bound leaves room for a busy runner
