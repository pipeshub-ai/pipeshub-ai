"""One contract, every `IDistributedLeaseManager` transport (Phase 6).

`DistributedConcurrencyManager` is the only implementation today, but the
assertions here run against it wired to both a standalone Redis double and a
`FakeClusterRedis` double sharing the exact same pool names -- some of which
(see `_DIFFERENT_SLOT_POOLS` below) hash to different cluster slots. A
regression that reintroduces a multi-key `MGET`/`EVAL` across pools (R1/R6)
fails only the cluster parametrization; a regression in the acquire/renew/
release semantics themselves fails both, which is the point of sharing one
assertion body across transports rather than duplicating it per test file.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import DistributedConcurrencyManager
from app.services.redis.standalone_provider import StandaloneRedisProvider
from tests.support.fake_cluster_redis import FakeClusterRedis

fakeredis_aioredis = pytest.importorskip("fakeredis.aioredis")

# Pool names chosen so the cluster parametrization actually spans slots --
# see `test_distributed_concurrency.py` for the `redis.crc.key_slot` values.
_DIFFERENT_SLOT_POOLS = ("indexing", "parsing:light")


def _standalone_client() -> "fakeredis_aioredis.FakeRedis":
    return fakeredis_aioredis.FakeRedis(decode_responses=True)


def _cluster_client() -> FakeClusterRedis:
    return FakeClusterRedis()


TRANSPORTS = [
    pytest.param(_standalone_client, id="standalone"),
    pytest.param(_cluster_client, id="cluster"),
]


async def _make_manager(make_client) -> DistributedConcurrencyManager:
    client = make_client()
    with (
        patch.object(StandaloneRedisProvider, "create_client", lambda self, *a, **k: client),
        patch.object(StandaloneRedisProvider, "get_client", lambda self: client),
    ):
        manager = DistributedConcurrencyManager(
            MagicMock(), RedisConfig(host="redis", port=6379)
        )
        await manager.initialize()
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("make_client", TRANSPORTS)
class TestLeaseManagerContract:
    async def test_acquire_then_release_frees_the_slot_for_another_owner(
        self, make_client
    ) -> None:
        manager = await _make_manager(make_client)
        try:
            pool, limit = _DIFFERENT_SLOT_POOLS[0], 1
            assert await manager.try_acquire(pool, "owner-a", limit, 60) is True
            assert await manager.try_acquire(pool, "owner-b", limit, 60) is False

            await manager.release(pool, "owner-a")

            assert await manager.try_acquire(pool, "owner-b", limit, 60) is True
        finally:
            await manager.cleanup()

    async def test_acquire_is_idempotent_for_the_same_owner(self, make_client) -> None:
        """Re-acquiring under the same owner refreshes the lease rather than
        counting twice against the limit -- a retried request must not lock
        itself out."""
        manager = await _make_manager(make_client)
        try:
            pool, limit = _DIFFERENT_SLOT_POOLS[0], 1
            assert await manager.try_acquire(pool, "owner-a", limit, 60) is True
            assert await manager.try_acquire(pool, "owner-a", limit, 60) is True
        finally:
            await manager.cleanup()

    async def test_renew_extends_a_held_lease_but_not_an_unheld_one(
        self, make_client
    ) -> None:
        manager = await _make_manager(make_client)
        try:
            pool = _DIFFERENT_SLOT_POOLS[0]
            await manager.try_acquire(pool, "owner-a", 4, 60)

            assert await manager.renew(pool, "owner-a", 120) is True
            assert await manager.renew(pool, "owner-never-held", 120) is False
        finally:
            await manager.cleanup()

    async def test_renew_many_under_contention_across_pools_in_different_slots(
        self, make_client
    ) -> None:
        """Two owners contend on two pools that hash to different slots --
        the scenario a real cluster/MemoryDB CROSSSLOTs on if the lease
        manager ever issues one multi-key command across them."""
        manager = await _make_manager(make_client)
        try:
            pool_a, pool_b = _DIFFERENT_SLOT_POOLS
            assert await manager.try_acquire(pool_a, "owner-a", 1, 60) is True
            assert await manager.try_acquire(pool_a, "owner-b", 1, 60) is False
            assert await manager.try_acquire(pool_b, "owner-a", 4, 60) is True

            results = await manager.renew_many(
                [(pool_a, "owner-a"), (pool_b, "owner-a"), (pool_a, "owner-b")], 120
            )

            assert results[(pool_a, "owner-a")] is True
            assert results[(pool_b, "owner-a")] is True
            # owner-b never held pool_a's lease -- reported lost, not renewed.
            assert results[(pool_a, "owner-b")] is False
        finally:
            await manager.cleanup()
