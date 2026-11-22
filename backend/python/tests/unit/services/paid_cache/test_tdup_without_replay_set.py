"""Replays cost CPU and I/O, never tokens: the durable cache, not the Redis replay set, is what prevents a duplicate."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.pdf import vlm_ocr_strategy
from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.locks import InProcessLocks
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

PAGES = 12
REPLAYS = 20


def _strategy(model: CountingModel) -> VLMOCRStrategy:
    strategy = VLMOCRStrategy(logging.getLogger("test-tdup"), MagicMock())
    strategy.PAGE_RENDER_BATCH_SIZE = 6
    strategy.CONCURRENCY_LIMIT = 4
    strategy.llm = model  # type: ignore[assignment]
    strategy.doc = MagicMock(pages=[MagicMock(width=10, height=20) for _ in range(PAGES)])
    strategy._pdf_path = "/unused.pdf"
    strategy._render_page_batch_to_base64 = lambda numbers: {  # type: ignore[method-assign]
        n: f"data:image/png;base64,page-{n}" for n in numbers
    }
    return strategy


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> PaidResultCache:
    monkeypatch.setenv("REPLAY_SET", "off")
    built = PaidResultCache(MemoryResultStore(), InProcessLocks())
    monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, **_k: built)
    return built


async def test_no_duplicate_paid_calls_without_the_replay_set(cache: PaidResultCache) -> None:
    from app.modules.indexing import replay_set

    assert replay_set.enabled() is False                       # the replay set is off for this whole test
    model = CountingModel("vlm")

    with record_in_flight("org-1", "rec-1"):
        # A rebalance replays twenty updates of the record, one after another and then all at once.
        for _ in range(REPLAYS // 2):
            await _strategy(model)._preprocess_document()
        await asyncio.gather(*(_strategy(model)._preprocess_document() for _ in range(REPLAYS // 2)))

    assert model.total == PAGES                                 # 12 pages paid once in 20 runs
    assert model.duplicates == 0


async def test_the_same_holds_when_redis_is_unreachable_for_the_lock_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLAY_SET", "off")

    class Down:
        async def acquire(self, name: str, ttl_ms: int) -> str | None:
            raise ConnectionError("redis down")

        async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
            return False

        async def release(self, name: str, token: str) -> None:
            return None

    built = PaidResultCache(MemoryResultStore(), Down())
    monkeypatch.setattr(vlm_ocr_strategy, "paid_cache_for", lambda *_a, **_k: built)
    model = CountingModel("vlm")

    with record_in_flight("org-1", "rec-1"):
        for _ in range(5):
            await _strategy(model)._preprocess_document()

    assert model.total == PAGES                                 # results are durable; only the lock needed Redis
