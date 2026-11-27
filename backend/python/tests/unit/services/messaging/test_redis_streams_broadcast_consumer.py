"""OPS1-4-10, OPS1-4-11: the fan-out subscriber on Redis Streams (``RedisStreamsBroadcastConsumer``).

The real consume loop runs against an in-memory Redis that implements streams and consumer groups; nothing in the
consumer is mocked. The same behaviour on a real Redis, with processes that are killed, is
``tests/integration/messaging/test_redis_broadcast_it.py``.
"""
from __future__ import annotations

import asyncio
from logging import getLogger
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams.broadcast_consumer import (
    RedisStreamsBroadcastConsumer,
)
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.messaging.config import StreamMessage
    from tests.support.fake_cluster_redis import FakeClusterRedis

TOPIC = "control-plane-events"


class NonBlockingXreadProvider(FakeRedisConnectionProvider):
    """fakeredis can deadlock when one client sits in ``XREAD BLOCK`` while another client on the same fake server
    runs a command, which real Redis never does. Reads are issued without BLOCK and an empty read waits out the
    block time on the event loop instead, which is what the consumer observes from a real server."""

    def create_client(self, options=None) -> FakeClusterRedis:
        client = super().create_client(options)
        read = client.xread

        async def xread(*args, block=None, **kwargs) -> list:
            result = await read(*args, **kwargs)
            if not result and block:
                await asyncio.sleep(block / 1000)
            return result

        client.xread = xread
        return client


@pytest.fixture
def provider() -> NonBlockingXreadProvider:
    return NonBlockingXreadProvider(is_cluster=False)


@pytest.fixture(autouse=True)
def _three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")


def _config(**overrides: object) -> RedisStreamsConfig:
    base: dict = {
        "topics": [TOPIC], "group_id": "cp-group-of-this-process", "client_id": "cp", "batch_size": 10,
        "block_ms": 5, "broadcast": True,
    }
    base.update(overrides)
    return RedisStreamsConfig(**base)


async def _publish(provider: FakeRedisConnectionProvider, *numbers: int) -> None:
    producer = RedisStreamsProducer(getLogger("test"), _config(), provider=provider)
    await producer.initialize()
    for i in numbers:
        await producer.send_message(TOPIC, {"eventType": "cp.changed", "payload": {"i": i}})
    await producer.cleanup()


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.005)


class Subscriber:
    def __init__(self, fail: dict[int, int] | None = None, raises: dict[int, BaseException] | None = None) -> None:
        self.seen: list[int] = []
        self._fail = dict(fail or {})
        self._raises = raises or {}

    async def __call__(self, message: StreamMessage) -> bool:
        i = message.payload["i"]
        self.seen.append(i)
        if i in self._raises:
            raise self._raises[i]
        if self._fail.get(i, 0) > 0:
            self._fail[i] -= 1
            return False
        return True


async def _subscribe(
    provider: FakeRedisConnectionProvider, handler: Subscriber, **config: object
) -> RedisStreamsBroadcastConsumer:
    consumer = RedisStreamsBroadcastConsumer(getLogger("test"), _config(**config), provider=provider)
    await consumer.start(handler)
    return consumer


async def _groups(provider: FakeRedisConnectionProvider) -> list[str]:
    client = provider.get_client()
    if not await client.exists(TOPIC):
        return []
    return [g["name"] for g in await client.xinfo_groups(TOPIC)]


class TestFanOut:
    async def test_every_subscriber_sees_every_message_and_none_from_before_it_subscribed(self, provider) -> None:
        await _publish(provider, 0)
        first, second = Subscriber(), Subscriber()
        consumers = [await _subscribe(provider, first), await _subscribe(provider, second)]

        await _publish(provider, 1, 2, 3)

        await _until(lambda: len(first.seen) == 3 and len(second.seen) == 3)
        assert first.seen == second.seen == [1, 2, 3]
        for consumer in consumers:
            await consumer.stop()

    async def test_a_subscriber_holds_no_consumer_group_running_stopped_or_abandoned(self, provider) -> None:
        await _publish(provider, 0)
        stopped, abandoned = Subscriber(), Subscriber()
        consumer = await _subscribe(provider, stopped)
        left_running = await _subscribe(provider, abandoned)
        await _publish(provider, 1)
        await _until(lambda: stopped.seen == [1] and abandoned.seen == [1])
        assert await _groups(provider) == []

        await consumer.stop()

        assert await _groups(provider) == []
        await left_running.stop()

    async def test_a_subscriber_on_a_stream_that_does_not_exist_yet_sees_its_first_message(self, provider) -> None:
        handler = Subscriber()
        consumer = await _subscribe(provider, handler)

        await _publish(provider, 1)

        await _until(lambda: handler.seen == [1])
        await consumer.stop()


class TestHandlerFailure:
    async def test_a_handler_that_fails_for_a_passing_reason_is_tried_again_then_the_next_message_follows(
        self, provider
    ) -> None:
        handler = Subscriber(fail={1: 1})
        consumer = await _subscribe(provider, handler)

        await _publish(provider, 1, 2)

        await _until(lambda: handler.seen == [1, 1, 2])
        await consumer.stop()

    async def test_a_message_that_keeps_failing_is_given_up_after_the_delivery_limit(self, provider) -> None:
        handler = Subscriber(fail={1: 99})
        consumer = await _subscribe(provider, handler)

        await _publish(provider, 1, 2)

        await _until(lambda: handler.seen[-1:] == [2])
        assert handler.seen == [1, 1, 1, 2]
        await consumer.stop()

    async def test_a_message_that_can_never_be_handled_is_not_tried_again(self, provider) -> None:
        handler = Subscriber(raises={1: FileNotFoundError("gone")})
        consumer = await _subscribe(provider, handler)

        await _publish(provider, 1, 2)

        await _until(lambda: handler.seen[-1:] == [2])
        assert handler.seen == [1, 2]
        await consumer.stop()


class TestReading:
    async def test_a_read_that_fails_is_taken_up_again_from_the_last_message_handled(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handler = Subscriber()
        consumer = await _subscribe(provider, handler)
        await _publish(provider, 1)
        await _until(lambda: handler.seen == [1])
        read = consumer.redis.xread
        dropped = asyncio.Event()

        async def fails_once(*args, **kwargs) -> list:
            if not dropped.is_set():
                dropped.set()
                raise ConnectionError("connection dropped")
            return await read(*args, **kwargs)

        monkeypatch.setattr(consumer.redis, "xread", fails_once)
        monkeypatch.setattr(asyncio, "sleep", _no_backoff(asyncio.sleep))
        await dropped.wait()

        await _publish(provider, 2, 3)

        await _until(lambda: len(handler.seen) == 3)
        assert handler.seen == [1, 2, 3]
        await consumer.stop()


def _no_backoff(sleep) -> Callable[[float], Awaitable[None]]:
    """The loop's one-second pause after a failed read, shortened; every other wait as it is."""

    async def shortened(seconds: float) -> None:
        await sleep(0.005 if seconds >= 1 else seconds)

    return shortened


class TestFactory:
    def test_a_broadcast_config_gives_the_subscriber_without_a_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_KEY_NAMESPACE", raising=False)
        logger = getLogger("test")

        broadcast = MessagingFactory.create_consumer(logger, _config(), broker_type=MessageBrokerType.REDIS)
        shared = MessagingFactory.create_consumer(
            logger, _config(broadcast=False), broker_type=MessageBrokerType.REDIS
        )

        assert type(broadcast) is RedisStreamsBroadcastConsumer
        assert type(shared) is RedisStreamsConsumer
