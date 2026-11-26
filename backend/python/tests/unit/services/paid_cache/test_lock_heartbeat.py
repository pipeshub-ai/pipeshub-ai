"""A holder keeps its lock while the paid call runs, and stores only while it still owns it."""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks, lock_name
from tests.support.paid_cache import MemoryResultStore

KEY = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
NAME = lock_name(KEY.org_id, KEY.record_id, KEY.digest())
TTL_MS = 90


@pytest.fixture(autouse=True)
def _short_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", TTL_MS)


def _heartbeats() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "paid-cache-lock-renewal" and not t.done()]


async def test_a_call_longer_than_three_ttls_keeps_its_lock_and_stores_once() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    calls, stolen = [], []

    async def compute() -> str:
        calls.append(1)
        for _ in range(8):                                    # 8 x 50 ms = 400 ms, over 4 TTLs
            await asyncio.sleep(0.05)
            stolen.append(await locks.acquire(NAME, TTL_MS))  # a rival that would take a lapsed lock
        return "text"

    assert await cache.get_or_compute(KEY, compute) == "text"
    assert stolen == [None] * 8
    assert len(calls) == 1
    assert len(store.results("org-1", "rec-1")) == 1
    assert _heartbeats() == []


async def test_a_holder_whose_lock_was_stolen_does_not_store_and_is_counted_once(caplog: pytest.LogCaptureFixture) -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    calls = []

    async def compute() -> str:
        calls.append(1)
        locks._held[NAME] = ("someone-else", locks._clock() + 60)   # Redis lost the lock; another payer took it
        await asyncio.sleep(0.2)
        return "text"

    with caplog.at_level(logging.WARNING, logger=cache.logger.name):
        assert await cache.get_or_compute(KEY, compute) == "text"   # the caller still gets its result

    assert len(calls) == 1
    assert store.results("org-1", "rec-1") == []
    assert sum("was lost" in r.getMessage() for r in caplog.records) == 1
    assert locks._held[NAME][0] == "someone-else"                   # the other payer's lock is not released
    assert _heartbeats() == []


async def test_a_lapsed_lock_with_no_renewal_is_not_written_over() -> None:
    """The ownership check alone (the renewal has not noticed yet): the lock lapsed just before the write."""
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)

    async def compute() -> str:
        locks._held.pop(NAME)
        return "text"

    assert await cache.get_or_compute(KEY, compute) == "text"
    assert store.results("org-1", "rec-1") == []


async def test_the_timer_stops_on_error_and_on_cancel() -> None:
    cache = PaidResultCache(MemoryResultStore(), InProcessLocks())

    async def boom() -> str:
        await asyncio.sleep(0.01)
        raise ValueError("model failed")

    with pytest.raises(ValueError):
        await cache.get_or_compute(KEY, boom)
    assert _heartbeats() == []

    async def forever() -> str:
        await asyncio.sleep(30)
        return "x"

    task = asyncio.create_task(cache.get_or_compute(KEY, forever))
    for _ in range(200):                                            # bounded wait for the timer to start
        if _heartbeats():
            break
        await asyncio.sleep(0.01)
    assert len(_heartbeats()) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _heartbeats() == []


async def test_the_lock_is_renewed_about_every_third_of_its_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed renewals, not elapsed time: the call ends after a set number of renewals, however fast the loop is."""
    from app.services.paid_cache import cache as cache_module

    renewals: list[float] = []
    enough = asyncio.Event()

    class Counting(InProcessLocks):
        async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
            renewals.append(asyncio.get_running_loop().time())
            if len(renewals) >= 4:
                enough.set()
            return await super().renew(name, token, ttl_ms)

    cache = PaidResultCache(MemoryResultStore(), Counting())

    async def compute() -> str:
        await asyncio.wait_for(enough.wait(), 10)                   # runs until the 4th renewal was observed
        return "text"

    await cache.get_or_compute(KEY, compute)
    gaps = [b - a for a, b in zip(renewals, renewals[1:])]
    third = cache_module.LOCK_TTL_MS / 3000
    assert len(renewals) >= 4
    assert all(gap >= third * 0.5 for gap in gaps), gaps            # never faster than about a third of the TTL
