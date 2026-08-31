import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import BlockingConnectionPool

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
    DistributedLeaseSet,
)
from app.services.messaging.redis_client import RedisClientRegistry

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


class _StubRegistry:
    """Stands in for RedisClientRegistry, handing out one AsyncMock client.

    The real registry keys clients by event loop; these tests never leave one
    loop, so a single client is an accurate stand-in and keeps assertions on
    ``manager.redis`` straightforward.
    """

    def __init__(self) -> None:
        self.redis = AsyncMock()
        self.closed = 0

    def client(self) -> AsyncMock:
        return self.redis

    async def aclose(self) -> None:
        self.closed += 1
        await self.redis.aclose()


@pytest.fixture
def manager() -> DistributedConcurrencyManager:
    instance = DistributedConcurrencyManager(
        MagicMock(),
        RedisConfig(host="redis", port=6379),
    )
    # initialize() normally builds the registry and pings; stub it so these
    # tests skip the real connection.
    registry = _StubRegistry()
    instance._registry = registry
    instance._acquire_script = AsyncMock()
    instance._renew_script = AsyncMock()
    # Keyed by the client object itself (a WeakKeyDictionary), so a discarded
    # client cannot have its address reused by a fresh one.
    instance._scripts_by_client[registry.redis] = (
        instance._acquire_script,
        instance._renew_script,
    )
    return instance


@pytest.mark.asyncio
async def test_try_acquire_returns_redis_decision(manager) -> None:
    manager._acquire_script.return_value = 1

    assert await manager.try_acquire("indexing", "worker-1", 10, 120) is True

    kwargs = manager._acquire_script.await_args.kwargs
    assert kwargs["keys"] == ["pipeshub:indexing:concurrency:indexing"]
    owner, lease_ms, limit, key_ttl_ms = kwargs["args"]
    assert owner == "worker-1"
    assert limit == 10
    assert key_ttl_ms == lease_ms * 2


@pytest.mark.asyncio
async def test_try_acquire_rejects_when_pool_is_full(manager) -> None:
    manager._acquire_script.return_value = 0

    assert await manager.try_acquire("parsing", "worker-2", 5, 120) is False


@pytest.mark.asyncio
async def test_renew_reports_lost_lease(manager) -> None:
    manager._renew_script.return_value = 0

    assert await manager.renew("indexing", "worker-1", 120) is False


@pytest.mark.asyncio
async def test_release_removes_only_owner_lease(manager) -> None:
    await manager.release("indexing", "worker-1")

    manager._registry.redis.zrem.assert_awaited_once_with(
        "pipeshub:indexing:concurrency:indexing", "worker-1"
    )


@pytest.mark.asyncio
async def test_cleanup_closes_owned_client(manager) -> None:
    registry = manager._registry

    await manager.cleanup()
    await manager.cleanup()

    assert registry.closed == 1
    assert manager._registry is None


def test_lease_set_tracks_thread_safe_snapshot() -> None:
    leases = DistributedLeaseSet()

    leases.add("indexing", "worker-1")
    assert leases.snapshot() == [("indexing", "worker-1")]
    assert leases.discard("indexing") == "worker-1"
    assert leases.snapshot() == []


@pytest.mark.asyncio
async def test_lua_scripts_against_fakeredis_acquire_expiry_limit() -> None:
    """Exercise the real acquire/renew/release Lua scripts (not mocks)
    against a fake-but-real Redis to catch script bugs the mocked tests
    above can't (e.g. bad KEYS/ARGV indexing, TIME() math)."""
    with patch.object(
        RedisClientRegistry,
        "_build_client",
        lambda self: fakeredis_aioredis.FakeRedis(decode_responses=True),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
        try:
            # Limit is 2: first two owners acquire, a third is rejected.
            assert await manager.try_acquire("pool", "a", 2, 60) is True
            assert await manager.try_acquire("pool", "b", 2, 60) is True
            assert await manager.try_acquire("pool", "c", 2, 60) is False

            # Re-acquiring for an owner already in the set renews it in
            # place rather than counting against the limit again.
            assert await manager.try_acquire("pool", "a", 2, 60) is True

            # renew() succeeds for a held lease, fails for one never held.
            assert await manager.renew("pool", "a", 60) is True
            assert await manager.renew("pool", "z", 60) is False

            # release() frees the slot for a subsequent acquire.
            await manager.release("pool", "b")
            assert await manager.try_acquire("pool", "c", 2, 60) is True

            # A lease with a near-zero TTL expires and is reaped by the
            # ZREMRANGEBYSCORE at the top of the acquire script, freeing
            # its slot even without an explicit release.
            assert await manager.try_acquire("short-pool", "x", 1, 0.05) is True
            await asyncio.sleep(0.15)
            assert await manager.try_acquire("short-pool", "y", 1, 60) is True
        finally:
            await manager.cleanup()


@pytest.mark.asyncio
async def test_initialize_bounds_redis_socket_operations() -> None:
    """The socket timeout and the pool size are the two bounds that stop a
    stalled caller from turning into a Redis outage: the timeout has to fire
    before any caller's own deadline (so redis-py raises instead of being
    cancelled mid-command, which forces the connection closed), and the pool
    has to be finite (redis-py defaults to 2**31, so every closed connection
    was replaced by a fresh TCP connect)."""
    built: list[BlockingConnectionPool] = []

    def _capture(self: RedisClientRegistry) -> AsyncMock:
        built.append(
            BlockingConnectionPool(
                host=self._config.host,
                port=self._config.port,
                max_connections=self._max_connections,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
            )
        )
        client = AsyncMock()
        client.ping = AsyncMock()
        return client

    with patch.object(RedisClientRegistry, "_build_client", _capture):
        manager = DistributedConcurrencyManager(
            MagicMock(),
            RedisConfig(host="redis", port=6379),
            operation_timeout_seconds=2.5,
            max_connections=17,
        )
        await manager.initialize()

    assert len(built) == 1
    pool = built[0]
    assert pool.max_connections == 17
    assert pool.connection_kwargs["socket_timeout"] == 2.5
    assert pool.connection_kwargs["socket_connect_timeout"] == 2.5


@pytest.mark.asyncio
async def test_renew_many_uses_one_round_trip_for_every_lease() -> None:
    """The renewal loop used to run per-message, so N in-flight records meant
    N background tasks each issuing their own renew every interval. One
    pipelined round trip keeps Redis load flat as the pipeline fills."""
    with patch.object(
        RedisClientRegistry,
        "_build_client",
        lambda self: fakeredis_aioredis.FakeRedis(decode_responses=True),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
        try:
            leases = [("indexing", "w1"), ("parsing:light", "w2")]
            for pool, owner in leases:
                assert await manager.try_acquire(pool, owner, 4, 60) is True

            results = await manager.renew_many([*leases, ("indexing", "never")], 60)

            assert results[("indexing", "w1")] is True
            assert results[("parsing:light", "w2")] is True
            # An owner that never held the lease reports lost, not renewed.
            assert results[("indexing", "never")] is False
            assert await manager.renew_many([], 60) == {}
        finally:
            await manager.cleanup()
