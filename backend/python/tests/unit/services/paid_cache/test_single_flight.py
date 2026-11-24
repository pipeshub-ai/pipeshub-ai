"""One compute per key, however many callers: in one process, across processes (locks), and after a crash."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import NotCached, PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks, lock_name
from tests.support.paid_cache import MemoryResultStore

KEY = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "POLL_FIRST_S", 0.01)
    monkeypatch.setattr(cache_module, "POLL_MAX_S", 0.02)


async def test_one_compute_per_key() -> None:
    cache = PaidResultCache(MemoryResultStore())
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "once"

    results = await asyncio.gather(*(cache.get_or_compute(KEY, compute) for _ in range(10)))

    assert results == ["once"] * 10
    assert calls == 1


async def test_ten_callers_of_ten_different_keys_compute_ten_times() -> None:
    cache = PaidResultCache(MemoryResultStore())
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "x"

    keys = [CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", f"page-{i}") for i in range(10)]
    await asyncio.gather(*(cache.get_or_compute(k, compute) for k in keys))
    assert calls == 10


async def test_two_processes_sharing_storage_and_locks_compute_once() -> None:
    # Two caches: two replicas of the indexing service. They share storage and the lock backend (Redis).
    store, locks = MemoryResultStore(), InProcessLocks()
    first, second = PaidResultCache(store, locks), PaidResultCache(store, locks)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return "paid"

    results = await asyncio.gather(first.get_or_compute(KEY, compute), second.get_or_compute(KEY, compute))

    assert results == ["paid", "paid"]
    assert calls == 1


async def test_a_dead_holders_lock_lapses_and_the_next_caller_computes() -> None:
    store, locks = MemoryResultStore(), InProcessLocks()
    # A holder that took the lock and died before storing anything.
    await locks.acquire(lock_name("org-1", "rec-1", KEY.digest()), 120_000)
    cache = PaidResultCache(store, locks)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return "recomputed"

    waiter = asyncio.create_task(cache.get_or_compute(KEY, compute, wait_s=5))
    await asyncio.sleep(0.05)
    assert calls == 0  # still waiting on the holder
    locks.expire_all()

    assert await asyncio.wait_for(waiter, 5) == "recomputed"
    assert calls == 1


async def test_a_holder_that_stops_renewing_costs_one_compute_when_its_lease_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wait is bounded by the holder's lease (a dead holder's lock lapses), not by a fixed ``wait_s``."""
    store, locks = MemoryResultStore(), InProcessLocks()
    now = [0.0]
    locks._clock = lambda: now[0]
    await locks.acquire(lock_name("org-1", "rec-1", KEY.digest()), 120_000)
    cache = PaidResultCache(store, locks)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return "after the lease"

    waiter = asyncio.create_task(cache.get_or_compute(KEY, compute, wait_s=0.05))
    await asyncio.sleep(0.2)                       # far past wait_s: the holder's lease is still running
    assert calls == 0
    now[0] = 121.0                                 # the lease ends
    assert await asyncio.wait_for(waiter, 5) == "after the lease"
    assert calls == 1


