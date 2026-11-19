"""The orphan stop on a real Redis: the delete marker carries the delete time, the guard reads it with single-key GETs, and a
marker rewritten for a live record stops nothing. A missing Redis fails the tests (the storage job of backend-matrix.yml starts one)."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import DELETED_TTL_MS, RedisLocks, deleted_name
from app.services.paid_cache.orphan import (
    DeliveryGuard,
    ProbeResult,
    RecordGone,
    bind_guard,
)
from tests.support.paid_cache import MemoryResultStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

# The storage job of backend-matrix.yml runs the files that read ARTIFACTS_IT_STORAGE_URL, and it starts the Redis these tests
# need (PAID_RESULTS_IT_REDIS_URL); these tests need no storage service themselves.
# Skipped where there is no storage job (the offline integration job has no Redis): same gate as the other paid-result ITs.
# In the storage job the variable is set, so a missing Redis there fails the tests instead.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    from redis.asyncio import Redis

    url = os.environ.get("PAID_RESULTS_IT_REDIS_URL")
    assert url, "set PAID_RESULTS_IT_REDIS_URL to a running Redis (the storage job starts one)"
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    yield client
    await client.aclose()


class Graph:
    def __init__(self) -> None:
        self.answer = ProbeResult(True, True, 1)
        self.reads = 0

    async def probe(self) -> ProbeResult:
        self.reads += 1
        return self.answer


async def test_a_real_delete_stops_the_run_and_a_redelivered_old_delete_does_not(redis: Redis) -> None:
    org, rec = f"org-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    locks = RedisLocks(redis, f"orphanit-{uuid.uuid4().hex[:6]}")
    cache = PaidResultCache(MemoryResultStore(), locks)
    graph = Graph()
    guard = DeliveryGuard(org_id=org, record_id=rec, probe=graph.probe, created=1, backend_of=cache._backend)
    guard.mark_read()
    bind_guard(guard)
    calls = 0

    async def paid() -> str:
        nonlocal calls
        calls += 1
        return "text"

    def key(i: int) -> CacheKey:
        return CacheKey(org, rec, "ocr_page", 1, "m", "p", "q", f"p{i}")

    await cache.get_or_compute(key(0), paid)
    # the earlier incarnation's delete arrives again while the record is live
    await cache.delete_record(org, rec)
    value = await locks.marker_value(deleted_name(org, rec))
    assert value is not None and value.isdigit() and len(value) >= 13                     # the delete time in ms
    await cache.get_or_compute(key(1), paid)
    assert (calls, graph.reads) == (2, 1)                                                 # one graph read for the change
    # the record is now really gone
    graph.answer = ProbeResult(found=False)
    await locks.mark(deleted_name(org, rec), DELETED_TTL_MS, str(int(value) + 1))
    with pytest.raises(RecordGone):
        await cache.get_or_compute(key(2), paid)
    assert calls == 2
    guard.finish()


async def test_the_two_markers_are_read_as_two_single_key_gets(redis: Redis) -> None:
    org, rec = f"org-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    locks = RedisLocks(redis, f"orphanit-{uuid.uuid4().hex[:6]}")
    cache = PaidResultCache(MemoryResultStore(), locks)
    assert await cache.read_markers(org, rec) == (None, None)
    await locks.mark(deleted_name(org), DELETED_TTL_MS, "777")
    await locks.mark(deleted_name(org, rec), DELETED_TTL_MS)                              # an older release's marker
    assert await cache.read_markers(org, rec) == ("1", "777")
    assert await locks.is_marked(deleted_name(org, rec))                                  # the fence's EXISTS still sees it
