"""Unit tests for app.utils.concurrency."""

import asyncio

import pytest

from app.services.llm_gateway.gateway import LLMGateway
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


def _fresh_gateway(monkeypatch: pytest.MonkeyPatch, limit: int) -> LLMGateway:
    from app.services.llm_gateway import gateway as gateway_module

    fresh = gateway_module.LLMGateway(limit)
    monkeypatch.setattr(gateway_module, "get_llm_gateway", lambda: fresh)
    return fresh


class TestIndexingLlmStats:
    """Wait against hold time is what separates our slot budget from a provider limit."""

    @pytest.mark.asyncio
    async def test_waits_and_holds_are_counted_apart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        gateway = _fresh_gateway(monkeypatch, 1)
        seen: list[dict] = []

        async def call() -> None:
            async with concurrency.indexing_llm_slot():
                await asyncio.sleep(0.05)

        async def watch() -> None:
            await asyncio.sleep(0.02)
            seen.append(dict(concurrency.indexing_llm_stats()))

        await asyncio.gather(call(), call(), watch())
        stats = concurrency.indexing_llm_stats()

        assert seen[0]["in_use"] == 1 and seen[0]["waiting"] == 1
        assert stats["in_use"] == 0 and stats["waiting"] == 0 and stats["acquired"] == 2
        assert stats["slots"] == 1
        # Unrounded: the snapshot rounds totals to 0.1 s, which would hide a ~0.05 s wait.
        timings = gateway.limiter.timings()
        assert timings.hold_s >= 0.09
        assert timings.wait_s >= 0.04  # the second call waited out the first

    @pytest.mark.asyncio
    async def test_hold_mean_ignores_calls_still_in_flight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        _fresh_gateway(monkeypatch, 2)
        assert concurrency.indexing_llm_stats()["hold_s_mean"] == 0.0

        release = asyncio.Event()

        async def held() -> None:
            async with concurrency.indexing_llm_slot():
                await release.wait()

        task = asyncio.create_task(held())
        async with concurrency.indexing_llm_slot():
            await asyncio.sleep(0.05)
        await asyncio.sleep(0)
        stats = concurrency.indexing_llm_stats()
        # One released call of ~0.05 s; the one still held must not halve the mean.
        assert stats["acquired"] == 2 and stats["in_use"] == 1
        assert stats["hold_s_mean"] >= 0.04
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_a_cancelled_wait_leaves_no_phantom_waiter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        gateway = _fresh_gateway(monkeypatch, 1)
        await gateway.limiter.acquire()  # the cap is full

        async def call() -> None:
            async with concurrency.indexing_llm_slot():
                pass

        task = asyncio.create_task(call())
        await asyncio.sleep(0.01)
        assert concurrency.indexing_llm_stats()["waiting"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stats = concurrency.indexing_llm_stats()
        assert stats["waiting"] == 0 and stats["in_use"] == 1 and stats["acquired"] == 0
        gateway.limiter.release()

    def test_rate_limit_retries_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        _fresh_gateway(monkeypatch, 1)
        concurrency.note_indexing_llm_rate_limit()
        assert concurrency.indexing_llm_stats()["rate_limit_retries"] == 1


class TestIndexingLlmSlotsRemaining:
    """The cap is process-wide, so the free count is too: a slot held on any loop counts."""

    @pytest.mark.asyncio
    async def test_counts_the_slots_held(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.utils import concurrency

        _fresh_gateway(monkeypatch, 3)
        assert concurrency.indexing_llm_slots_remaining() == 3
        async with indexing_llm_slot():
            assert concurrency.indexing_llm_slots_remaining() == 2
        assert concurrency.indexing_llm_slots_remaining() == 3

    def test_held_on_another_loop_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading

        from app.utils import concurrency

        _fresh_gateway(monkeypatch, 2)
        holding, done = threading.Event(), threading.Event()

        async def hold() -> None:
            async with concurrency.indexing_llm_slot():
                holding.set()
                await asyncio.to_thread(done.wait)

        worker = threading.Thread(target=lambda: asyncio.run(hold()))
        worker.start()
        assert holding.wait(5)
        assert concurrency.indexing_llm_slots_remaining() == 1
        done.set()
        worker.join(5)
        assert concurrency.indexing_llm_slots_remaining() == 2

    def test_outside_a_loop_reports_the_ceiling(self) -> None:
        from app.services.llm_gateway.gateway import get_llm_gateway
        from app.utils import concurrency

        limiter = get_llm_gateway().limiter
        assert concurrency.indexing_llm_slots_remaining() == limiter.limit - limiter.in_use
