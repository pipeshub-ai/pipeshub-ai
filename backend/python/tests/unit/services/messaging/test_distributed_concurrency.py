import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
    DistributedLeaseSet,
)

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")


@pytest.fixture
def manager() -> DistributedConcurrencyManager:
    instance = DistributedConcurrencyManager(
        MagicMock(),
        RedisConfig(host="redis", port=6379),
    )
    instance._redis = AsyncMock()
    # initialize() normally does this via register_script(); tests set
    # _redis directly to skip the real connection, so wire these up too.
    instance._acquire_script = AsyncMock()
    instance._renew_script = AsyncMock()
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

    manager._redis.zrem.assert_awaited_once_with(
        "pipeshub:indexing:concurrency:indexing", "worker-1"
    )


@pytest.mark.asyncio
async def test_cleanup_closes_owned_client(manager) -> None:
    redis = manager._redis

    await manager.cleanup()
    await manager.cleanup()

    redis.aclose.assert_awaited_once()
    assert manager._redis is None


def test_lease_set_tracks_thread_safe_snapshot() -> None:
    leases = DistributedLeaseSet()

    leases.add("indexing", "worker-1")
    assert leases.owns("indexing", "worker-1") is True
    assert leases.snapshot() == [("indexing", "worker-1")]
    assert leases.discard("indexing") == "worker-1"
    assert leases.snapshot() == []


@pytest.mark.asyncio
async def test_lua_scripts_against_fakeredis_acquire_expiry_limit() -> None:
    """Exercise the real acquire/renew/release Lua scripts (not mocks)
    against a fake-but-real Redis to catch script bugs the mocked tests
    above can't (e.g. bad KEYS/ARGV indexing, TIME() math)."""
    with patch(
        "app.services.messaging.distributed_concurrency.Redis",
        new=fakeredis_aioredis.FakeRedis,
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
    with patch(
        "app.services.messaging.distributed_concurrency.Redis"
    ) as redis_cls:
        redis = redis_cls.return_value
        redis.ping = AsyncMock()
        manager = DistributedConcurrencyManager(
            MagicMock(),
            RedisConfig(host="redis", port=6379),
            operation_timeout_seconds=2.5,
        )

        await manager.initialize()

        redis_cls.assert_called_once_with(
            host="redis",
            port=6379,
            password=None,
            db=0,
            decode_responses=True,
            socket_timeout=2.5,
            socket_connect_timeout=2.5,
        )
