"""Real Redis and the real storage service (local disk, S3, Azure Blob): one payer per key, and the delayed re-purge.

Needs ``ARTIFACTS_IT_STORAGE_URL`` (see ``tests/support/paid_results_it.py``) and ``PAID_RESULTS_IT_REDIS_URL``
(the storage job of ``.github/workflows/backend-matrix.yml`` starts both). A missing Redis fails the tests.
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.core.org_context import OrgContext, bind
from app.services.messaging.config import IndexingEvent
from app.services.paid_cache import access, repurge
from app.services.paid_cache import cache as cache_module
from app.services.paid_cache import run as run_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import RedisLocks, lock_name
from app.services.paid_cache.run import RunScope, clear_run
from tests.support.paid_results_it import new_org
from tests.support.record_handler import make_record_handler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]
TRIALS = 42


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    from redis.asyncio import Redis

    url = os.environ.get("PAID_RESULTS_IT_REDIS_URL")
    assert url, "set PAID_RESULTS_IT_REDIS_URL to a running Redis (the storage job starts one)"
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    yield client
    await client.aclose()


@pytest.mark.parametrize("trial", range(TRIALS))
async def test_a_dead_holder_with_many_waiters_costs_one_paid_call(
    store: NodePaidResultStore, redis: Redis, trial: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "POLL_FIRST_S", 0.02)
    monkeypatch.setattr(cache_module, "POLL_MAX_S", 0.04)
    rng = random.Random(900 + trial)
    ns = f"s7it-{uuid.uuid4().hex[:8]}"
    waiters = rng.randrange(4, 9)
    replicas = [PaidResultCache(store, RedisLocks(redis, ns)) for _ in range(waiters)]
    org, rec = new_org(), str(uuid.uuid4())
    key = CacheKey(org, rec, "ocr_page", 1, "m", "p", "q", "i")
    # A holder that died: its lock lapses 400 ms from now.
    await redis.set(f"{ns}:" + lock_name(org, rec, key.digest()), "dead-holder", px=400)
    paid = 0

    async def compute() -> str:
        nonlocal paid
        paid += 1
        await asyncio.sleep(0.2)
        return "text"

    out = await asyncio.gather(*(c.get_or_compute(key, compute) for c in replicas))

    assert out == ["text"] * waiters
    assert paid == 1, f"trial {trial}: {paid} paid calls for {waiters} waiters after one lapse"
    await store.purge_record(org, rec)


async def test_a_late_write_after_redis_loses_the_fence_is_removed_by_the_delayed_repurge(
    store: NodePaidResultStore, redis: Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = f"s7it-{uuid.uuid4().hex[:8]}"
    org, rec = new_org(), str(uuid.uuid4())
    indexing = PaidResultCache(store, RedisLocks(redis, ns))
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: indexing)
    producer = AsyncMock()
    handler = make_record_handler(producer=producer)
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": rec, "orgId": org, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()

    async def deliver(event_type: str, payload: dict) -> list:
        with bind(OrgContext(org, None, "service", "envelope", "req-1")):
            return [e async for e in handler.process_event(event_type, payload) if e.event != IndexingEvent.START_DOWNLOAD]

    try:
        await deliver(EventTypes.DELETE_RECORD.value, {"recordId": rec, "orgId": org, "virtualRecordId": "vr"})
        assert await redis.exists(f"{ns}:pc:deleted:{{{org}:{rec}}}") == 1
        await redis.delete(f"{ns}:pc:deleted:{{{org}:{rec}}}")     # Redis restarted without persistence: the fence is gone

        parsing = PaidResultCache(store, RedisLocks(redis, ns))     # the orphaned parse finishes after the delete
        scope = RunScope(org, rec, "rev1", "parse")
        for page in range(4):
            key = CacheKey(org, rec, "ocr_page", 1, "m", "p", "q", f"p{page}")
            await scope.note(parsing.store, key.relative_path(), opens=True)

            async def compute() -> str:
                return "text"

            await parsing.get_or_compute(key, compute)
        await scope.finish(parsing.store)
        landed = len(await store.list_files(org, rec))

        sent = producer.send_event.await_args.kwargs                  # the event the delete published
        await deliver(sent["event_type"], sent["payload"])
        after = len(await store.list_files(org, rec))
        print(f"landed={landed} after_repurge={after}")
        assert landed == 5
        assert after == 0
    finally:
        clear_run()
        await store.purge_org(org)


async def test_after_a_delete_the_fence_refuses_late_writes_on_real_redis(store: NodePaidResultStore, redis: Redis) -> None:
    ns = f"s7it-{uuid.uuid4().hex[:8]}"
    org, rec = new_org(), str(uuid.uuid4())
    cache = PaidResultCache(store, RedisLocks(redis, ns))
    assert await cache.delete_record(org, rec) is True               # the marker went to the shared backend
    key = CacheKey(org, rec, "ocr_page", 1, "m", "p", "q", "i")

    async def compute() -> str:
        return "late"

    assert await cache.get_or_compute(key, compute) == "late"
    assert await store.list_files(org, rec) == []
    await store.purge_org(org)


@pytest.mark.parametrize("runs_for_s", [2400, 3000, 3600])
async def test_an_orphaned_parse_paying_after_a_delete_with_redis_flushed_leaves_nothing_on_the_real_store(
    store: NodePaidResultStore, redis: Redis, monkeypatch: pytest.MonkeyPatch, runs_for_s: int,
) -> None:
    """The delete, a flushed Redis, a parse that keeps paying pages for ``runs_for_s``, and the chain of hops.

    The write bound runs from the stage's start, so a page paid after the deadline is not stored and the run's
    manifests are not written either: nothing lands after the chain's last purge.
    """
    now = [1_000_000.0]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    ns = f"s7it-{uuid.uuid4().hex[:8]}"
    org, rec = new_org(), str(uuid.uuid4())
    indexing = PaidResultCache(store, RedisLocks(redis, ns))
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: indexing)
    producer = AsyncMock()
    handler = make_record_handler(producer=producer)
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": rec, "orgId": org, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()

    async def deliver(event_type: str, payload: dict) -> None:
        with bind(OrgContext(org, None, "service", "envelope", "req-1")):
            [e async for e in handler.process_event(event_type, payload) if e.event != IndexingEvent.START_DOWNLOAD]

    async def compute() -> str:
        return "text"

    try:
        start = now[0]
        await deliver(EventTypes.DELETE_RECORD.value, {"recordId": rec, "orgId": org, "virtualRecordId": "vr"})
        pending = [producer.send_event.await_args.kwargs["payload"]]
        for name in [k async for k in redis.scan_iter(match=f"{ns}:*")]:
            await redis.delete(name)                                # Redis restarted: marker and locks are gone
        parsing = PaidResultCache(store, RedisLocks(redis, ns))
        scope = RunScope(org, rec, "rev1", "parse", clock=lambda: now[0])
        run_module._run.set(scope)
        page, hops = 0, 0
        while now[0] - start < runs_for_s or pending:
            if now[0] - start < runs_for_s:
                now[0] += 200
                key = CacheKey(org, rec, "ocr_page", 1, "m", "p", "q", f"p{page}")
                await scope.note(parsing.store, key.relative_path(), opens=True)
                await parsing.get_or_compute(key, compute)
                page += 1
                if now[0] - start >= runs_for_s:
                    await scope.finish(parsing.store)
            else:
                now[0] = max(now[0], pending[0]["_retry_not_before"])
            while pending and pending[0]["_retry_not_before"] <= now[0]:
                due = pending.pop(0)
                producer.send_event.reset_mock()
                await deliver("purgePaidResults", due)
                hops += 1
                if producer.send_event.await_args is not None:
                    pending.append(producer.send_event.await_args.kwargs["payload"])
        assert page >= 12 and hops >= 6
        assert await store.list_files(org, rec) == []
    finally:
        clear_run()
        await store.purge_org(org)
