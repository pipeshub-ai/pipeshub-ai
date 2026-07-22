"""Unit tests for indexing queue lag / ETA snapshot."""

from unittest.mock import AsyncMock

import pytest

from app.connectors.services.indexing_queue import (
    RECORDS_CONSUMER_GROUP,
    fetch_indexing_queue_snapshot,
)


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
