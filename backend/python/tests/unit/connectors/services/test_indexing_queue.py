"""Unit tests for org-scoped indexing queue backlog / ETA snapshot."""

from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from redis.commands.cluster import AsyncClusterDataAccessCommands

from app.connectors.services import indexing_queue as indexing_queue_mod
from app.connectors.services.indexing_queue import (
    clear_indexing_queue_snapshot_cache,
    fetch_indexing_queue_snapshot,
)
from app.connectors.services.sync_progress_store import (
    ConnectorSyncProgressStore,
    org_progress_key_pattern,
    progress_key,
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


def _scan_iter(keys_for: Callable[[str], list[str]]) -> MagicMock:
    """Stand-in for ``redis.scan_iter``: an async iterator over the matching keys."""

    async def _iter(match: str = "", count: int = 100) -> AsyncIterator[str]:
        for key in keys_for(match):
            yield key

    return MagicMock(side_effect=_iter)


@pytest.mark.asyncio
async def test_snapshot_sums_org_backlog_across_connectors() -> None:
    keys = [
        progress_key(ORG_ID, "c1"),
        progress_key(ORG_ID, "c2"),
        progress_key(ORG_ID, "c3"),
        f"{progress_key(ORG_ID, 'c1')}:outcomes:run-a",
    ]
    run_data = {
        keys[0]: _run_hash(phase="INDEXING", total=100, indexed=40),
        keys[1]: _run_hash(phase="DISCOVERING", discovered=25),
        keys[2]: _run_hash(phase="IDLE", total=50, indexed=50),
    }
    redis = AsyncMock()

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        return run_data.get(key, {})

    redis.scan_iter = _scan_iter(lambda _match: keys)
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

    keys = [progress_key(ORG_ID, "c1")]
    redis = AsyncMock()

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {"lag": "2000", "at": str(time.time() - 10)}
        return _run_hash(phase="INDEXING", total=1000, indexed=0)

    redis.scan_iter = _scan_iter(lambda _match: keys)
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
    redis.scan_iter = MagicMock(side_effect=RuntimeError("redis down"))
    assert await fetch_indexing_queue_snapshot(redis, ORG_ID) is None


@pytest.mark.asyncio
async def test_snapshot_reuses_cache_within_ttl() -> None:
    keys = [progress_key(ORG_ID, "c1")]
    redis = AsyncMock()
    scan_mock = _scan_iter(lambda _match: keys)
    redis.scan_iter = scan_mock

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
    assert scan_mock.call_count == 1


@pytest.mark.asyncio
async def test_snapshot_cache_is_org_scoped() -> None:
    redis = AsyncMock()

    async def hgetall(key: str):
        if key.startswith("indexing_queue:throughput_sample:"):
            return {}
        if key == progress_key("org-a", "c1"):
            return _run_hash(phase="INDEXING", total=10, indexed=0)
        return _run_hash(phase="INDEXING", total=99, indexed=0)

    keys_by_pattern = {
        org_progress_key_pattern(org): [progress_key(org, "c1")] for org in ("org-a", "org-b")
    }
    redis.scan_iter = _scan_iter(lambda match: keys_by_pattern[match])
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
    keys = [progress_key(ORG_ID, "c1")]
    redis = AsyncMock()

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

    redis.scan_iter = _scan_iter(lambda _match: keys)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)
    assert snap is not None
    assert snap["lag"] == 0


@pytest.mark.asyncio
async def test_standalone_redis_scan_covers_every_page() -> None:
    """Enough keys that SCAN needs several cursor round-trips."""
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    for i in range(250):
        await redis.hset(
            progress_key(ORG_ID, f"c{i}"),
            mapping=_run_hash(phase="INDEXING", total=2, indexed=0),
        )
    await redis.sadd(f"{progress_key(ORG_ID, 'c0')}:outcomes:run-a", "r1")
    await redis.hset(
        progress_key("other-org", "c1"),
        mapping=_run_hash(phase="INDEXING", total=1000, indexed=0),
    )

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)

    assert snap is not None
    assert snap["lag"] == 500
    await redis.aclose()


class _ClusterShapedRedis(AsyncClusterDataAccessCommands):
    """Mimics redis-py's async RedisCluster SCAN contract.

    ``scan()`` without ``target_nodes`` fans out to every primary and returns
    ``({node_name: cursor}, keys)``; a follow-up call targets one node. The
    real ``scan_iter`` from redis-py is inherited, not faked.
    """

    def __init__(self, pages_by_node: dict[str, list[list[str]]], hashes: dict[str, dict[str, str]]) -> None:
        self._pages = pages_by_node
        self._hashes = hashes

    def get_node(self, node_name: str) -> str:
        return node_name

    async def scan(
        self,
        cursor: int = 0,
        match: str | None = None,
        count: int | None = None,
        _type: str | None = None,
        target_nodes: str | None = None,
        **kwargs: object,
    ) -> tuple[dict[str, int], list[str]]:
        if not isinstance(cursor, int):
            # Real RedisCluster cannot encode a dict of per-node cursors.
            raise TypeError(f"Invalid input of type: '{type(cursor).__name__}'")
        nodes = [target_nodes] if target_nodes is not None else list(self._pages)
        cursors: dict[str, int] = {}
        keys: list[str] = []
        for node in nodes:
            pages = self._pages[node]
            keys.extend(pages[cursor])
            cursors[node] = cursor + 1 if cursor + 1 < len(pages) else 0
        return cursors, keys

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._hashes.get(key, {})

    async def hset(self, key: str, mapping: dict[str, object]) -> None:
        return None

    async def expire(self, key: str, seconds: int) -> None:
        return None


@pytest.mark.asyncio
async def test_cluster_redis_scan_merges_per_node_cursors() -> None:
    k = [progress_key(ORG_ID, f"c{i}") for i in range(3)]
    redis = _ClusterShapedRedis(
        pages_by_node={
            "node-a:6379": [[k[0]], [k[1], f"{k[1]}:outcomes:run-a"]],
            "node-b:6379": [[k[2]]],
        },
        hashes={key: _run_hash(phase="INDEXING", total=10, indexed=4) for key in k},
    )

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)

    assert snap is not None
    assert snap["lag"] == 18


@pytest.mark.asyncio
async def test_scan_finds_the_keys_the_progress_store_writes() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = ConnectorSyncProgressStore(MagicMock(), redis)
    for org_id, connector_id, discovered in ((ORG_ID, "c1", 7), (ORG_ID, "c2", 5), ("org-2", "c1", 100)):
        run_id = await store.start_run(org_id, connector_id)
        await store.add_discovered(org_id, connector_id, discovered, run_id=run_id)
        await store.record_result(
            org_id, connector_id, outcome="indexed", run_id=run_id, record_id="r1"
        )

    snap = await fetch_indexing_queue_snapshot(redis, ORG_ID)

    assert snap is not None
    # (7 - 1) + (5 - 1); the other org and the outcomes sets are not counted.
    assert snap["lag"] == 10
    await redis.aclose()
