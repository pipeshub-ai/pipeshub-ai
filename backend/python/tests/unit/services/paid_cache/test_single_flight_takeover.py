"""When a lock's holder dies with waiters, exactly one of them pays, and nobody pays without holding the lock."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Never

import pytest

from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks, lock_name
from tests.support.paid_cache import MemoryResultStore


class JitterStore(MemoryResultStore):
    """Every storage call takes a random 0-3 ms, as a network round trip does: other tasks run in between."""

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self._rng = rng

    async def get(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(self._rng.random() * 0.003)
        return await super().get(*a, **k)

    async def put(self, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(self._rng.random() * 0.003)
        return await super().put(*a, **k)


class WatchedLocks(InProcessLocks):
    """Lock calls yield for a random 0-2 ms and are logged, so a test can state which lock a compute ran under."""

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self._rng = rng
        self.log: list[tuple[str, str]] = []

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        await asyncio.sleep(self._rng.random() * 0.002)
        token = await super().acquire(name, ttl_ms)
        if token is not None:
            self.log.append(("acquired", token))
        return token

    async def release(self, name: str, token: str) -> None:
        await asyncio.sleep(self._rng.random() * 0.002)
        self.log.append(("released", token))
        await super().release(name, token)

    def holder(self, name: str) -> str | None:
        held = self._held.get(name)
        return held[0] if held is not None and held[1] > self._clock() else None


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "POLL_FIRST_S", 0.003)
    monkeypatch.setattr(cache_module, "POLL_MAX_S", 0.006)


@pytest.mark.parametrize("trial", range(40))
async def test_a_dead_holder_with_many_waiters_costs_one_paid_call_under_the_lock(trial: int) -> None:
    rng = random.Random(7_000 + trial)
    store, locks = JitterStore(rng), WatchedLocks(rng)
    waiters = rng.randrange(3, 8)
    replicas = [PaidResultCache(store, locks) for _ in range(waiters)]
    key = CacheKey("org-1", f"rec-{trial}", "ocr_page", 1, "m", "p", "q", "i")
    name = lock_name(key.org_id, key.record_id, key.digest())
    assert await locks.acquire(name, 120_000)            # a holder that died with its lock
    paid: list[str | None] = []

    async def compute() -> str:
        paid.append(locks.holder(name))                  # who holds the lock while this call is paid
        await asyncio.sleep(0.01 + rng.random() * 0.01)
        return "text"

    tasks = [asyncio.create_task(c.get_or_compute(key, compute)) for c in replicas]
    await asyncio.sleep(0.01 + rng.random() * 0.01)
    locks.expire_all()                                   # its lease ends
    assert await asyncio.gather(*tasks) == ["text"] * waiters

    assert len(paid) == 1, f"{len(paid)} paid calls for {waiters} waiters after one lapse"
    assert paid[0] is not None, "the paid call ran without holding the lock"


async def test_the_waiter_that_takes_a_lapsed_lock_keeps_it_until_it_has_computed() -> None:
    """The lock is never released between taking it over and computing (the gap a second waiter used to enter)."""
    rng = random.Random(1)
    store, locks = MemoryResultStore(), WatchedLocks(rng)
    cache = PaidResultCache(store, locks)
    key = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    name = lock_name(key.org_id, key.record_id, key.digest())
    await locks.acquire(name, 120_000)
    del locks.log[:]
    seen_at_compute: list[list[tuple[str, str]]] = []

    async def compute() -> str:
        seen_at_compute.append(list(locks.log))
        return "text"

    task = asyncio.create_task(cache.get_or_compute(key, compute))
    await asyncio.sleep(0.02)
    locks.expire_all()
    assert await task == "text"

    assert [kind for kind, _ in seen_at_compute[0]] == ["acquired"]   # taken over, not yet released
    assert [kind for kind, _ in locks.log] == ["acquired", "released"]


async def test_a_waiter_that_takes_over_finds_the_result_the_lapsed_holder_stored_and_does_not_pay() -> None:
    """The holder stored its object just before it lapsed: the takeover looks the key up again under the lock."""
    rng = random.Random(2)
    store, locks = MemoryResultStore(), WatchedLocks(rng)
    cache = PaidResultCache(store, locks)
    key = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    name = lock_name(key.org_id, key.record_id, key.digest())
    await locks.acquire(name, 120_000)
    paid = 0

    async def compute() -> str:
        nonlocal paid
        paid += 1
        return "mine"

    task = asyncio.create_task(cache.get_or_compute(key, compute))
    await asyncio.sleep(0.02)
    locks.expire_all()
    store.files[(key.org_id, key.record_id, key.relative_path())] = (
        b'{"v":1,"kind":"ocr_page","value":"theirs"}', 1,
    )
    assert await task == "theirs"
    assert paid == 0


async def test_with_no_lock_service_every_caller_computes_and_is_counted_not_blocked() -> None:
    """The designed fallback when Redis cannot be asked: compute without a lock (never hang)."""

    class Unreachable:
        shared = True

        async def acquire(self, name: str, ttl_ms: int) -> Never:
            raise ConnectionError("redis down")

        async def release(self, name: str, token: str) -> Never:
            raise AssertionError("nothing was acquired")

        async def renew(self, name: str, token: str, ttl_ms: int) -> Never:
            raise AssertionError("nothing was acquired")

        async def mark(self, name: str, ttl_ms: int) -> Never:
            raise ConnectionError("redis down")

        async def is_marked(self, name: str) -> Never:
            raise ConnectionError("redis down")

    store = MemoryResultStore()
    cache = PaidResultCache(store, Unreachable())  # type: ignore[arg-type]
    key = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")

    async def compute() -> str:
        return "text"

    assert await asyncio.wait_for(cache.get_or_compute(key, compute), 5) == "text"
    assert key.relative_path() in store.paths("org-1", "rec-1")
