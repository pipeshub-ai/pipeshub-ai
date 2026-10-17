import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import BlockingConnectionPool

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
    DistributedLeaseSet,
)
from app.services.redis.standalone_provider import StandaloneRedisProvider
from tests.support.fake_cluster_redis import FakeClusterRedis

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
        self.provider = MagicMock()
        self.provider.load_script = AsyncMock(
            side_effect=["acquire-sha", "renew-sha"]
        )

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
    # initialize() normally builds the registry, pings, and loads the Lua
    # scripts; stub it so these tests skip the real connection and start
    # with both SHAs already "loaded" (R6).
    registry = _StubRegistry()
    instance._registry = registry
    instance._acquire_sha = "acquire-sha"
    instance._renew_sha = "renew-sha"
    return instance


@pytest.mark.asyncio
async def test_try_acquire_returns_redis_decision(manager) -> None:
    manager._registry.redis.evalsha = AsyncMock(return_value=1)

    assert await manager.try_acquire("indexing", "worker-1", 10, 120) is True

    args, _kwargs = manager._registry.redis.evalsha.await_args
    sha, numkeys, key, owner, lease_ms, limit, key_ttl_ms = args
    assert sha == "acquire-sha"
    assert numkeys == 1
    assert key == "pipeshub:indexing:concurrency:indexing"
    assert owner == "worker-1"
    assert limit == 10
    assert key_ttl_ms == lease_ms * 2


@pytest.mark.asyncio
async def test_try_acquire_rejects_when_pool_is_full(manager) -> None:
    manager._registry.redis.evalsha = AsyncMock(return_value=0)

    assert await manager.try_acquire("parsing", "worker-2", 5, 120) is False


@pytest.mark.asyncio
async def test_renew_reports_lost_lease(manager) -> None:
    manager._registry.redis.evalsha = AsyncMock(return_value=0)

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
    above can't (e.g. bad KEYS/ARGV indexing, TIME() math).

    ``create_client`` and ``get_client`` (used internally by
    ``load_script``) are pinned to the same fake instance so SCRIPT LOAD
    and EVALSHA see one shared keyspace, exactly as they would against one
    real Redis node.
    """
    fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: fake),
        patch.object(StandaloneRedisProvider, "get_client", lambda self: fake),
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
    real_create_client = StandaloneRedisProvider.create_client

    def _capture(self, options=None):
        client = real_create_client(self, options)
        built.append(client.connection_pool)
        client.ping = AsyncMock()
        return client

    with (
        patch.object(StandaloneRedisProvider, "create_client", _capture),
        patch.object(StandaloneRedisProvider, "load_script", AsyncMock(return_value="sha")),
    ):
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
async def test_initialize_picks_up_redis_key_namespace(monkeypatch) -> None:
    """REDIS_KEY_NAMESPACE (R9) is read from the provider once initialized
    and applied by `_key` -- never as a client-level prefix."""
    monkeypatch.setenv("REDIS_KEY_NAMESPACE", "tenant-a")
    client = AsyncMock()
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: client),
        patch.object(StandaloneRedisProvider, "load_script", AsyncMock(return_value="sha")),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
        try:
            assert manager._key("indexing") == "tenant-a:pipeshub:indexing:concurrency:indexing"
        finally:
            await manager.cleanup()


@pytest.mark.asyncio
async def test_renew_many_uses_one_round_trip_for_every_lease() -> None:
    """The renewal loop used to run per-message, so N in-flight records meant
    N background tasks each issuing their own renew every interval. One
    pipelined round trip keeps Redis load flat as the pipeline fills."""
    fake = fakeredis_aioredis.FakeRedis(decode_responses=True)
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: fake),
        patch.object(StandaloneRedisProvider, "get_client", lambda self: fake),
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


@pytest.mark.asyncio
async def test_renew_many_pools_in_different_slots_under_fake_cluster() -> None:
    """CROSSSLOT regression (R1/R6): `("indexing", ...)` and
    `("parsing:light", ...)` hash to different slots. A single multi-key
    EVALSHA across both would raise `ClusterCrossSlotError` on a real
    cluster/MemoryDB; the non-transactional per-key pipeline `renew_many`
    actually issues must not."""
    fake = FakeClusterRedis()
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: fake),
        patch.object(StandaloneRedisProvider, "get_client", lambda self: fake),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
        try:
            leases = [("indexing", "w1"), ("parsing:light", "w2")]
            for pool, owner in leases:
                assert await manager.try_acquire(pool, owner, 4, 60) is True

            # Must not raise ClusterCrossSlotError despite spanning slots.
            results = await manager.renew_many(leases, 60)

            assert results == {("indexing", "w1"): True, ("parsing:light", "w2"): True}
        finally:
            await manager.cleanup()


@pytest.mark.asyncio
async def test_renew_many_reloads_once_on_noscript_and_completes_map() -> None:
    """A `SCRIPT FLUSH` (or a freshly-joined cluster master) evicts the
    renew script between load and use; `renew_many` must reload once and
    retry rather than losing leases, and the returned map must have one
    entry per requested lease (`strict=True`) even after the retry."""
    fake = FakeClusterRedis()
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: fake),
        patch.object(StandaloneRedisProvider, "get_client", lambda self: fake),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
        try:
            leases = [("indexing", "w1"), ("parsing:light", "w2"), ("extraction", "w3")]
            for pool, owner in leases:
                assert await manager.try_acquire(pool, owner, 4, 60) is True

            # Simulate the renew script having been evicted server-side
            # without the manager's cached sha knowing about it yet.
            await fake.script_flush()

            results = await manager.renew_many(leases, 60)

            assert len(results) == len(leases)
            assert all(results.values())
        finally:
            await manager.cleanup()
