"""The coalesced re-purge window on a real Redis (the storage job of backend-matrix.yml starts one). A missing Redis fails."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache import repurge_window
from app.services.paid_cache.locks import REPURGE_HOP_S
from app.services.paid_cache.repurge_window import (
    APPENDED,
    BUSY,
    CLAIMED,
    CLOSED,
    SEAL_OK,
    RedisWindowStore,
)
from tests.support.repurge_window import Broker, World, first_hop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

# The storage job of backend-matrix.yml runs the files that read ARTIFACTS_IT_STORAGE_URL, and it starts the Redis these tests
# need (PAID_RESULTS_IT_REDIS_URL). These tests need no storage service themselves, but they follow the other paid-result
# integration files: the offline integration job (no services) does not set the storage URL, so they are not run there; where
# the URL is set the Redis fixture asserts its URL, so a missing Redis fails loudly instead of skipping.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL (the storage job does)"),
]
NOW = 1_700_000_000_000


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    from redis.asyncio import Redis

    url = os.environ.get("PAID_RESULTS_IT_REDIS_URL")
    assert url, "set PAID_RESULTS_IT_REDIS_URL to a running Redis (the storage job starts one)"
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_RESULT_REPURGE_COALESCE", "true")


async def _gather(coros: list, limit: int = 40) -> list:
    gate = asyncio.Semaphore(limit)

    async def one(c):  # noqa: ANN202
        async with gate:
            return await c

    return await asyncio.gather(*(one(c) for c in coros))


def _store(redis: Redis) -> tuple[RedisWindowStore, str]:
    ns = f"g32it-{uuid.uuid4().hex[:8]}"
    return RedisWindowStore(redis, lambda name: f"{ns}:{name}"), f"org-{uuid.uuid4().hex[:6]}"


async def test_two_hundred_concurrent_deleters_have_one_claimant_and_every_id_lands_once(redis: Redis) -> None:
    store, org = _store(redis)
    first = await _gather([store.open_or_append(org, 5, f"t{i:04x}", f"e{i}", f"r{i}", NOW) for i in range(200)])
    assert sum(r.state == CLAIMED for r in first) == 1 and sum(r.state == BUSY for r in first) == 199
    winner = next(i for i, r in enumerate(first) if r.state == CLAIMED)
    assert await store.mark_published(org, 5, f"t{winner:04x}", f"e{winner}", NOW + 330_000, NOW)
    again = await _gather([store.open_or_append(org, 5, f"t{i:04x}", f"e{i}", f"r{i}", NOW) for i in range(200)])
    assert {r.state for r in again} == {APPENDED}
    read = await store.seal_read(org, 5, f"e{winner}", NOW + 400_000)
    assert read.state == SEAL_OK and sorted(read.ids) == sorted(f"r{i}" for i in range(200))
    assert (await store.open_or_append(org, 5, "late", "e", "r-late", NOW)).state == CLOSED


async def test_a_seal_reads_a_large_list_in_chunks_on_a_real_server(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repurge_window, "SEAL_READ_CHUNK", 1000)
    store, org = _store(redis)
    assert (await store.open_or_append(org, 5, "t", "e1", "seed", NOW)).state == CLAIMED
    assert await store.mark_published(org, 5, "t", "e1", NOW + 330_000, NOW)
    await _gather([store.open_or_append(org, 5, "z", "e", f"r{i}", NOW) for i in range(5000)])
    read = await store.seal_read(org, 5, "e1", NOW + 400_000)
    assert read.ids == tuple(f"r{i}" for i in range(5000)) or sorted(read.ids) == sorted(f"r{i}" for i in range(5000))


async def test_a_bulk_delete_of_2000_records_makes_four_pages_whose_hops_purge_every_record(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    store, org = _store(redis)
    broker = Broker()
    now = time.time()                                       # the claim's expiry is checked against the real clock
    for i in range(2000):
        assert await repurge_window.coalesce_successor(broker, org, first_hop(org, f"rec-{i}", now - REPURGE_HOP_S), store=store, now=now)
    seals = [m for m in broker.sent if m.type == "purgePaidResultsBatch"]
    assert len(seals) == 1
    broker.sent.clear()
    await repurge_window.handle_seal(broker, org, seals[0].payload, store=store, now=seals[0].payload["_retry_not_before"])
    pages = [m.payload["recordIds"] for m in broker.sent]
    assert [len(p) for p in pages] == [500] * 4 and sorted(i for p in pages for i in p) == sorted(f"rec-{i}" for i in range(2000))


async def test_the_property_world_loses_nothing_on_a_real_server(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_RESULT_REPURGE_CHAIN", "true")
    total_lost = acked = 0
    for seed in range(7301, 7311):
        result = await World(
            seed, rate=0.15, horizon=1800, outages=2, outage_s=240, p_crash=0.05, client=redis, namespace=f"g32it-{uuid.uuid4().hex[:8]}",
        ).run(monkeypatch)
        total_lost += result["lost"]
        acked += result["acked"]
    assert acked > 1000 and total_lost == 0
