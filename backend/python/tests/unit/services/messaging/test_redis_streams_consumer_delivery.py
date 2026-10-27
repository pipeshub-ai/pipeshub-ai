"""Delivery guarantees of the simple Redis Streams consumer (``RedisStreamsConsumer``).

When a deployment uses Redis Streams instead of Kafka, this consumer carries
the connectors service's entity and sync events. These tests run the real
consume loop end to end against an in-memory Redis that implements streams,
consumer groups and the pending-entries list (the list of delivered but not
yet acknowledged messages), with the real ``RetryManager`` and error
classifier. Nothing in the consumer is mocked.
"""
from __future__ import annotations

import asyncio
import json
from logging import getLogger
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.retry_manager import RetryManager
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from app.services.messaging.config import StreamMessage
    from tests.support.fake_cluster_redis import FakeClusterRedis

TOPIC = "entity-events"
GROUP = "entity_consumer_group"


def _config(**overrides: object) -> RedisStreamsConfig:
    base: dict = {
        "topics": [TOPIC],
        "group_id": GROUP,
        "client_id": "connectors-1",
        "batch_size": 10,
        "block_ms": 20,
        "claim_min_idle_ms": 0,
    }
    base.update(overrides)
    return RedisStreamsConfig(**base)


@pytest.fixture(autouse=True)
def _three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")


class _NonBlockingReadProvider(FakeRedisConnectionProvider):
    """fakeredis can deadlock when one client sits in ``XREADGROUP BLOCK``
    while another client on the same fake server runs a command, which real
    Redis never does. Reads are issued without BLOCK and an empty read waits
    out the block time on the event loop instead, which is what the consumer
    observes from a real server."""

    def create_client(self, options=None) -> FakeClusterRedis:
        client = super().create_client(options)
        read = client.xreadgroup

        async def xreadgroup(*args, block=None, **kwargs):  # noqa: ANN202
            result = await read(*args, **kwargs)
            if not result and block:
                await asyncio.sleep(block / 1000)
            return result

        client.xreadgroup = xreadgroup
        return client


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return _NonBlockingReadProvider(is_cluster=False)


@pytest.fixture
def retry_manager(provider: FakeRedisConnectionProvider) -> RetryManager:
    return RetryManager(getLogger("test"), redis_client=provider.get_client())


async def _produce(provider: FakeRedisConnectionProvider, *events: dict) -> None:
    producer = RedisStreamsProducer(getLogger("test"), _config(), provider=provider)
    await producer.initialize()
    for event in events:
        await producer.send_message(TOPIC, event)
    await producer.cleanup()


def _event(i: int) -> dict:
    return {"eventType": "userAdded", "payload": {"i": i, "orgId": "org-a"}}


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


async def _pending(provider: FakeRedisConnectionProvider) -> int:
    summary = await provider.get_client().xpending(TOPIC, GROUP)
    return int(summary["pending"])


async def _all_read_and_settled(provider: FakeRedisConnectionProvider) -> bool:
    """Every entry has been handed to the group and none is still pending.

    Checking only the pending count would pass before the consumer has read
    anything at all."""
    client = provider.get_client()
    stream = await client.xinfo_stream(TOPIC)
    group = next(g for g in await client.xinfo_groups(TOPIC) if g["name"] == GROUP)
    return group["last-delivered-id"] == stream["last-generated-id"] and int(group["pending"]) == 0


class Recorder:
    def __init__(self, fail: dict[int, BaseException] | None = None, fail_times: int = 1,
                 return_false: set[int] | None = None) -> None:
        self.seen: list[int] = []
        self.fail = fail or {}
        self.fail_times = fail_times
        self.return_false = return_false or set()
        self._failures: dict[int, int] = {}

    async def __call__(self, message: StreamMessage) -> bool:
        i = message.payload["i"]
        self.seen.append(i)
        if i in self.return_false:
            return False
        if i in self.fail and self._failures.get(i, 0) < self.fail_times:
            self._failures[i] = self._failures.get(i, 0) + 1
            raise self.fail[i]
        return True


async def _start(provider, handler, retry_manager=None, **config) -> RedisStreamsConsumer:
    consumer = RedisStreamsConsumer(
        getLogger("test"), _config(**config), retry_manager, provider=provider
    )
    await consumer.start(handler)
    return consumer


class TestAcknowledgement:
    async def test_handled_messages_are_acknowledged_and_leave_nothing_pending(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider, _event(0), _event(1), _event(2))
        handler = Recorder()
        consumer = await _start(provider, handler, retry_manager)

        async def done() -> bool:
            return handler.seen == [0, 1, 2] and await _pending(provider) == 0

        await _until(done)
        await consumer.stop()


