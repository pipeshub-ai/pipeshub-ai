"""Unit tests for app.utils.concurrency."""

import asyncio

import pytest

from app.utils.concurrency import (
    MAX_CONCURRENT_INDEXING_LLM_CALLS,
    MAX_CONCURRENT_PAGE_BUILDS,
    MAX_CONCURRENT_ROW_BATCHES,
    MAX_CONCURRENT_TABLES,
    TABLE_ROW_BATCH_SIZE,
    _indexing_llm_semaphore,
    gather_with_concurrency,
    indexing_llm_slot,
)


class TestConstants:
    def test_max_concurrent_tables(self):
        assert MAX_CONCURRENT_TABLES == 15

    def test_max_concurrent_row_batches(self):
        assert MAX_CONCURRENT_ROW_BATCHES == 8

    def test_max_concurrent_page_builds(self):
        assert MAX_CONCURRENT_PAGE_BUILDS == 4

    def test_table_row_batch_size(self):
        assert TABLE_ROW_BATCH_SIZE == 50

    def test_max_concurrent_indexing_llm_calls_default(self):
        assert MAX_CONCURRENT_INDEXING_LLM_CALLS >= 1


class TestGatherWithConcurrency:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        result = await gather_with_concurrency(5)
        assert result == []

    @pytest.mark.asyncio
    async def test_preserves_order(self):
        async def make(val, delay):
            await asyncio.sleep(delay)
            return val

        result = await gather_with_concurrency(
            10,
            make("a", 0.03),
            make("b", 0.01),
            make("c", 0.02),
        )
        assert result == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_limits_concurrency(self):
        peak = 0
        current = 0

        async def tracked():
            nonlocal peak, current
            current += 1
            if current > peak:
                peak = current
            await asyncio.sleep(0.02)
            current -= 1
            return True

        results = await gather_with_concurrency(
            2,
            tracked(), tracked(), tracked(), tracked(), tracked(),
        )
        assert all(results)
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_return_exceptions_true(self):
        async def ok():
            return 1

        async def fail():
            raise ValueError("boom")

        results = await gather_with_concurrency(5, ok(), fail(), return_exceptions=True)
        assert results[0] == 1
        assert isinstance(results[1], ValueError)

    @pytest.mark.asyncio
    async def test_return_exceptions_false_propagates(self):
        async def fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await gather_with_concurrency(5, fail())

    @pytest.mark.asyncio
    async def test_limit_one(self):
        order = []

        async def record(val):
            order.append(f"start-{val}")
            await asyncio.sleep(0.01)
            order.append(f"end-{val}")
            return val

        await gather_with_concurrency(1, record("a"), record("b"))
        assert order.index("end-a") < order.index("start-b")


class TestIndexingLlmSemaphore:
    @pytest.mark.asyncio
    async def test_returns_semaphore(self):
        sem = _indexing_llm_semaphore()
        assert isinstance(sem, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_same_loop_same_semaphore(self):
        sem1 = _indexing_llm_semaphore()
        sem2 = _indexing_llm_semaphore()
        assert sem1 is sem2


class TestIndexingLlmSlot:
    @pytest.mark.asyncio
    async def test_slot_acquires_and_releases(self):
        async with indexing_llm_slot():
            pass


class TestIndexingLlmStats:
    """Wait against hold time is what separates our slot budget from a provider limit."""

    @pytest.mark.asyncio
    async def test_waits_and_holds_are_counted_apart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        monkeypatch.setattr(concurrency, "_slot_stats", concurrency._SlotStats())
        sem = asyncio.Semaphore(1)
        monkeypatch.setattr(concurrency, "_indexing_llm_semaphore", lambda: sem)
        seen: list[dict] = []

        async def call() -> None:
            async with concurrency.indexing_llm_slot():
                await asyncio.sleep(0.05)

        async def watch() -> None:
            await asyncio.sleep(0.02)
            seen.append(concurrency.indexing_llm_stats())

        await asyncio.gather(call(), call(), watch())
        stats = concurrency.indexing_llm_stats()

        assert seen[0]["in_use"] == 1 and seen[0]["waiting"] == 1
        assert stats["in_use"] == 0 and stats["waiting"] == 0 and stats["acquired"] == 2
        # Unrounded: the snapshot rounds totals to 0.1 s, which would hide a ~0.05 s wait.
        assert concurrency._slot_stats.hold_s >= 0.09
        assert concurrency._slot_stats.wait_s >= 0.04  # the second call waited out the first

    def test_hold_mean_ignores_calls_still_in_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        stats = concurrency._SlotStats()
        stats.acquired, stats.in_use, stats.hold_s = 10, 9, 10.0
        monkeypatch.setattr(concurrency, "_slot_stats", stats)

        assert concurrency.indexing_llm_stats()["hold_s_mean"] == 10.0

        stats.acquired, stats.in_use, stats.hold_s = 3, 3, 0.0
        assert concurrency.indexing_llm_stats()["hold_s_mean"] == 0.0

    @pytest.mark.asyncio
    async def test_a_cancelled_wait_leaves_no_phantom_waiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        monkeypatch.setattr(concurrency, "_slot_stats", concurrency._SlotStats())
        sem = asyncio.Semaphore(0)
        monkeypatch.setattr(concurrency, "_indexing_llm_semaphore", lambda: sem)

        async def call() -> None:
            async with concurrency.indexing_llm_slot():
                pass

        task = asyncio.create_task(call())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stats = concurrency.indexing_llm_stats()
        assert stats["waiting"] == 0 and stats["in_use"] == 0 and stats["acquired"] == 0


class TestIndexingLlmSlotsRemaining:
    """The free count comes from the slots this loop holds, not the semaphore's
    private counter, which a missing attribute would turn into "saturated"."""

    @pytest.mark.asyncio
    async def test_counts_the_slots_this_loop_holds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        class _PublicOnlySemaphore:
            def __init__(self) -> None:
                self._inner = asyncio.Semaphore(concurrency.scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS))

            async def acquire(self) -> bool:
                return await self._inner.acquire()

            def release(self) -> None:
                self._inner.release()

        monkeypatch.setattr(concurrency, "_slot_stats", concurrency._SlotStats())
        sem = _PublicOnlySemaphore()
        monkeypatch.setattr(concurrency, "_indexing_llm_semaphore", lambda: sem)
        monkeypatch.setitem(concurrency._sem_by_loop, asyncio.get_running_loop(), sem)
        ceiling = concurrency.scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS)

        assert concurrency.indexing_llm_slots_remaining() == ceiling
        async with indexing_llm_slot():
            assert concurrency.indexing_llm_slots_remaining() == ceiling - 1
        assert concurrency.indexing_llm_slots_remaining() == ceiling

    def test_outside_a_loop_reports_the_ceiling(self) -> None:
        from app.utils import concurrency

        assert concurrency.indexing_llm_slots_remaining() == concurrency.scaled(MAX_CONCURRENT_INDEXING_LLM_CALLS)
