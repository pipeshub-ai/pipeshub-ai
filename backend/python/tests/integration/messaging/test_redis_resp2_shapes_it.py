"""Replies from a real Redis, through the provider, have the RESP2 shapes the
stream and Lua helpers parse.

redis-py 6+ speaks RESP3 unless told otherwise, which turns XREADGROUP,
XINFO, ZRANGE WITHSCORES and EVAL table replies into dicts or differently
nested lists. The providers pin ``protocol=2``; these tests fail if a client
reaches the server without that pin.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.redis.config import (
    RESP_PROTOCOL,
    ClientOptions,
    RedisConnectionConfig,
)
from app.services.redis.connection_provider_factory import (
    get_redis_provider,
    reset_redis_provider_registry,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def client(redis_available, unique_suffix) -> AsyncIterator[Redis]:
    host, port = redis_available
    provider = get_redis_provider(
        RedisConnectionConfig(host=host, port=port), mode="standalone"
    )
    redis = provider.create_client(ClientOptions(decode_responses=True))
    yield redis
    await redis.aclose()
    await provider.close()
    reset_redis_provider_registry()


@pytest.fixture
async def blocking_client(redis_available) -> AsyncIterator[Redis]:
    host, port = redis_available
    provider = get_redis_provider(
        RedisConnectionConfig(host=host, port=port), mode="standalone"
    )
    redis = provider.create_client(ClientOptions(blocking=True))
    yield redis
    await redis.aclose()
    await provider.close()
    reset_redis_provider_registry()


def _protocol(redis) -> int | None:
    return redis.connection_pool.connection_kwargs.get("protocol")


async def test_provider_clients_are_pinned_to_resp2(client, blocking_client) -> None:
    assert RESP_PROTOCOL == 2
    assert _protocol(client) == 2
    assert _protocol(blocking_client) == 2
    # The server agrees: HELLO reports the protocol the connection negotiated.
    conn = await client.connection_pool.get_connection()
    try:
        assert conn.protocol == 2
    finally:
        await client.connection_pool.release(conn)


async def test_stream_replies_are_resp2_shaped(client, unique_suffix) -> None:
    stream, group, consumer = f"s-{unique_suffix}", "g", "c1"
    await client.xgroup_create(stream, group, id="0", mkstream=True)
    ids = [await client.xadd(stream, {"n": str(i)}) for i in range(3)]

    read = await client.xreadgroup(group, consumer, {stream: ">"}, count=3)
    assert isinstance(read, list)  # RESP3 would be {stream: [[...]]}
    name, entries = read[0]
    assert name == stream
    assert [entry_id for entry_id, _ in entries] == ids
    assert isinstance(entries[0][1], dict)

    summary = await client.xpending(stream, group)
    assert summary["pending"] == 3
    assert summary["consumers"] == [{"name": consumer, "pending": 3}]
    assert summary["min"] == ids[0] and summary["max"] == ids[-1]

    rows = await client.xpending_range(stream, group, min="-", max="+", count=10)
    assert isinstance(rows, list) and len(rows) == 3
    assert {"message_id", "consumer", "time_since_delivered", "times_delivered"} <= set(rows[0])

    claimed = await client.xautoclaim(stream, group, "c2", min_idle_time=0, start_id="0-0", count=10)
    assert isinstance(claimed, (list, tuple))
    next_id, messages, *_ = claimed
    assert next_id == "0-0"
    assert [entry_id for entry_id, _ in messages] == ids

    info = await client.xinfo_stream(stream)
    assert isinstance(info, dict) and info["length"] == 3
    groups = await client.xinfo_groups(stream)
    assert isinstance(groups, list) and groups[0]["name"] == group
    consumers = await client.xinfo_consumers(stream, group)
    assert isinstance(consumers, list)
    assert {c["name"] for c in consumers} == {consumer, "c2"}


async def test_sorted_set_hash_and_script_replies_are_resp2_shaped(client, unique_suffix) -> None:
    key, hkey = f"z-{unique_suffix}", f"h-{unique_suffix}"
    await client.zadd(key, {"a": 1.0, "b": 2.0})

    withscores = await client.zrange(key, 0, -1, withscores=True)
    # RESP3 yields lists of [member, score] pairs rather than tuples.
    assert withscores == [("a", 1.0), ("b", 2.0)]
    assert all(isinstance(pair, tuple) for pair in withscores)

    popped = await client.zpopmin(key)
    assert popped == [("a", 1.0)]

    await client.hset(hkey, mapping={"f1": "v1", "f2": "v2"})
    assert await client.hgetall(hkey) == {"f1": "v1", "f2": "v2"}

    table = await client.eval("return {1, 'two', {3, 4}}", 0)
    assert table == [1, "two", [3, 4]]
    assert isinstance(table, list)
    # A Lua table with float score fields is still an array reply.
    assert isinstance(await client.eval("return redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')", 1, key), list)
