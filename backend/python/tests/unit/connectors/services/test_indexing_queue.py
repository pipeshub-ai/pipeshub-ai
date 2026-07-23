"""Unit tests for indexing queue lag / ETA snapshot."""

from unittest.mock import AsyncMock

import pytest

from app.connectors.services import indexing_queue as indexing_queue_mod
from app.connectors.services.indexing_queue import (
    RECORDS_CONSUMER_GROUP,
    clear_indexing_queue_snapshot_cache,
    fetch_indexing_queue_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> None:
    # Module-level TTL cache otherwise leaks lag/ETA across tests in the same
    # process (the failure mode seen in CI when the suite runs back-to-back).
    clear_indexing_queue_snapshot_cache()
    yield
    clear_indexing_queue_snapshot_cache()


@pytest.mark.asyncio
async def test_snapshot_reads_lag_and_pending() -> None:
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        return_value=[
            {"name": RECORDS_CONSUMER_GROUP, "lag": 3100, "pending": 40},
        ]
    )
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis)
    assert snap is not None
    assert snap["lag"] == 3100
    assert snap["pending"] == 40
    assert snap["etaSeconds"] is None


@pytest.mark.asyncio
async def test_snapshot_estimates_eta_from_drain_rate() -> None:
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        return_value=[{"name": RECORDS_CONSUMER_GROUP, "lag": 1000, "pending": 10}]
    )
    # Previous sample: 2000 lag, 10s ago → drain 100/s → ETA 10s
    redis.hgetall = AsyncMock(return_value={"lag": "2000", "at": str(__import__("time").time() - 10)})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis)
    assert snap is not None
    assert snap["etaSeconds"] == 10


@pytest.mark.asyncio
async def test_snapshot_returns_none_when_redis_unavailable() -> None:
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(side_effect=RuntimeError("no streams"))
    assert await fetch_indexing_queue_snapshot(redis) is None


@pytest.mark.asyncio
async def test_snapshot_returns_none_without_client() -> None:
    assert await fetch_indexing_queue_snapshot(None) is None


@pytest.mark.asyncio
async def test_snapshot_reuses_cache_within_ttl() -> None:
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        return_value=[{"name": RECORDS_CONSUMER_GROUP, "lag": 50, "pending": 1}]
    )
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    first = await fetch_indexing_queue_snapshot(redis)
    second = await fetch_indexing_queue_snapshot(redis)
    assert first == second
    assert redis.xinfo_groups.await_count == 1


@pytest.mark.asyncio
async def test_snapshot_cache_miss_after_clear() -> None:
    redis = AsyncMock()
    redis.xinfo_groups = AsyncMock(
        side_effect=[
            [{"name": RECORDS_CONSUMER_GROUP, "lag": 10, "pending": 0}],
            [{"name": RECORDS_CONSUMER_GROUP, "lag": 20, "pending": 0}],
        ]
    )
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    first = await fetch_indexing_queue_snapshot(redis)
    clear_indexing_queue_snapshot_cache()
    second = await fetch_indexing_queue_snapshot(redis)
    assert first is not None and second is not None
    assert first["lag"] == 10
    assert second["lag"] == 20
    assert indexing_queue_mod._snapshot_cache is not None
