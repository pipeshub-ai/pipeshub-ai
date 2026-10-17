"""`DistributedConcurrencyManager` against a real Redis Cluster.

Companion to `tests/unit/services/redis/test_lease_manager_contract.py`
(fake transport): pool names are chosen so `renew_many` genuinely spans
real cluster slots (verified via `provider.key_slot`), proving the
non-transactional per-key `EVALSHA` pipeline (R6) is cluster-safe against
a real `RedisCluster` client, not just `FakeClusterRedis`'s enforcement of
the CROSSSLOT rule.
"""
from __future__ import annotations

import logging

import pytest

from app.services.messaging.config import RedisConfig
from app.services.messaging.distributed_concurrency import DistributedConcurrencyManager
from app.services.redis.connection_provider_factory import get_redis_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _pools_in_different_slots(provider, prefix: str, count: int = 2) -> list[str]:
    """Pick `count` pool names whose full key (`{prefix}:{pool}`) lands on
    distinct slots on *this* cluster -- generated fresh per test since slot
    ownership only depends on the key string, not cluster topology."""
    pools: list[str] = []
    seen_slots: set[int] = set()
    candidate = 0
    while len(pools) < count:
        name = f"pool-{candidate}"
        slot = provider.key_slot(f"{prefix}:{name}")
        if slot not in seen_slots:
            seen_slots.add(slot)
            pools.append(name)
        candidate += 1
        if candidate > 1000:
            raise AssertionError("could not find pools in distinct slots")
    return pools


@pytest.fixture
async def manager(redis_cluster_available, unique_suffix):
    key_prefix = f"pipeshub:indexing:concurrency-it-{unique_suffix}"
    lease_manager = DistributedConcurrencyManager(
        logging.getLogger("it-lease-manager"),
        RedisConfig(host=redis_cluster_available.host, port=redis_cluster_available.port),
        key_prefix=key_prefix,
    )
    await lease_manager.initialize()
    yield lease_manager
    await lease_manager.cleanup()


class TestLeaseManagerOnARealCluster:
    async def test_acquire_renew_release_round_trip(self, manager) -> None:
        pool = "solo-pool"
        assert await manager.try_acquire(pool, "owner-a", 1, 60) is True
        assert await manager.renew(pool, "owner-a", 120) is True

        await manager.release(pool, "owner-a")

        assert await manager.try_acquire(pool, "owner-b", 1, 60) is True

    async def test_renew_many_across_real_cluster_slots(self, manager) -> None:
        provider = get_redis_provider(mode="cluster")
        pool_a, pool_b = _pools_in_different_slots(provider, manager.key_prefix)

        assert await manager.try_acquire(pool_a, "owner-a", 4, 60) is True
        assert await manager.try_acquire(pool_b, "owner-a", 4, 60) is True

        # Must not raise ClusterCrossSlotError against the real cluster.
        results = await manager.renew_many(
            [(pool_a, "owner-a"), (pool_b, "owner-a")], 120
        )

        assert results == {(pool_a, "owner-a"): True, (pool_b, "owner-a"): True}
