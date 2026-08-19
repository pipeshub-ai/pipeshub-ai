from __future__ import annotations

import asyncio
import time

import pytest

from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


def _registry(limit: int, pool: Pool = Pool.HEAVY_PARSE) -> LimitRegistry:
    return LimitRegistry(Limits(values={p: (limit if p == pool else 1) for p in Pool}))


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
class TestAdmissionGateBasics:
    async def test_weighted_acquire_and_release(self) -> None:
        registry = _registry(limit=3)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)

        assert await gate.acquire(cost=2) is True
        assert gate.in_use == 2
        assert await gate.acquire(cost=1) is True
        assert gate.in_use == 3
        # No room left for a 3rd.
        assert await gate.acquire(cost=1, timeout=0.01) is False

        gate.release(cost=2)
        assert gate.in_use == 1
        assert await gate.acquire(cost=1) is True
        assert gate.in_use == 2

    async def test_timeout_returns_false_not_an_exception(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)

        result = await gate.acquire(cost=1, timeout=0.05)
        assert result is False
        # A failed acquire must not have consumed a permit.
        assert gate.in_use == 1

    async def test_oversized_cost_admitted_only_when_idle(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)

        # Nothing in flight -> an oversized (cost > limit) request is still
        # admitted alone, preventing a permanent deadlock.
        assert await gate.acquire(cost=5) is True
        assert gate.in_use == 5

        # Now something is in flight; a second oversized request must wait.
        assert await gate.acquire(cost=5, timeout=0.02) is False

    async def test_shrink_does_not_revoke_in_flight_permits(self) -> None:
        registry = _registry(limit=4)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=4)
        assert gate.in_use == 4

        registry.set(Pool.HEAVY_PARSE, 1)  # shrink below in_use

        assert gate.in_use == 4  # nothing revoked
        assert await gate.acquire(cost=1, timeout=0.02) is False  # but no new admission either

        gate.release(cost=4)
        assert await gate.acquire(cost=1) is True  # now within the new, smaller limit

    async def test_slot_context_manager_releases_on_exit(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)

        async with gate.slot() as admitted:
            assert admitted is True
            assert gate.in_use == 1
        assert gate.in_use == 0

    async def test_slot_context_manager_does_not_release_when_not_admitted(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)  # occupy the only slot

        async with gate.slot(timeout=0.02) as admitted:
            assert admitted is False
        assert gate.in_use == 1  # unaffected by the failed nested acquire

    async def test_over_release_cannot_mint_permits(self) -> None:
        registry = _registry(limit=4)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)

        gate.release(cost=3)
        assert gate.in_use == 0


@pytest.mark.asyncio
class TestAdmissionGateWaking:
    async def test_waiters_wake_promptly_when_limit_rises(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)  # occupy the only slot

        async def waiter() -> bool:
            return await gate.acquire(cost=1, timeout=5.0)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert not task.done()

        registry.set(Pool.HEAVY_PARSE, 2)  # raise the limit -> should wake immediately

        result = await asyncio.wait_for(task, timeout=0.5)
        assert result is True

    async def test_waiters_wake_on_release(self) -> None:
        registry = _registry(limit=1)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)

        async def waiter() -> bool:
            return await gate.acquire(cost=1, timeout=5.0)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        gate.release(cost=1)

        result = await asyncio.wait_for(task, timeout=0.5)
        assert result is True


@pytest.mark.asyncio
class TestAdmissionGateCrossLoop:
    async def test_second_loop_raises_runtime_error(self) -> None:
        registry = _registry(limit=2)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry)
        await gate.acquire(cost=1)  # binds to the currently-running (pytest-asyncio) loop

        other_loop = asyncio.new_event_loop()
        coro = gate.acquire(cost=1)
        try:
            with pytest.raises(RuntimeError, match="event loop"):
                other_loop.run_until_complete(coro)
        finally:
            coro.close()
            other_loop.close()


class TestAdmissionGateDemandAccounting:
    def test_drain_resets_accumulators(self) -> None:
        clock = FakeClock()
        registry = _registry(limit=2)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry, clock=clock)

        async def scenario() -> None:
            await gate.acquire(cost=1)
            clock.advance(1.0)
            gate.release(cost=1)

        asyncio.run(scenario())

        demand = gate.drain_demand()
        assert demand.permit_seconds == pytest.approx(1.0)
        assert demand.completions == 1

        demand_again = gate.drain_demand()
        assert demand_again.permit_seconds == 0.0
        assert demand_again.completions == 0

    def test_permit_seconds_matches_analytic_integral(self) -> None:
        clock = FakeClock()
        registry = _registry(limit=3)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry, clock=clock)

        async def scenario() -> None:
            await gate.acquire(cost=1)   # in_use: 0 -> 1 at t=0
            clock.advance(2.0)           # holds 1 permit for 2s
            await gate.acquire(cost=2)   # in_use: 1 -> 3 at t=2
            clock.advance(3.0)           # holds 3 permits for 3s
            gate.release(cost=2)         # in_use: 3 -> 1 at t=5
            clock.advance(1.0)           # holds 1 permit for 1s
            gate.release(cost=1)         # in_use: 1 -> 0 at t=6

        asyncio.run(scenario())

        # integral of in_use*dt = 1*2 + 3*3 + 1*1 = 12
        demand = gate.drain_demand()
        assert demand.permit_seconds == pytest.approx(12.0)

    def test_massively_concurrent_short_holds_are_visible_to_demand(self) -> None:
        """The aliasing regression (plan section 4.1): 300 concurrent
        ~5ms acquisitions against a limit of 2 must show blocked_acquires
        and high utilisation even though in_use is back to 0 by the time
        the controller drains — proving demand is not point-sampled.
        """
        registry = _registry(limit=2)
        gate = AdmissionGate(Pool.HEAVY_PARSE, registry, clock=time.monotonic)

        async def worker() -> None:
            async with gate.slot(timeout=30.0) as admitted:
                assert admitted
                await asyncio.sleep(0.005)

        async def scenario() -> None:
            await asyncio.gather(*(worker() for _ in range(300)))

        wall_start = time.monotonic()
        asyncio.run(scenario())
        wall_elapsed = time.monotonic() - wall_start

        demand = gate.drain_demand()
        assert gate.in_use == 0  # point-in-time read would see no contention at all
        assert demand.blocked_acquires > 0
        # Fully back-to-back at limit=2 for the whole run -> mean occupancy
        # over the wall-clock window is near the limit, i.e. high utilisation.
        assert demand.utilisation(limit=2, interval=wall_elapsed) >= 0.7
        # A controller sampling once at the end still sees demand: with 300
        # tasks and a limit of 2, the large majority had to wait.
        assert demand.blocked_acquires >= 290
