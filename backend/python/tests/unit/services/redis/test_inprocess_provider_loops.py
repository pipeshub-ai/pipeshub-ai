"""The in-process provider keeps one client per thread and replaces it when its event loop is gone or another one runs."""
import asyncio

from app.services.redis.config import RedisConnectionConfig
from app.services.redis.inprocess_provider import InProcessRedisProvider


def _provider() -> InProcessRedisProvider:
    return InProcessRedisProvider(RedisConnectionConfig(host="localhost", port=6379))


def test_a_new_loop_on_the_same_thread_gets_a_new_client_and_the_registry_does_not_grow() -> None:
    provider = _provider()

    async def grab() -> object:
        return provider.get_client()

    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second
    assert len(provider._clients) == 1


async def test_the_same_loop_keeps_its_client() -> None:
    provider = _provider()
    assert provider.get_client() is provider.get_client()


async def test_replies_have_the_array_shapes_the_redis_helpers_parse() -> None:
    """The real providers pin RESP2; fakeredis is not pinned by us, so a release
    that switched its default to map-shaped (RESP3) replies must fail here
    rather than in code that only runs under ``REDIS_MODE=inprocess``."""
    client = _provider().get_client()

    await client.zadd("z", {"a": 1.0, "b": 2.0})
    withscores = await client.zrange("z", 0, -1, withscores=True)
    assert withscores == [("a", 1.0), ("b", 2.0)]
    assert isinstance(withscores, list)
    assert all(isinstance(pair, tuple) for pair in withscores)
    popped = await client.zpopmin("z")
    assert popped == [("a", 1.0)]

    await client.xgroup_create("s", "g", id="0", mkstream=True)
    await client.xadd("s", {"k": "v"})
    reply = await client.xreadgroup("g", "c", {"s": ">"}, count=1)
    # RESP3 would give {"s": [[(id, fields)]]}.
    assert isinstance(reply, list)
    stream, entries = reply[0]
    assert stream == "s"
    assert entries[0][1] == {"k": "v"}
