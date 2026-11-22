"""``AdmissionGate.acquire(priority=True)``: a priority acquire is next in line, never over the limit."""

from __future__ import annotations

import asyncio

import pytest

from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


def _gate(limit: int) -> AdmissionGate:
    pool = Pool.INDEX_LIGHT
    return AdmissionGate(pool, LimitRegistry(Limits(values={p: (limit if p == pool else 1) for p in Pool})))


async def _settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
class TestPriorityAcquire:
    async def test_is_admitted_before_normal_waiters_queued_earlier(self) -> None:
        # P1
        gate = _gate(1)
        await gate.acquire()
        order: list[str] = []

        async def waiter(name: str, priority: bool) -> None:
            assert await gate.acquire(priority=priority)
            order.append(name)
            gate.release()

        tasks = [asyncio.create_task(waiter("normal-1", False)), asyncio.create_task(waiter("normal-2", False))]
        await _settle()
        tasks.append(asyncio.create_task(waiter("priority", True)))
        await _settle()

        gate.release()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

        assert order == ["priority", "normal-1", "normal-2"]

    async def test_priority_acquires_keep_their_arrival_order(self) -> None:
        gate = _gate(1)
        await gate.acquire()
        order: list[str] = []

        async def waiter(name: str) -> None:
            assert await gate.acquire(priority=True)
            order.append(name)
            gate.release()

        tasks = []
        for name in ("p-1", "p-2", "p-3"):
            tasks.append(asyncio.create_task(waiter(name)))
            await _settle()
        gate.release()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

        assert order == ["p-1", "p-2", "p-3"]

    async def test_a_fresh_priority_acquire_does_not_overtake_an_earlier_priority_waiter(self) -> None:
        gate = _gate(1)
        await gate.acquire()
        first = asyncio.create_task(gate.acquire(priority=True))
        await _settle()
        gate.release()  # the first waiter is woken but has not run

        assert await gate.acquire(priority=True, timeout=0.05) is False
        assert await asyncio.wait_for(first, timeout=5) is True

    async def test_never_goes_over_the_limit(self) -> None:
        # P2: the lane is ahead of the queue, not above the gate.
        gate = _gate(2)
        await gate.acquire()
        await gate.acquire()

        assert await gate.acquire(priority=True, timeout=0.05) is False
        assert gate.in_use == 2

    async def test_takes_room_that_is_free_while_a_normal_waiter_is_still_waking(self) -> None:
        gate = _gate(1)
        await gate.acquire()
        normal = asyncio.create_task(gate.acquire(timeout=0.2))
        await _settle()
        gate.release()  # the normal waiter is woken but has not run

        assert await gate.acquire(priority=True) is True
        assert gate.in_use == 1
        assert await normal is False  # it waited out its timeout: the priority acquire holds the permit

    async def test_a_priority_acquire_that_gives_up_leaves_the_queue_as_it_was(self) -> None:
        gate = _gate(1)
        await gate.acquire()
        normal = asyncio.create_task(gate.acquire())
        await _settle()

        assert await gate.acquire(priority=True, timeout=0.05) is False
        gate.release()

        assert await asyncio.wait_for(normal, timeout=5) is True
        assert gate.in_use == 1

    async def test_a_normal_acquire_is_unchanged(self) -> None:
        gate = _gate(1)
        await gate.acquire()
        order: list[str] = []

        async def waiter(name: str) -> None:
            assert await gate.acquire()
            order.append(name)
            gate.release()

        tasks = [asyncio.create_task(waiter(name)) for name in ("a", "b")]
        await _settle()
        gate.release()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

        assert order == ["a", "b"]
