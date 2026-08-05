"""Dedicated regression test for the aliasing defect (plan section 4.1):
point-sampling `in_use` cannot see thousands of millisecond-scale parses,
but accumulated demand can. Complements the accounting assertions already
made inline in test_gate.py.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


def _registry(limit: int) -> LimitRegistry:
    return LimitRegistry(Limits(values=dict.fromkeys(Pool, limit)))


class TestConfluenceShapedDemand:
    """300 concurrent 5ms holds against a limit of 2 — the same shape (and
    task count) as test_gate.py's
    test_massively_concurrent_short_holds_are_visible_to_demand, which
    proves this aliasing property for a Jira/Confluence BLOCKS/HTML sync
    (plan section 4.1)."""

    NUM_TASKS = 300

    def test_blocked_acquires_and_high_utilisation_survive_to_drain(self) -> None:
        registry = _registry(limit=2)
        gate = AdmissionGate(Pool.LIGHT_PARSE, registry, clock=time.monotonic)

        async def tiny_parse() -> None:
            async with gate.slot(timeout=60.0) as admitted:
                assert admitted
                await asyncio.sleep(0.005)

        async def scenario() -> float:
            start = time.monotonic()
            await asyncio.gather(*(tiny_parse() for _ in range(self.NUM_TASKS)))
            return time.monotonic() - start

        wall_elapsed = asyncio.run(scenario())

        # At the exact instant of drain, nothing is in flight -- a naive
        # point sampler would conclude there was never any demand.
        assert gate.in_use == 0

        demand = gate.drain_demand()
        assert demand.blocked_acquires > 0
        assert demand.utilisation(limit=2, interval=wall_elapsed) >= 0.7
        # Loose sanity bound only: under real scheduling, 300 concurrent
        # 5ms holds against a limit of 2 take noticeably longer in wall
        # time than the analytic minimum, but permit_seconds must still be
        # in the right ballpark (same order of magnitude), not zero/tiny.
        assert demand.permit_seconds > self.NUM_TASKS * 0.005 * 0.5

    def test_drain_demand_resets_so_next_interval_starts_clean(self) -> None:
        registry = _registry(limit=2)
        gate = AdmissionGate(Pool.LIGHT_PARSE, registry, clock=time.monotonic)

        async def tiny_parse() -> None:
            async with gate.slot(timeout=60.0):
                await asyncio.sleep(0.001)

        async def scenario() -> None:
            await asyncio.gather(*(tiny_parse() for _ in range(50)))

        asyncio.run(scenario())
        first = gate.drain_demand()
        assert first.blocked_acquires > 0 or first.completions == 50

        idle = gate.drain_demand()
        assert idle.blocked_acquires == 0
        assert idle.completions == 0
        assert idle.permit_seconds == pytest.approx(0.0)
