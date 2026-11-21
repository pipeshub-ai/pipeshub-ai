"""`RedisDistributedKeyValueStore` against a real Redis Cluster.

Companion to `tests/unit/services/redis/test_kv_store_contract.py` (fake
transports): this is the same contract against `ClusterRedisProvider`
talking to real cluster nodes, so a `create_key(overwrite=False)` regression
that only breaks against a real `RedisCluster` client (e.g. a `SET NX`
option the client library maps differently in cluster mode) is caught here.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def store(redis_cluster_available, unique_suffix):
    kv_store = RedisDistributedKeyValueStore(
        serializer=lambda v: json.dumps(v).encode(),
        deserializer=lambda b: json.loads(b),
        host=redis_cluster_available.host,
        port=redis_cluster_available.port,
        key_prefix=f"pipeshub:kv-cluster-it-{unique_suffix}:",
    )
    yield kv_store
    await kv_store.close()


class TestKvStoreOnARealCluster:
    async def test_health_check_reports_true(self, store) -> None:
        assert await store.health_check() is True

    async def test_create_get_update_delete_round_trip(self, store) -> None:
        assert await store.create_key("/k", {"n": 1}, overwrite=False) is True
        assert (await store.get_key("/k")) == {"n": 1}

        await store.update_value("/k", {"n": 2})
        assert (await store.get_key("/k")) == {"n": 2}

        assert await store.delete_key("/k") is True
        assert (await store.get_key("/k")) is None

    async def test_create_key_overwrite_false_reports_false_and_keeps_value(
        self, store
    ) -> None:
        await store.create_key("/k", {"n": 1}, overwrite=False)

        assert await store.create_key("/k", {"n": 2}, overwrite=False) is False
        assert (await store.get_key("/k")) == {"n": 1}

    async def test_get_all_keys_across_slots(self, store) -> None:
        """Keys named to land on different slots -- `get_all_keys`'s SCAN
        must fan out over every master (R2), not just the one the first
        cursor happens to hit."""
        await store.create_key("/dir/a", {"n": 1}, overwrite=False)
        await store.create_key("/dir/bbbb", {"n": 2}, overwrite=False)
        await store.create_key("/dir/ccccccc", {"n": 3}, overwrite=False)

        keys = await store.get_all_keys()

        assert set(keys) == {"/dir/a", "/dir/bbbb", "/dir/ccccccc"}

    async def test_compare_and_set_on_a_real_cluster(self, store) -> None:
        """The swap is a Lua script with its key in KEYS, which the cluster client routes to the key's slot."""
        assert await store.compare_and_set("/cas", None, {"n": 1}) is True
        assert await store.compare_and_set("/cas", None, {"n": 2}) is False
        assert await store.compare_and_set("/cas", {"n": 0}, {"n": 2}) is False
        assert await store.compare_and_set("/cas", {"n": 1}, {"n": 2}) is True
        assert (await store.get_key("/cas")) == {"n": 2}

    @pytest.mark.parametrize("expected", [{"n": 0}, None], ids=["present", "absent"])
    async def test_concurrent_compare_and_set_has_exactly_one_winner(self, store, expected) -> None:
        for round_ in range(5):
            key = f"/cas-race/{round_}"
            if expected is not None:
                await store.create_key(key, expected)

            results = await asyncio.gather(*(store.compare_and_set(key, expected, {"n": f"w{i}"}) for i in range(8)))

            assert results.count(True) == 1, results
            assert (await store.get_key(key)) == {"n": f"w{results.index(True)}"}
