"""`RedisDistributedKeyValueStore` against every registered connection mode
(Phase 6).

Companion to `tests/unit/config/test_key_value_store_contract.py` (which
covers the in-memory/encrypted/etcd stores): the Redis-backed
`KeyValueStore` is exercised here, once per `IRedisConnectionProvider` mode,
against a real `FakeRedis`/`FakeClusterRedis` instead of a mocked client, so
a `create_key(overwrite=False)` regression that only breaks under a cluster
provider (e.g. a reintroduced `WATCH`/`MULTI`/`EXEC` CAS, R3) is caught here
even though the mocked unit tests in `tests/unit/config/test_redis_store.py`
would not catch it.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.config.providers.redis import redis_store as redis_store_module
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from tests.support.redis_provider_matrix import PROVIDERS


def _make_store(make_provider) -> RedisDistributedKeyValueStore:
    provider = make_provider()
    with patch.object(redis_store_module, "get_redis_provider", return_value=provider):
        return RedisDistributedKeyValueStore(
            serializer=lambda v: json.dumps(v).encode(),
            deserializer=lambda b: json.loads(b),
            host="redis",
            port=6379,
            key_prefix="pipeshub:kv-contract-test:",
        )


def _transport_from_provider(provider_param):
    (make_provider,) = provider_param.values
    return pytest.param(lambda: _make_store(make_provider), id=provider_param.id)


# Derived from `PROVIDERS` in `tests/support/redis_provider_matrix.py` (T4)
# so an EE `conftest.py` appending a `memorydb` entry there runs this same
# suite against it with no changes here.
TRANSPORTS = [_transport_from_provider(p) for p in PROVIDERS]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_store", TRANSPORTS)
class TestRedisKvStoreContract:
    async def test_health_check_reports_true_when_reachable(self, make_store) -> None:
        store = make_store()
        assert await store.health_check() is True
        await store.close()

    async def test_create_get_update_delete_round_trip(self, make_store) -> None:
        store = make_store()
        try:
            assert await store.create_key("/k", {"n": 1}, overwrite=False) is True
            assert (await store.get_key("/k")) == {"n": 1}

            await store.update_value("/k", {"n": 2})
            assert (await store.get_key("/k")) == {"n": 2}

            assert await store.delete_key("/k") is True
            assert (await store.get_key("/k")) is None
        finally:
            await store.close()

    async def test_create_key_overwrite_false_reports_false_and_keeps_value(
        self, make_store
    ) -> None:
        """The store's only atomic claim primitive (`SET NX`): a losing
        claim must not silently overwrite the winner's value."""
        store = make_store()
        try:
            await store.create_key("/k", {"n": 1}, overwrite=False)

            assert await store.create_key("/k", {"n": 2}, overwrite=False) is False
            assert (await store.get_key("/k")) == {"n": 1}
        finally:
            await store.close()

    async def test_update_value_on_a_missing_key_raises_key_error(self, make_store) -> None:
        store = make_store()
        try:
            with pytest.raises(KeyError):
                await store.update_value("/missing", {"n": 1})
        finally:
            await store.close()

    async def test_get_all_keys_and_list_keys_in_directory(self, make_store) -> None:
        store = make_store()
        try:
            await store.create_key("/dir/a", {"n": 1}, overwrite=False)
            await store.create_key("/dir/b", {"n": 2}, overwrite=False)
            await store.create_key("/other", {"n": 3}, overwrite=False)

            all_keys = await store.get_all_keys()
            assert set(all_keys) == {"/dir/a", "/dir/b", "/other"}

            dir_keys = await store.list_keys_in_directory("/dir")
            assert set(dir_keys) == {"/dir/a", "/dir/b"}
        finally:
            await store.close()