async def test_a_waiter_outlasts_wait_s_while_the_holder_is_alive_and_pays_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA-S7a-5: a rate-limited provider keeps the holder longer than any fixed wait; the waiter still pays 0."""
    store, locks = MemoryResultStore(), InProcessLocks()
    a, b = PaidResultCache(store, locks), PaidResultCache(store, locks)
    paid = 0

    async def slow() -> str:
        nonlocal paid
        paid += 1
        await asyncio.sleep(0.4)
        return "text"

    t1 = asyncio.create_task(a.get_or_compute(KEY, slow, wait_s=0.2))
    await asyncio.sleep(0.02)
    t2 = asyncio.create_task(b.get_or_compute(KEY, slow, wait_s=0.2))
    assert await asyncio.gather(t1, t2) == ["text", "text"]
    assert paid == 1


async def test_a_compute_returning_none_caches_nothing() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)

    async def nothing() -> None:
        return None

    assert await cache.get_or_compute(KEY, nothing) is None
    assert store.files == {}
    assert await cache.get_or_compute(KEY, nothing) is None
    assert store.ops["put"] == 0


async def test_a_failing_leader_lets_the_next_caller_lead() -> None:
    cache = PaidResultCache(MemoryResultStore())
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        if attempts == 1:
            raise RuntimeError("provider down")
        return "ok"

    results = await asyncio.gather(
        *(cache.get_or_compute(KEY, flaky) for _ in range(3)), return_exceptions=True,
    )

    assert isinstance(results[0], RuntimeError)
    assert results[1:] == ["ok", "ok"]
    assert attempts == 2


async def test_the_lock_is_released_after_the_compute_and_after_a_failure() -> None:
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)

    async def boom() -> str:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await cache.get_or_compute(KEY, boom)
    assert await locks.acquire(lock_name("org-1", "rec-1", KEY.digest()), 1000) is not None


async def test_the_lock_is_renewed_while_a_long_compute_runs() -> None:
    locks = InProcessLocks()
    cache = PaidResultCache(MemoryResultStore(), locks)
    renewals = 0
    original = locks.renew

    async def counting(name: str, token: str, ttl: int) -> bool:
        nonlocal renewals
        renewals += 1
        return await original(name, token, ttl)

    locks.renew = counting  # type: ignore[method-assign]

    async def slow() -> str:
        await asyncio.sleep(0.15)
        return "done"

    with patch.object(cache_module, "LOCK_TTL_MS", 30), patch("app.services.paid_cache.cache.LOCK_TTL_MS", 30):
        assert await cache.get_or_compute(KEY, slow) == "done"
    assert renewals >= 1


async def test_without_a_lock_service_the_call_is_still_made() -> None:
    class Broken:
        async def acquire(self, name: str, ttl_ms: int) -> str | None:
            raise ConnectionError("redis down")

        async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
            return False

        async def release(self, name: str, token: str) -> None:
            return None

    cache = PaidResultCache(MemoryResultStore(), Broken())

    async def compute() -> str:
        return "unlocked"

    assert await cache.get_or_compute(KEY, compute) == "unlocked"


async def test_ten_callers_compute_once_even_when_the_lock_service_is_down() -> None:
    class Broken:
        async def acquire(self, name: str, ttl_ms: int) -> str | None:
            raise ConnectionError("redis down")

        async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
            return False

        async def release(self, name: str, token: str) -> None:
            return None

    cache = PaidResultCache(MemoryResultStore(), Broken())
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "once"

    assert await asyncio.gather(*(cache.get_or_compute(KEY, compute) for _ in range(10))) == ["once"] * 10
    assert calls == 1                                     # callers of one process attach to the first one's call


async def test_a_storage_outage_is_a_miss_not_a_failure() -> None:
    store = MemoryResultStore()
    store.failing = {"get", "put", "replace"}
    cache = PaidResultCache(store)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return "still computed"

    assert await cache.get_or_compute(KEY, compute) == "still computed"
    assert calls == 1


async def test_not_cached_values_reach_every_waiting_caller_from_one_compute() -> None:
    cache = PaidResultCache(MemoryResultStore())
    calls = 0

    async def compute() -> object:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return NotCached("")

    assert await asyncio.gather(*(cache.get_or_compute(KEY, compute) for _ in range(5))) == [""] * 5
    assert calls == 1


def test_two_event_loops_sharing_a_cache_do_not_wait_on_each_others_futures() -> None:
    # The indexing service runs the server loop and the consumer's worker loop. A future made by one cannot be
    # awaited by the other, so each loop leads or follows its own callers and the lock and the object join them.
    import threading

    cache = PaidResultCache(MemoryResultStore(), InProcessLocks())
    calls = 0
    results: list[str] = []
    errors: list[BaseException] = []

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.15)
        return "paid"

    def run() -> None:
        try:
            results.append(asyncio.run(cache.get_or_compute(KEY, compute)))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    assert results == ["paid", "paid"]
    assert calls == 1                       # the second loop found the first one's object, or its lock
