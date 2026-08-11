"""Unit tests for org-scoped indexing queue backlog / ETA snapshot."""

from unittest.mock import AsyncMock

import pytest

from app.connectors.services import indexing_queue as indexing_queue_mod
from app.connectors.services.indexing_queue import (
    clear_indexing_queue_snapshot_cache,
    fetch_indexing_queue_snapshot,
)


ORG_ID = "org-1"


@pytest.fixture(autouse=True)
def _clear_snapshot_cache() -> None:
    clear_indexing_queue_snapshot_cache()
    yield
    clear_indexing_queue_snapshot_cache()


def _run_hash(
    *,
    phase: str = "INDEXING",
    discovered: int = 0,
    indexed: int = 0,
    failed: int = 0,
    skipped: int = 0,
    total: int = 0,
    heartbeat_offset_ms: int = 0,
) -> dict[str, str]:
    import time

    now_ms = int(time.time() * 1000)
    return {
        "phase": phase,
        "discovered": str(discovered),
        "indexed": str(indexed),
        "failed": str(failed),
        "skipped": str(skipped),
        "total": str(total),
        "heartbeatAt": str(now_ms - heartbeat_offset_ms),
    }


@pytest.mark.asyncio
async def test_snapshot_sums_org_backlog_across_connectors() -> None:
    keys = [
        f"connector_sync_progress:{ORG_ID}:c1",
        f"connector_sync_progress:{ORG_ID}:c2",
        f"connector_sync_progress:{ORG_ID}:c3",
        f"connector_sync_progress:{ORG_ID}:c1:outcomes:run-a",
    ]
    run_data = {
        keys[0]: _run_hash(phase="INDEXING", total=100, indexed=40),
        keys[1]: _run_hash(phase="DISCOVERING", discovered=25),
        keys[2]: _run_hash(phase="IDLE", total=50, indexed=50),
    }
    redis = AsyncMock()

    async def scan(cursor, match=None, count=100):  # noqa: ARG001
        return 0, keys

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        return run_data.get(key, {})

    redis.scan = AsyncMock(side_effect=scan)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    assert snap is not None
    # c1: 60 remaining + c2: 25 remaining; outcomes key skipped; idle contributes 0
    assert snap["lag"] == 85
    assert snap["pending"] == 0
    assert snap["etaSeconds"] is None


@pytest.mark.asyncio
async def test_snapshot_estimates_eta_from_org_drain_rate() -> None:
    import time

    keys = [f"connector_sync_progress:{ORG_ID}:c1"]
    redis = AsyncMock()

    async def scan(cursor, match=None, count=100):  # noqa: ARG001
        return 0, keys

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {"lag": "2000", "at": str(time.time() - 10)}
        return _run_hash(phase="INDEXING", total=1000, indexed=0)

    redis.scan = AsyncMock(side_effect=scan)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    assert snap is not None
    assert snap["lag"] == 1000
    # Previous sample: 2000 lag, 10s ago → drain 100/s → ETA 10s
    assert snap["etaSeconds"] == 10


@pytest.mark.asyncio
async def test_snapshot_returns_none_without_client_or_org() -> None:
    assert await fetch_indexing_queue_snapshot(None, ORG_ID) is None
    assert await fetch_indexing_queue_snapshot(AsyncMock(), "") is None


@pytest.mark.asyncio
async def test_snapshot_returns_none_when_scan_fails() -> None:
    redis = AsyncMock()
    redis.scan = AsyncMock(side_effect=RuntimeError("redis down"))
    assert await fetch_indexing_queue_snapshot(redis, ORG_ID) is None


@pytest.mark.asyncio
async def test_snapshot_reuses_cache_within_ttl() -> None:
    keys = [f"connector_sync_progress:{ORG_ID}:c1"]
    redis = AsyncMock()
    scan_mock = AsyncMock(side_effect=lambda *a, **k: (0, keys))
    redis.scan = scan_mock

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        return _run_hash(phase="INDEXING", total=50, indexed=0)

    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    first = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    second = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    assert first == second
    assert scan_mock.await_count == 1


@pytest.mark.asyncio
async def test_snapshot_cache_is_org_scoped() -> None:
    redis = AsyncMock()

    async def scan(cursor, match=None, count=100):  # noqa: ARG001
        org = (match or "").split(":")[1]
        return 0, [f"connector_sync_progress:{org}:c1"]

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        if ":org-a:" in key:
            return _run_hash(phase="INDEXING", total=10, indexed=0)
        return _run_hash(phase="INDEXING", total=99, indexed=0)

    redis.scan = AsyncMock(side_effect=scan)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    a = await fetch_indexing_queue_snapshot(redis, "org-a")
    b = await fetch_indexing_queue_snapshot(redis, "org-b")
    assert a is not None and b is not None
    assert a["lag"] == 10
    assert b["lag"] == 99
    assert "org-a" in indexing_queue_mod._snapshot_cache
    assert "org-b" in indexing_queue_mod._snapshot_cache


@pytest.mark.asyncio
async def test_stale_runs_do_not_count_toward_backlog() -> None:
    keys = [f"connector_sync_progress:{ORG_ID}:c1"]
    redis = AsyncMock()

    async def scan(cursor, match=None, count=100):  # noqa: ARG001
        return 0, keys

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        # Older than STALE_THRESHOLD_MS (30m)
        return _run_hash(
            phase="INDEXING",
            total=100,
            indexed=0,
            heartbeat_offset_ms=31 * 60 * 1000,
        )

    redis.scan = AsyncMock(side_effect=scan)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    assert snap is not None
    assert snap["lag"] == 0
