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

    async def delete_key(self, key) -> bool:
        return self.rows.pop(key, None) is not None

    async def list_keys_in_directory(self, directory) -> list[str]:
        return [k for k in self.rows if k.startswith(directory)]


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
    assert out["written"] == 2 and out["changed"] == 2
    assert out["different_after"] == 0


async def test_namespace_is_honoured(redis) -> None:
    dst = FakePg()
    await copy_kv(_src(redis, "ns"), dst)
    assert dst.rows == {"/services/c": b"enc-c"}


async def test_rerun_is_a_noop(redis) -> None:
    dst = FakePg()
    await copy_kv(_src(redis), dst)
    before = (dict(dst.rows), dict(dst.ttls))
    out = await copy_kv(_src(redis), dst)
    assert out["changed"] == 0 and out["unchanged"] == 2
    assert dst.rows == before[0] and dst.ttls["/services/a"] is None


async def test_an_equal_value_takes_the_expiry_redis_has(redis) -> None:
    dst = FakePg({"/services/a": b"enc-a"})
    dst.ttls["/services/a"] = 5  # same bytes, but would expire in PostgreSQL while Redis keeps it
    await copy_kv(_src(redis), dst)
    assert dst.ttls["/services/a"] is None


async def test_a_key_that_expires_after_the_scan_is_not_written(redis) -> None:
    class ExpiresDuringPreflight(FakePg):
        async def get_key(self, key) -> bytes | None:
            await redis.delete("pipeshub:kv:/services/b")
            return await super().get_key(key)

    dst = ExpiresDuringPreflight()
    out = await copy_kv(_src(redis), dst)
    assert "/services/b" not in dst.rows
    assert out["expired_during_copy"] == 1


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


async def test_a_key_that_expires_during_the_copy_leaves_no_copy_in_postgres(redis) -> None:
    class ExpiresAfterPreflight(FakePg):
        async def create_key(self, key, value, overwrite=True, ttl=None) -> bool:
            await redis.delete("pipeshub:kv:/services/b")  # expires while the first key is written
            return await super().create_key(key, value, overwrite, ttl)

    dst = ExpiresAfterPreflight({"/services/b": b"enc-b"})  # an earlier run's copy
    out = await copy_kv(_src(redis), dst)
    assert "/services/b" not in dst.rows
    assert out["removed_expired"] == 1 and out["different_after"] == 0


async def test_a_copy_of_a_key_redis_no_longer_has_is_a_conflict_until_overwrite(redis) -> None:
    src = _src(redis)
    listed = await src.get_all_keys()

    async def with_a_gone_key() -> list[str]:
        return [*listed, "/services/old"]  # listed by the scan, gone by the read

    src.get_all_keys = with_a_gone_key
    dst = FakePg({"/services/old": b"stale"})
    out = await copy_kv(src, dst)
    assert out["conflicts"] == ["/services/old"]
    assert dst.rows == {"/services/old": b"stale"}
    out = await copy_kv(src, dst, overwrite=True)
    assert "/services/old" not in dst.rows
    assert out["removed_expired"] == 1 and out["different_after"] == 0


async def test_a_key_written_again_after_it_was_gone_is_copied(redis) -> None:
    src = _src(redis)
    listed = await src.get_all_keys()

    async def with_a_gone_key() -> list[str]:
        return [*listed, "/services/back"]  # listed by the scan, absent at the first read

    class WrittenAgainDuringTheCopy(FakePg):
        async def create_key(self, key, value, overwrite=True, ttl=None) -> bool:
            await redis.set("pipeshub:kv:/services/back", b"enc-back")
            return await super().create_key(key, value, overwrite, ttl)

    src.get_all_keys = with_a_gone_key
    dst = WrittenAgainDuringTheCopy()
    out = await copy_kv(src, dst)
    assert dst.rows["/services/back"] == b"enc-back"
    assert out["different_after"] == 0


async def test_a_configuration_key_only_postgres_holds_is_a_conflict_and_runtime_rows_are_left_alone(redis) -> None:
    dst = FakePg({"/services/removed": b"stale", "lease:sync:1": b"runtime"})
    out = await copy_kv(_src(redis), dst)
    assert out["conflicts"] == ["/services/removed"]
    assert "/services/a" not in dst.rows  # refused before any write
    out = await copy_kv(_src(redis), dst, overwrite=True)
    assert "/services/removed" not in dst.rows and dst.rows["lease:sync:1"] == b"runtime"
    assert out["different_after"] == 0
