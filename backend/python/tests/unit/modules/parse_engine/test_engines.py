"""The Docling engine: demand-sized floor, AIMD on 429, and the engine decided once per revision."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.parse_engine.admission import AdmissionNumbers
from app.modules.parse_engine.class_gate import ClassGate, GateRequest, PoolCapacity
from app.modules.parse_engine.cost import (
    CostClass,
    CostConfig,
    CostInputs,
    Engine,
    FormatKind,
    estimate,
)
from app.modules.parse_engine.engines import (
    DemandEwma,
    DoclingEngine,
    EngineDecision,
    decide_engine,
    docling_deadline_s,
    docling_floor,
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.mark.parametrize(
    ("rate", "service", "static_min", "cap", "want"),
    [
        (0.0, 1.0, 1, None, 1),  # no demand: the static minimum
        (0.2, 10.5, 1, None, 4),  # lambda 0.2/s, 10.5 pages at 1 s/page: ceil(2.1 / 0.55) = 4
        (0.2, 10.5, 6, None, 6),
        (2.0, 10.5, 1, 8, 8),  # bounded by the width the budget can give
        (0.5, 1.1, 1, None, 1),  # exactly 0.55 of one conversion: one
    ],
)
def test_floor_is_demand_sized(rate: float, service: float, static_min: int, cap: int | None, want: int) -> None:
    assert docling_floor(rate, service, static_min=static_min, max_workers=cap) == want


def test_floor_follows_measured_demand_and_decays() -> None:
    clock = Clock()
    ewma = DemandEwma(initial_rate=0.2, initial_service_s=10.5, clock=clock)
    eng = DoclingEngine(max_workers=16, demand=ewma, clock=clock)
    assert eng.floor == 4
    clock.t += 3600  # an hour of nothing: the rate decays to the static minimum
    assert eng.floor == 1


def test_floor_comes_from_the_ledger_planner_when_given() -> None:
    seen: list[tuple[float, float]] = []

    def plan(rate: float, service: float) -> int:
        seen.append((rate, service))
        return 6

    eng = DoclingEngine(max_workers=8, floor_fn=plan, demand=DemandEwma(initial_rate=0.1, initial_service_s=2.0))
    assert eng.floor == 6 and seen
    assert DoclingEngine(max_workers=4, floor_fn=lambda *_: 99).floor == 4  # never above the workers there are


def test_aimd_halves_on_429_and_recovers() -> None:
    eng = DoclingEngine(max_workers=8, increase_after=3)
    for want in (4, 2, 1, 1):
        eng.on_throttled()
        assert eng.limit == want
    for _ in range(3):
        eng.on_success()
    assert eng.limit == 2
    for _ in range(3 * 10):
        eng.on_success()
    assert eng.limit == 8  # back to the width, never above it


async def test_in_flight_never_exceeds_the_limit_and_a_429_lowers_it() -> None:
    eng = DoclingEngine(max_workers=4)
    peak = 0

    async def convert(i: int) -> None:
        nonlocal peak
        async with eng.slot():
            peak = max(peak, eng.in_flight)
            if i == 0:
                eng.on_throttled()  # a running call sees a 429: the limit halves under the others
            await asyncio.sleep(0.01)

    await asyncio.wait_for(asyncio.gather(*(convert(i) for i in range(40))), 10)
    assert eng.max_in_flight <= 4 and peak <= 4
    assert eng.in_flight == 0
    assert eng.limit == 2


async def test_retry_after_holds_new_calls() -> None:
    eng = DoclingEngine(max_workers=2)
    eng.on_throttled(retry_after_s=0.3)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    async with eng.slot():
        pass
    assert loop.time() - t0 >= 0.25


async def test_a_docling_throttle_slows_only_docling() -> None:
    """A 429 storm leaves the CPU gate alone: the engine shares no state with it and reports to no coordinator."""
    eng = DoclingEngine(max_workers=4)
    cpu = ClassGate("heavy_parse", numbers=lambda: AdmissionNumbers(), capacity=lambda: PoolCapacity(4))
    for _ in range(20):
        eng.on_throttled(retry_after_s=5)
    assert eng.limit == 1
    cap = cpu._capacity()
    permits = [
        await asyncio.wait_for(cpu.acquire(GateRequest(CostClass.SMALL), deadline_s=1), 0.5) for _ in range(cap.capacity)
    ]
    assert cpu.running(CostClass.SMALL) == 4
    for p in permits:
        p.release()


def test_unavailable_after_a_continuous_spell_and_cleared_by_a_success() -> None:
    clock = Clock()
    eng = DoclingEngine(max_workers=4, fallback_after_s=60.0, clock=clock)
    eng.on_throttled()
    clock.t += 30
    assert not eng.unavailable()
    eng.on_throttled()
    clock.t += 31
    assert eng.unavailable()  # the spell began at the first 429
    eng.on_success()
    assert not eng.unavailable()


def test_deadline_rule() -> None:
    assert docling_deadline_s(1.0) == 60.0  # floor
    assert docling_deadline_s(20.0) == 80.0
    assert docling_deadline_s(60.0) == 120.0  # capped


def test_engine_decided_once() -> None:
    cfg = CostConfig()
    small = estimate(CostInputs(FormatKind.PDF, 1_000_000, pages=10), cfg)
    large = estimate(CostInputs(FormatKind.PDF, 1_000_000, pages=100), cfg)
    assert decide_engine(small) == EngineDecision(Engine.DOCLING, "estimate")
    assert decide_engine(large) == EngineDecision(Engine.CPU, "estimate")
    # Docling unavailable for long enough: small falls back to the CPU engine, large is unaffected
    assert decide_engine(small, docling_unavailable=True) == EngineDecision(Engine.CPU, "unavailable")
    assert decide_engine(large, docling_unavailable=True).reason == "estimate"
    # a recorded decision is reused as it is, whatever the estimate or Docling's state now say
    rec = EngineDecision(Engine.CPU, "deadline")
    assert decide_engine(small, recorded=rec) == EngineDecision(Engine.CPU, "recorded")
    assert decide_engine(small, recorded=EngineDecision(Engine.DOCLING, "estimate"), docling_unavailable=True).engine is Engine.DOCLING
