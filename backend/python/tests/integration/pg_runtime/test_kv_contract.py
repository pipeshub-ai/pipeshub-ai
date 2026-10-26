"""The key-value store contract, run against the Redis store (over a fake Redis) and the PostgreSQL store.

Both must give the same answers to the same calls: that is what lets ``KV_STORE_TYPE`` choose the backend without the code above
it noticing.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

pytest.importorskip("fakeredis.aioredis")

from collections.abc import Callable

from app.config.providers.postgres_store import PostgresKeyValueStore
from app.config.providers.redis import redis_store as redis_store_module
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.inprocess_provider import InProcessRedisProvider

SER = lambda v: json.dumps(v).encode()  # noqa: E731
DE = lambda b: json.loads(b)  # noqa: E731


@pytest.fixture(params=["redis", "postgres"])
def make_store(request, runtime, uid) -> Callable[[], object]:
    """Factory for stores that share one backend; every key is prefixed so cases do not see each other."""
    from fakeredis import FakeServer

    server = FakeServer()

    def make() -> object:
        if request.param == "postgres":
            store = PostgresKeyValueStore(SER, DE, runtime=runtime)
        else:
            provider = InProcessRedisProvider(RedisConnectionConfig(), server=server)
            with patch.object(
                redis_store_module, "get_redis_provider", return_value=provider
            ):
                store = RedisDistributedKeyValueStore(
                    SER, DE, host="x", port=1, key_prefix=f"t:{uid}:"
                )
        return store

    make.p = f"/c{uid}/"
    return make


async def test_round_trip(make_store) -> None:
    s, k = make_store(), make_store.p + "k"
    assert await s.create_key(k, {"n": 1}, overwrite=False) is True
    assert await s.get_key(k) == {"n": 1}
    await s.update_value(k, {"n": 2})
    assert await s.get_key(k) == {"n": 2}
    assert await s.delete_key(k) is True
    assert await s.get_key(k) is None
    assert await s.delete_key(k) is False


async def test_overwrite_false_keeps_the_first_value(make_store) -> None:
    s, k = make_store(), make_store.p + "k"
    assert await s.create_key(k, "first", overwrite=False) is True
    assert await s.create_key(k, "second", overwrite=False) is False
    assert await s.get_key(k) == "first"
    assert await s.create_key(k, "third", overwrite=True) is True
    assert await s.get_key(k) == "third"


async def test_update_of_a_missing_key_raises(make_store) -> None:
    with pytest.raises(KeyError):
        await make_store().update_value(make_store.p + "missing", 1)


async def test_ttl_expires_and_frees_the_key_for_a_new_claim(make_store) -> None:
    s, k = make_store(), make_store.p + "ttl"
    assert await s.create_key(k, "v", overwrite=False, ttl=1) is True
    assert await s.get_key(k) == "v"
    await asyncio.sleep(1.3)
    assert await s.get_key(k) is None
    assert k not in await s.get_all_keys()
    assert (
        await s.create_key(k, "w", overwrite=False, ttl=60) is True
    )  # an expired key is absent for SET NX
    assert await s.get_key(k) == "w"


async def test_update_without_ttl_drops_the_ttl(make_store) -> None:
    s, k = make_store(), make_store.p + "ttl2"
    await s.create_key(k, "v", ttl=1)
    await s.update_value(
        k, "w"
    )  # Redis SET XX without EX clears the expiry; so does the PostgreSQL store
    await asyncio.sleep(1.3)
    assert await s.get_key(k) == "w"


async def test_listing_by_directory_and_all(make_store) -> None:
    s, p = make_store(), make_store.p
    for key in (p + "d/a", p + "d/b", p + "d/sub/c", p + "other"):
        await s.create_key(key, 1)
    assert sorted(await s.list_keys_in_directory(p + "d")) == [
        p + "d/a",
        p + "d/b",
        p + "d/sub/c",
    ]
    assert sorted(await s.list_keys_in_directory(p + "d/")) == [
        p + "d/a",
        p + "d/b",
        p + "d/sub/c",
    ]
    assert {p + "other", p + "d/a"} <= set(await s.get_all_keys())


async def test_keys_with_glob_and_sql_wildcards_are_literal(make_store) -> None:
    s, p = make_store(), make_store.p
    await s.create_key(p + "w/a*b", 1)
    await s.create_key(p + "w/a%b", 1)
    await s.create_key(p + "w/axb", 1)
    assert sorted(await s.list_keys_in_directory(p + "w")) == [
        p + "w/a%b",
        p + "w/a*b",
        p + "w/axb",
    ]


async def test_a_watch_fires_for_writes_made_through_the_same_store(make_store) -> None:
    s, k = make_store(), make_store.p + "watch"
    seen = []
    wid = await s.watch_key(k, seen.append)
    await s.create_key(k, 1)
    await s.update_value(k, 2)
    await s.delete_key(k)
    await s.cancel_watch(k, wid)
    await s.create_key(k, 3)
    assert seen == [1, 2, None]


async def test_a_change_published_by_one_store_reaches_a_subscriber_of_another(
    make_store, runtime, monkeypatch
) -> None:
    from app.services.pg_runtime import listen_dsn

    if isinstance(make_store(), PostgresKeyValueStore):
        monkeypatch.setenv("PG_LISTEN_URL", __import__("os").environ["PG_CORE_URL"])
        assert listen_dsn(runtime.cfg) is not None
    a, b = make_store(), make_store()
    got = []
    handle = await a.subscribe_changes(got.append)
    assert handle is not None
    await asyncio.sleep(0.5)  # LISTEN / SUBSCRIBE in place
    got.clear()  # a reconnect clears every cache: not what is under test
    await b.publish_change(make_store.p + "changed")
    for _ in range(50):
        if make_store.p + "changed" in got:
            break
        await asyncio.sleep(0.1)
    assert make_store.p + "changed" in got
    await a.unsubscribe_changes(handle)
    await a.close()


async def test_a_failing_purge_does_not_fail_the_write(runtime, uid) -> None:
    """Expired rows are removed after the write has committed, in a transaction of their own: if that fails, the write stands."""
    from app.config.providers import postgres_store

    store = PostgresKeyValueStore(SER, DE, runtime=runtime)
    key = f"/purge{uid}/k"
    with patch.object(postgres_store, "_PURGE", "/* pipeshub:kv.purge */ SELECT 1/0"):
        store._last_purge = 0.0
        assert await store.create_key(key, {"v": 1}) is True
    assert await store.get_key(key) == {"v": 1}
    await store.delete_key(key)
