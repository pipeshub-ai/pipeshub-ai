"""kv_redis_to_postgres: copy at the raw level, honour prefix and namespace, carry TTL, re-run is a no-op."""

import asyncio
import threading

import fakeredis
import fakeredis.aioredis
import pytest

from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from app.scripts.kv_redis_to_postgres import copy_kv


class FakePg:
    """Stands in for PostgresKeyValueStore: same create_key / get_all_keys contract."""

    def __init__(self, existing=None) -> None:
        self.rows = dict(existing or {})
        self.ttls = {}

    async def create_key(self, key, value, overwrite=True, ttl=None) -> bool:
        if key in self.rows and not overwrite:
            return False
        self.rows[key], self.ttls[key] = value, ttl
        return True

    async def get_key(self, key) -> bytes | None:
        return self.rows.get(key)


def _src(redis, namespace="") -> RedisDistributedKeyValueStore:
    store = RedisDistributedKeyValueStore(bytes, bytes, host="x", port=1, key_prefix="pipeshub:kv:")
    store._key_namespace_prefix = f"{namespace}:" if namespace else ""
    store._clients[threading.get_ident()] = (redis, asyncio.get_running_loop())
    return store


@pytest.fixture
async def redis() -> fakeredis.aioredis.FakeRedis:
    r = fakeredis.aioredis.FakeRedis()
    await r.set("pipeshub:kv:/services/a", b"enc-a")
    await r.set("pipeshub:kv:/services/b", b"enc-b", ex=100)
    await r.set("ns:pipeshub:kv:/services/c", b"enc-c")
    await r.set("pipeshub:cache:x", b"cache")  # not config KV
    await r.set("{record-events}:lane", b"queue")
    return r


async def test_copies_raw_values_ttl_and_only_the_kv_namespace(redis) -> None:
    dst = FakePg()
    out = await copy_kv(_src(redis), dst)
    assert dst.rows == {"/services/a": b"enc-a", "/services/b": b"enc-b"}
    assert dst.ttls["/services/a"] is None
    assert 90 < dst.ttls["/services/b"] <= 100
    assert out["copied"] == 2
    assert out["different_after"] == 0


async def test_namespace_is_honoured(redis) -> None:
    dst = FakePg()
    await copy_kv(_src(redis, "ns"), dst)
    assert dst.rows == {"/services/c": b"enc-c"}


async def test_rerun_is_a_noop(redis) -> None:
    dst = FakePg()
    await copy_kv(_src(redis), dst)
    out = await copy_kv(_src(redis), dst)
    assert out["copied"] == 0
    assert out["already_equal"] == 2


async def test_a_different_value_refuses_before_any_write_unless_overwrite(redis) -> None:
    dst = FakePg({"/services/a": b"other"})
    out = await copy_kv(_src(redis), dst)
    assert out["conflicts"] == ["/services/a"]
    assert dst.rows == {"/services/a": b"other"}
    await copy_kv(_src(redis), dst, overwrite=True)
    assert dst.rows == {"/services/a": b"enc-a", "/services/b": b"enc-b"}


async def test_expired_keys_are_not_written(redis) -> None:
    await redis.set("pipeshub:kv:/services/dead", b"x", px=1)
    await asyncio.sleep(0.05)
    dst = FakePg()
    await copy_kv(_src(redis), dst)
    assert "/services/dead" not in dst.rows


async def test_a_key_changed_during_the_copy_is_recopied(redis) -> None:
    class Racy(FakePg):
        async def create_key(self, key, value, overwrite=True, ttl=None) -> bool:
            if key == "/services/a" and value == b"enc-a":
                await redis.set("pipeshub:kv:/services/a", b"enc-a2")  # a service writes mid-copy
            return await super().create_key(key, value, overwrite, ttl)

    dst = Racy()
    out = await copy_kv(_src(redis), dst)
    assert dst.rows["/services/a"] == b"enc-a2"
    assert out["recopied"] == 1
    assert out["different_after"] == 0


async def test_dry_run_writes_nothing_and_counts_prefixes(redis) -> None:
    dst = FakePg()
    out = await copy_kv(_src(redis), dst, dry_run=True)
    assert dst.rows == {}
    assert out["by_prefix"] == {"services": 2}
