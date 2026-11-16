"""A caller that shares another's in-flight work records the key, and a refresh never takes the object it replaces."""
from __future__ import annotations

import asyncio

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import begin_run, clear_run
from tests.support.paid_cache import MemoryResultStore, record_in_flight

ORG, REC = "org-1", "rec-1"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "POLL_FIRST_S", 0.01)
    monkeypatch.setattr(cache_module, "POLL_MAX_S", 0.02)


async def test_a_follower_of_an_in_flight_computation_lists_the_key_in_its_own_manifest() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    scopes = {}

    async def caller(name: str) -> str:
        scopes[name] = begin_run(ORG, REC, f"rev-{name}", "parse")

        async def compute() -> str:
            await asyncio.sleep(0.05)
            return "paid once"

        return await cache.get_or_compute(KEY, compute)

    with record_in_flight(ORG, REC):
        try:
            results = await asyncio.gather(caller("leader"), caller("follower"))
        finally:
            clear_run()

    assert results == ["paid once", "paid once"]
    assert KEY.relative_path() in scopes["leader"].keys
    assert KEY.relative_path() in scopes["follower"].keys, "the follower's run used the result but does not list it"


async def test_a_refresh_caller_that_waits_for_a_holder_gets_the_replacement_not_the_old_result() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    first, second = PaidResultCache(store, locks), PaidResultCache(store, locks)

    async def seed() -> str:
        return "old"

    with record_in_flight(ORG, REC):
        assert await first.get_or_compute(KEY, seed) == "old"

        async def holder() -> str:
            begin_run(ORG, REC, "rev-a", "parse", refresh=True)

            async def compute() -> str:
                await asyncio.sleep(0.15)
                return "new"

            return await first.get_or_compute(KEY, compute)

        async def waiter() -> str:
            await asyncio.sleep(0.03)                                    # the holder has the lock by now
            begin_run(ORG, REC, "rev-b", "parse", refresh=True)

            async def compute() -> str:
                return "recomputed"

            return await second.get_or_compute(KEY, compute)

        try:
            results = await asyncio.gather(holder(), waiter())
        finally:
            clear_run()

    assert results[0] == "new"
    assert results[1] in ("new", "recomputed"), "a refresh caller was handed the result it exists to replace"
    assert results[1] != "old"


async def test_deleting_a_record_named_like_the_org_purge_route_touches_nothing() -> None:
    """``DELETE .../paid-results/org`` is the whole-org purge, so a record id ``org`` is refused before the store."""
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())

    async def paid() -> str:
        return "kept"

    with record_in_flight(ORG, REC):
        await cache.get_or_compute(KEY, paid)
    assert store.results(ORG, REC)

    await cache.purge_record(ORG, "org")
    await cache.delete_record(ORG, "org")

    assert store.results(ORG, REC), "a delete of record 'org' reached the org's other records"
