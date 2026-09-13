"""Unit tests for app.utils.concurrency."""

import asyncio

import pytest

from app.utils.concurrency import (
    MAX_CONCURRENT_INDEXING_LLM_CALLS,
    MAX_CONCURRENT_PAGE_BUILDS,
    MAX_CONCURRENT_ROW_BATCHES,
    MAX_CONCURRENT_TABLES,
    TABLE_ROW_BATCH_SIZE,
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
    async def test_the_slot_is_a_permit_of_the_gateways_process_wide_cap(self) -> None:
        from app.services.llm_gateway.gateway import get_llm_gateway

        limiter = get_llm_gateway().limiter
        before = limiter.in_use
        async with indexing_llm_slot():
            assert limiter.in_use == before + 1
        assert limiter.in_use == before


class TestIndexingLlmSlot:
    @pytest.mark.asyncio
    async def test_slot_acquires_and_releases(self):
        async with indexing_llm_slot():
            pass
