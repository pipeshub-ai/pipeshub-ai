"""The single-flight lock on a real Redis: one holder, renewal, release by token only, and a lapse after a crash."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import RedisLocks, lock_name
from tests.support.paid_cache import MemoryResultStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]


@pytest.fixture
def org() -> str:
    """An org of this test alone: parallel workers share one Redis, so no test touches another's locks."""
    return f"org-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def redis(redis_available: tuple[str, int], org: str) -> AsyncIterator[Redis]:  # type: ignore[no-untyped-def]
    from redis.asyncio import Redis

    host, port = redis_available
    client = Redis(host=host, port=port, decode_responses=True)
    yield client
    for key in await client.keys(f"pc:lock:{{{org}:*"):
        await client.delete(key)
    await client.aclose()


async def test_one_holder_at_a_time_and_release_only_by_its_token(redis, org) -> None:  # type: ignore[no-untyped-def]
    locks = RedisLocks(redis)
    name = lock_name(org, "rec-1", "d" * 64)

    token = await locks.acquire(name, 5000)
    assert token is not None
    assert await locks.acquire(name, 5000) is None          # another caller loses
    await locks.release(name, "not-the-token")
    assert await locks.acquire(name, 5000) is None          # a wrong token releases nothing
    assert await locks.renew(name, token, 5000) is True
    await locks.release(name, token)
    assert await locks.acquire(name, 5000) is not None


async def test_a_crashed_holders_lock_lapses(redis, org) -> None:  # type: ignore[no-untyped-def]
    locks = RedisLocks(redis)
    name = lock_name(org, "rec-1", "e" * 64)
    assert await locks.acquire(name, 200) is not None       # the holder dies without releasing

    assert await locks.acquire(name, 200) is None
    await asyncio.sleep(0.3)
    assert await locks.acquire(name, 5000) is not None


async def test_two_replicas_on_one_redis_pay_once(redis, org) -> None:  # type: ignore[no-untyped-def]
    store = MemoryResultStore()
    first, second = PaidResultCache(store, RedisLocks(redis)), PaidResultCache(store, RedisLocks(redis))
    key = CacheKey(org, "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    paid = 0

    async def compute() -> str:
        nonlocal paid
        paid += 1
        await asyncio.sleep(0.5)
        return "once"

    results = await asyncio.gather(first.get_or_compute(key, compute), second.get_or_compute(key, compute))

    assert results == ["once", "once"]
    assert paid == 1
    assert await redis.keys(f"pc:lock:{{{org}:*") == []     # this test's locks are released


async def test_the_lock_and_delete_marker_keys_carry_the_namespace_on_a_real_redis(redis: Redis) -> None:  # type: ignore[no-untyped-def]
    from app.services.paid_cache.locks import deleted_name

    locks = RedisLocks(redis, "idxs-ns")
    name = lock_name("org-ns", "rec-ns", "d" * 64)
    token = await locks.acquire(name, 5000)
    await locks.mark(deleted_name("org-ns", "rec-ns"), 5000)

    assert token is not None
    assert await redis.exists(f"idxs-ns:{name}") == 1 and await redis.exists(name) == 0
    assert await locks.is_marked(deleted_name("org-ns", "rec-ns")) is True
    assert await RedisLocks(redis, "other").is_marked(deleted_name("org-ns", "rec-ns")) is False
    await locks.release(name, token)
    await redis.delete("idxs-ns:pc:deleted:{org-ns:rec-ns}")


async def test_ownership_is_one_compare_on_the_token_and_a_stolen_lock_is_not_stored(redis, org, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    locks = RedisLocks(redis)
    name = lock_name(org, "rec-2", "f" * 64)
    token = await locks.acquire(name, 5000)
    assert token is not None
    assert await locks.owns(name, token) is True
    assert await locks.owns(name, "not-the-token") is False
    await redis.set(name, "another-payer", px=5000)         # Redis lost the lock and another payer took it
    assert await locks.owns(name, token) is False
    await redis.delete(name)

    store = MemoryResultStore()
    cache = PaidResultCache(store, locks)
    key = CacheKey(org, "rec-2", "ocr_page", 1, "m", "p", "q", "i")

    async def compute() -> str:
        await redis.set(lock_name(key.org_id, key.record_id, key.digest()), "another-payer", px=5000)
        return "text"

    assert await cache.get_or_compute(key, compute) == "text"
    assert store.results(org, "rec-2") == []
