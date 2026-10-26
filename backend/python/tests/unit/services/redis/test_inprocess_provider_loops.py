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