class TestPoisonMessages:
    async def test_malformed_json_is_acknowledged_and_later_messages_still_flow(
        self, provider, retry_manager
    ) -> None:
        # Create the group first so the raw entry lands after it.
        await _produce(provider)
        client = provider.get_client()
        await client.xadd(TOPIC, {"value": "{not json"})
        await _produce(provider, _event(1))
        handler = Recorder()
        consumer = await _start(provider, handler, retry_manager)

        async def done() -> bool:
            return handler.seen == [1] and await _pending(provider) == 0

        await _until(done)
        await consumer.stop()

    async def test_an_envelope_without_event_type_is_acknowledged_without_retries(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider, {"payload": {"i": 0}}, _event(1))
        handler = Recorder()
        consumer = await _start(provider, handler, retry_manager)

        async def done() -> bool:
            return handler.seen == [1] and await _pending(provider) == 0

        await _until(done)
        await consumer.stop()

    async def test_json_that_is_not_an_object_is_given_up_on_after_the_retry_budget(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider)
        await provider.get_client().xadd(TOPIC, {"value": json.dumps([1, 2, 3])})
        handler = Recorder()
        attempts: list[int] = []
        count_failure = retry_manager.increment_and_check

        async def counting_increment(message_id: str, max_attempts: int) -> tuple[int, bool]:
            result = await count_failure(message_id, max_attempts)
            attempts.append(result[0])
            return result

        retry_manager.increment_and_check = counting_increment
        consumer = await _start(provider, handler, retry_manager)

        await _until(lambda: _all_read_and_settled(provider))
        await consumer.stop()
        assert handler.seen == []
        # Each attempt was counted and the entry was dropped only on the last one.
        assert attempts == [1, 2, 3]


class TestRetries:
    async def test_a_transient_failure_is_retried_when_the_stream_goes_idle(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider, _event(0), _event(1))
        handler = Recorder(fail={0: ConnectionError("graph database unreachable")})
        consumer = await _start(provider, handler, retry_manager)

        async def done() -> bool:
            return await _pending(provider) == 0 and handler.seen.count(0) == 2

        await _until(done)
        await consumer.stop()
        # The failure did not hold back the message behind it.
        assert handler.seen[:2] == [0, 1]
        assert handler.seen == [0, 1, 0]
        leftover = [k async for k in provider.get_client().scan_iter(match="messaging:*")]
        assert leftover == []

    async def test_a_message_that_keeps_failing_is_tried_max_attempts_times_then_dropped(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider, _event(0))
        handler = Recorder(fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(provider, handler, retry_manager)

        await _until(lambda: _all_read_and_settled(provider))
        await asyncio.sleep(0.2)
        await consumer.stop()
        assert handler.seen == [0, 0, 0]

    async def test_a_handler_that_returns_false_is_retried_too(self, provider, retry_manager) -> None:
        await _produce(provider, _event(0))
        handler = Recorder(return_false={0})
        consumer = await _start(provider, handler, retry_manager)

        await _until(lambda: _all_read_and_settled(provider))
        await consumer.stop()
        assert handler.seen == [0, 0, 0]

    async def test_a_terminal_handler_error_is_acknowledged_at_once(self, provider, retry_manager) -> None:
        await _produce(provider, _event(0))
        handler = Recorder(fail={0: FileNotFoundError("gone")}, fail_times=99)
        consumer = await _start(provider, handler, retry_manager)

        await _until(lambda: _all_read_and_settled(provider))
        await asyncio.sleep(0.2)
        await consumer.stop()
        assert handler.seen == [0]

    async def test_without_a_retry_manager_redis_delivery_counts_cap_the_retries(self, provider) -> None:
        await _produce(provider, _event(0))
        handler = Recorder(fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(provider, handler, retry_manager=None)

        await _until(lambda: _all_read_and_settled(provider))
        await consumer.stop()
        # Retried at least once, never beyond the budget.
        assert 2 <= handler.seen.count(0) <= 3


class TestShutdownAndRestart:
    async def test_stop_during_a_handler_leaves_the_message_pending_for_the_next_start(
        self, provider, retry_manager
    ) -> None:
        await _produce(provider, _event(0))
        entered = asyncio.Event()

        async def slow_handler(message: StreamMessage) -> bool:
            entered.set()
            await asyncio.sleep(30)
            return True

        consumer = await _start(provider, slow_handler, retry_manager)
        await asyncio.wait_for(entered.wait(), 5)
        await consumer.stop()
        assert consumer.consume_task.done()
        assert consumer.redis is None
        assert await _pending(provider) == 1

        handler = Recorder()
        restarted = await _start(provider, handler, retry_manager)

        async def done() -> bool:
            return handler.seen == [0] and await _pending(provider) == 0

        await _until(done)
        await restarted.stop()

    async def test_a_disposable_group_is_removed_on_shutdown(self, provider) -> None:
        consumer = await _start(provider, Recorder(), ephemeral_group=True, group_id="broadcast-1")
        await asyncio.sleep(0.05)
        groups = await provider.get_client().xinfo_groups(TOPIC)
        assert [g["name"] for g in groups] == ["broadcast-1"]
        await consumer.stop()
        assert await provider.get_client().xinfo_groups(TOPIC) == []

    async def test_a_lasting_group_survives_shutdown(self, provider) -> None:
        consumer = await _start(provider, Recorder())
        await asyncio.sleep(0.05)
        await consumer.stop()
        groups = await provider.get_client().xinfo_groups(TOPIC)
        assert [g["name"] for g in groups] == [GROUP]
