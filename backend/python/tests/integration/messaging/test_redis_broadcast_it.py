"""OPS1-4-9: the fan-out subscriber against a real Redis, with processes that are killed.

What a fake cannot show: that a subscriber killed with SIGKILL leaves no consumer group on the server (the leak of
CPD-6 §10.3 F-A), and a real blocking XREAD picking up where it stopped.

Requires a Redis (REDIS_IT_HOST / REDIS_IT_PORT); each test uses a stream of its own and deletes it.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from tests.integration.messaging.conftest import DRAIN_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from logging import Logger

    from redis.asyncio import Redis

    from app.services.messaging.config import StreamMessage
    from app.services.messaging.interface.consumer import IMessagingConsumer

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# A subscriber process: joins the topic as the product does, says so, then waits to be stopped or killed.
_SUBSCRIBER = """
import asyncio, logging, sys
from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.messaging_factory import MessagingFactory

async def main(host, port, topic, clean):
    config = RedisStreamsConfig(host=host, port=int(port), topics=[topic], client_id="cp", group_id="cp-own",
                                batch_size=10, block_ms=100, broadcast=True)
    consumer = MessagingFactory.create_consumer(logging.getLogger("it"), config, broker_type=MessageBrokerType.REDIS)

    async def handler(_message):
        return True

    await consumer.start(handler)
    if clean == "clean":
        await consumer.stop()
        return
    print("SUBSCRIBED", flush=True)
    await asyncio.sleep(600)

asyncio.run(main(*sys.argv[1:]))
"""


def _config(host: str, port: int, topic: str) -> RedisStreamsConfig:
    return RedisStreamsConfig(
        host=host, port=port, topics=[topic], client_id="cp", group_id="cp-own", batch_size=10, block_ms=100,
        broadcast=True,
    )


@pytest.fixture
async def stream(redis_available: tuple[str, int], unique_suffix: str) -> AsyncIterator[tuple[str, int, str, Redis]]:
    from redis.asyncio import Redis

    host, port = redis_available
    topic = f"ops1f4-{unique_suffix}-control-plane-events"
    client = Redis(host=host, port=port, decode_responses=True)
    yield host, port, topic, client
    await client.delete(topic)
    await client.aclose()


async def _groups(client: Redis, topic: str) -> list[str]:
    if not await client.exists(topic):
        return []
    return [group["name"] for group in await client.xinfo_groups(topic)]


async def _until(predicate) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


async def _subscriber(logger: Logger, host: str, port: int, topic: str, handler) -> IMessagingConsumer:
    consumer = MessagingFactory.create_consumer(logger, _config(host, port, topic), broker_type=MessageBrokerType.REDIS)
    await consumer.start(handler)
    return consumer


async def test_processes_stopped_or_killed_leave_no_consumer_group(stream, logger) -> None:
    host, port, topic, client = stream
    argv = [sys.executable, "-c", _SUBSCRIBER, host, str(port), topic]
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    for _ in range(5):
        subprocess.run([*argv, "clean"], env=env, check=True, capture_output=True, timeout=120)
    for _ in range(5):
        child = subprocess.Popen([*argv, "killed"], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            assert child.stdout is not None
            while not (await asyncio.to_thread(child.stdout.readline)).startswith(b"SUBSCRIBED"):
                assert child.poll() is None, "the subscriber process exited before it subscribed"
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait()

    assert await _groups(client, topic) == []


async def test_each_subscriber_sees_every_message_and_one_that_stopped_reading_catches_up(stream, logger) -> None:
    host, port, topic, client = stream
    producer = RedisStreamsProducer(logger, _config(host, port, topic))
    await producer.initialize()
    await producer.send_message(topic, {"eventType": "cp.changed", "payload": {"i": 0}})
    steady: list[int] = []
    paused: list[int] = []
    resume = asyncio.Event()

    async def reads(message: StreamMessage) -> bool:
        steady.append(message.payload["i"])
        return True

    async def stops_at_the_first(message: StreamMessage) -> bool:
        paused.append(message.payload["i"])
        if message.payload["i"] == 1:
            await resume.wait()
        return True

    consumers = [
        await _subscriber(logger, host, port, topic, reads),
        await _subscriber(logger, host, port, topic, stops_at_the_first),
    ]
    try:
        for i in (1, 2, 3, 4):
            await producer.send_message(topic, {"eventType": "cp.changed", "payload": {"i": i}})
        await _until(lambda: len(steady) == 4 and paused == [1])
        assert await _groups(client, topic) == []

        resume.set()

        await _until(lambda: len(paused) == 4)
        assert steady == paused == [1, 2, 3, 4]
    finally:
        for consumer in consumers:
            await consumer.stop()
        await producer.cleanup()
