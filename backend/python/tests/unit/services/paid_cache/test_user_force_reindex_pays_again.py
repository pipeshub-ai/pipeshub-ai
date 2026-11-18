"""D-180: with stored results present, a user's Force reindex makes every paid call again; a plain reindex makes none."""
from __future__ import annotations

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import MemoryResultStore, record_in_flight

ORG, REC = "org-1", "rec-1"
SITES = ("ocr_page", "image_description", "table_summary", "classify", "entities")   # parse, VLM, tables, classify, entities
KEYS = [CacheKey(ORG, REC, site, 1, "m", "p", "q", f"i{n}") for n, site in enumerate(SITES)]


@pytest.fixture(autouse=True)
def _no_run() -> None:
    clear_run()
    yield
    clear_run()


async def _run(cache: PaidResultCache, *, refresh: bool, content_rev: str = "rev-1") -> int:
    """One indexing run over every paid site; returns how many calls were actually paid."""
    paid = 0
    begin_run(ORG, REC, content_rev, "index", refresh=refresh)
    for key in KEYS:
        async def compute() -> str:
            nonlocal paid
            paid += 1
            return "answer"

        await cache.get_or_compute(key, compute)
    clear_run()
    return paid


async def test_a_plain_reindex_reuses_and_a_forced_one_pays_every_call_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_CACHE_WRITE", "on")
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    with record_in_flight(ORG, REC):
        assert await _run(cache, refresh=False) == len(KEYS)            # the first index pays and stores
        assert await _run(cache, refresh=False) == 0                    # a plain reindex reuses all of it
        assert await _run(cache, refresh=True) == len(KEYS)             # a user's Force reindex pays every call again
        assert await _run(cache, refresh=False) == 0                    # and what it stored is reused afterwards
