"""Big records reserve their memory; ordinary ones never wait for it."""
from __future__ import annotations

import asyncio

import pytest

from app.services.resource_governor.memory_budget import (
    RECORD_MEMORY_FACTOR,
    MemoryBudget,
    record_memory_cost,
)
from app.services.resource_governor.models import ParseTier

MB = 1024 * 1024


class TestRecordMemoryCost:
    def test_a_record_the_governor_already_sizes_for_reserves_nothing(self) -> None:
        assert record_memory_cost(ParseTier.LIGHT, 2 * MB) == 0
        assert record_memory_cost(ParseTier.HEAVY, 30 * MB) == 0
        assert record_memory_cost(ParseTier.LIGHT, None) == 0

    def test_a_big_record_reserves_its_whole_estimate(self) -> None:
        assert record_memory_cost(ParseTier.LIGHT, 100 * MB) == 100 * MB * RECORD_MEMORY_FACTOR
        assert record_memory_cost(ParseTier.HEAVY, 100 * MB) == 100 * MB * RECORD_MEMORY_FACTOR


class TestMemoryBudget:
    async def test_without_a_limit_everything_is_admitted(self) -> None:
        budget = MemoryBudget()

        assert await budget.reserve(10_000 * MB, timeout=0.01)
        assert await budget.reserve(10_000 * MB, timeout=0.01)

    async def test_a_record_waits_while_the_memory_is_spoken_for(self) -> None:
        budget = MemoryBudget()
        budget.limit = 500 * MB
        assert await budget.reserve(400 * MB)

        assert not await budget.reserve(200 * MB, timeout=0.05)

        budget.release(400 * MB)
        assert await budget.reserve(200 * MB, timeout=0.05)

    async def test_a_record_bigger_than_the_whole_budget_runs_alone(self) -> None:
        budget = MemoryBudget()
        budget.limit = 100 * MB

        assert await budget.reserve(300 * MB, timeout=0.05)
        assert not await budget.reserve(1 * MB, timeout=0.05)

    async def test_a_release_admits_the_next_in_line_in_arrival_order(self) -> None:
        budget = MemoryBudget()
        budget.limit = 100 * MB
        assert await budget.reserve(100 * MB)
        admitted: list[str] = []

        async def wait(name: str, size: int) -> None:
            await budget.reserve(size)
            admitted.append(name)

        first = asyncio.create_task(wait("first", 80 * MB))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(wait("second", 10 * MB))
        await asyncio.sleep(0.01)
        assert admitted == []

        budget.release(100 * MB)
        await asyncio.wait_for(asyncio.gather(first, second), 2)

        assert admitted == ["first", "second"]

    async def test_a_limit_raised_by_a_sample_admits_a_waiter_without_a_release(self) -> None:
        budget = MemoryBudget()
        budget.limit = 100 * MB
        assert await budget.reserve(90 * MB)
        waiting = asyncio.create_task(budget.reserve(50 * MB, timeout=3))
        await asyncio.sleep(0.01)

        budget.limit = 200 * MB

        assert await asyncio.wait_for(waiting, 3)

    async def test_a_cancelled_waiter_does_not_hold_up_the_queue(self) -> None:
        budget = MemoryBudget()
        budget.limit = 100 * MB
        assert await budget.reserve(100 * MB)
        cancelled = asyncio.create_task(budget.reserve(50 * MB))
        await asyncio.sleep(0.01)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        budget.release(100 * MB)

        assert await budget.reserve(50 * MB, timeout=0.05)
        assert budget.stats() == {
            "limit_bytes": 100 * MB, "reserved_bytes": 50 * MB, "waiting": 0, "waiting_held_bytes": 0,
        }

    async def test_what_a_waiting_record_already_holds_counts_against_everyone_else(self) -> None:
        """A record waits here after its download: its buffer is in memory already."""
        budget = MemoryBudget()
        budget.limit = 1000 * MB
        assert await budget.reserve(800 * MB, held=200 * MB)
        waiting = asyncio.create_task(budget.reserve(800 * MB, held=200 * MB))
        await asyncio.sleep(0.01)

        assert budget.stats()["waiting_held_bytes"] == 200 * MB
        # 800 running + 200 held by the waiter + 150 > 1000.
        assert not await budget.reserve(150 * MB, timeout=0.05, held=50 * MB)

        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert budget.stats()["waiting_held_bytes"] == 0
        assert await budget.reserve(150 * MB, timeout=0.05, held=50 * MB)

    async def test_a_waiter_s_own_held_bytes_are_part_of_its_reservation(self) -> None:
        budget = MemoryBudget()
        budget.limit = 900 * MB
        assert await budget.reserve(500 * MB)
        waiting = asyncio.create_task(budget.reserve(500 * MB, held=400 * MB))
        await asyncio.sleep(0.01)
        assert not waiting.done()

        # Exactly enough for 500 running + this 500, its 400 held counted once.
        budget.limit = 1000 * MB
        assert await asyncio.wait_for(waiting, 3)
        assert budget.stats()["reserved_bytes"] == 1000 * MB
