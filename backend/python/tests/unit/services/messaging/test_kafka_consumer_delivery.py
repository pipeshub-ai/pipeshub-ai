"""Delivery guarantees of the simple Kafka consumer (``KafkaMessagingConsumer``).

This consumer carries the connectors service's entity events (organisations,
users, apps) and sync events, and the query service's AI-configuration
events. These tests run the real consumer loop, the real ``RetryManager`` on
an in-memory Redis and the real error classifier; only the broker is faked,
by ``tests.support.fake_kafka``, which keeps Kafka's rule that a fetched
record is never handed out again unless the consumer seeks back to it.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import aiokafka
import fakeredis.aioredis
import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as consumer_module
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.retry_manager import RetryManager
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.services.messaging.config import StreamMessage

TOPIC = "entity-events"
GROUP = "entity_consumer_group"


def test_the_kafka_client_library_is_real_not_a_conftest_stand_in() -> None:
    # tests/conftest.py swaps aiokafka for a MagicMock when it is missing,
    # which would make every test below pass without exercising anything.
    assert not isinstance(sys.modules["aiokafka"], MagicMock)
    assert aiokafka.__file__ and aiokafka.__file__.endswith(".py")
    assert isinstance(consumer_module.AIOKafkaConsumer, type)


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=[TOPIC],
        client_id="connectors-test",
        group_id=GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["kafka:9092"],
    )


def _event(i: int, event_type: str = "userAdded") -> dict:
    return {"eventType": event_type, "payload": {"i": i, "orgId": "org-a"}, "timestamp": 1}


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


async def _settle(seconds: float = 0.4) -> None:
    """Give the loop a few idle polls (each empty poll sleeps 0.1s)."""
    await asyncio.sleep(seconds)


@pytest.fixture
def broker() -> Iterator[FakeKafkaBroker]:
    broker = FakeKafkaBroker()
    with patch.object(consumer_module, "AIOKafkaConsumer", broker.consumer_factory()):
        yield broker


@pytest.fixture
def retry_manager() -> RetryManager:
    return RetryManager(logging.getLogger("test"), redis_client=fakeredis.aioredis.FakeRedis())


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")


class Recorder:
    """A handler that records what it saw, and can fail on chosen messages."""

    def __init__(self, broker: FakeKafkaBroker, fail: dict[int, BaseException] | None = None,
                 fail_times: int = 1) -> None:
        self.broker = broker
        self.seen: list[int] = []
        self.committed_when_seen: list[int | None] = []
        self.fail = fail or {}
        self.fail_times = fail_times
        self._failures: dict[int, int] = {}

    async def __call__(self, message: StreamMessage) -> bool:
        i = message.payload["i"]
        self.seen.append(i)
        self.committed_when_seen.append(self.broker.committed_offset(GROUP, TOPIC))
        if i in self.fail and self._failures.get(i, 0) < self.fail_times:
            self._failures[i] = self._failures.get(i, 0) + 1
            raise self.fail[i]
        return True


async def _start(handler, retry_manager=None) -> KafkaMessagingConsumer:
    consumer = KafkaMessagingConsumer(logging.getLogger("test"), _config(), retry_manager)
    await consumer.start(handler)
    return consumer


class TestOffsetsAreCommittedOnlyAfterSuccess:
    async def test_each_offset_is_committed_after_its_handler_returns(self, broker, retry_manager) -> None:
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
        await consumer.stop()

        assert handler.seen == [0, 1, 2]
        # While message N was being handled, only the messages before it
        # were committed: a crash mid-handler redelivers N.
        assert handler.committed_when_seen == [None, 1, 2]

    async def test_a_restart_replays_nothing_that_was_already_committed(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, _event(0))
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker)
        first = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await first.stop()

        broker.produce(TOPIC, _event(2))
        second = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
        await second.stop()
        assert handler.seen == [0, 1, 2]

    async def test_without_a_retry_manager_a_failure_is_committed_rather_than_looping(self, broker) -> None:
        broker.produce(TOPIC, _event(0))
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker, fail={0: ConnectionError("down")})
        consumer = await _start(handler, retry_manager=None)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await consumer.stop()
        assert handler.seen == [0, 1]


class TestPoisonMessages:
    async def test_malformed_json_is_committed_and_does_not_block_the_partition(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, b"{not json")
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await consumer.stop()
        assert handler.seen == [1]

    async def test_an_envelope_without_event_type_is_committed_without_retries(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, {"payload": {"i": 0}})
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await consumer.stop()
        assert handler.seen == [1]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0

    async def test_bytes_that_are_not_utf8_are_committed_and_do_not_block_later_messages(
        self, broker, retry_manager
    ) -> None:
        broker.produce(TOPIC, b"\xff\xfe\x00 not text")
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await consumer.stop()
        assert handler.seen == [1]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0

    async def test_a_double_encoded_envelope_is_still_delivered(self, broker, retry_manager) -> None:
        import json

        broker.produce(TOPIC, json.dumps(json.dumps(_event(0))).encode())
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await consumer.stop()
        assert handler.seen == [0]

    async def test_a_handler_error_classified_terminal_is_committed_at_once(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, _event(0))
        broker.produce(TOPIC, _event(1))
        handler = Recorder(broker, fail={0: FileNotFoundError("missing")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
        await consumer.stop()
        assert handler.seen == [0, 1]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0


class TestDuplicates:
    async def test_a_message_redelivered_after_a_rebalance_is_not_handled_twice(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, _event(0))
        handler = Recorder(broker)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)

        # A rebalance can hand this consumer an offset it already processed.
        broker.consumers[-1].seek(next(iter(broker.logs)), 0)
        await _settle()
        await consumer.stop()
        assert handler.seen == [0]


class TestTransientFailures:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left for a follow-up because consumer.py is part of open PR #3128: "
            "on a transient failure the loop stops reading that batch and waits for "
            "Kafka to redeliver, but Kafka never redelivers a fetched record without "
            "a seek, so the failed message and the rest of its batch are skipped, and "
            "the next successful commit moves past them for good."
        ),
    )
    async def test_a_transient_failure_does_not_lose_that_message_or_the_ones_after_it(
        self, broker, retry_manager
    ) -> None:
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        handler = Recorder(broker, fail={0: ConnectionError("graph database unreachable")})
        consumer = await _start(handler, retry_manager)
        await _until(lambda: 0 in handler.seen)
        await _settle()
        broker.produce(TOPIC, _event(3))
        await _until(lambda: 3 in handler.seen)
        await _settle()
        await consumer.stop()
        # Message 0 must get its second, successful attempt, and nothing
        # after it may be skipped.
        assert handler.seen.count(0) == 2
        assert {1, 2, 3} <= set(handler.seen)

    @pytest.mark.xfail(
        strict=True,
        reason="Same bug as above: a failed message is never offered again, so it is never retried.",
    )
    async def test_a_message_that_keeps_failing_is_tried_max_attempts_times_then_skipped(
        self, broker, retry_manager
    ) -> None:
        broker.produce(TOPIC, _event(0))
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: handler.seen.count(0) >= 3, timeout=3)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await consumer.stop()


class TestGracefulShutdown:
    async def test_stop_during_a_handler_leaves_the_message_for_the_next_start(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, _event(0))
        entered = asyncio.Event()
        seen: list[int] = []

        async def slow_handler(message: StreamMessage) -> bool:
            seen.append(message.payload["i"])
            entered.set()
            await asyncio.sleep(30)
            return True

        consumer = await _start(slow_handler, retry_manager)
        await asyncio.wait_for(entered.wait(), 5)
        await consumer.stop()

        assert consumer.is_running() is False
        assert consumer.consume_task.done()
        assert broker.consumers[-1].stopped is True
        assert broker.committed_offset(GROUP, TOPIC) is None

        handler = Recorder(broker)
        restarted = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await restarted.stop()
        assert handler.seen == [0]

    async def test_stop_before_any_message_is_clean(self, broker) -> None:
        consumer = await _start(Recorder(broker))
        await _settle(0.15)
        await consumer.stop()
        assert consumer.consume_task.done()
        assert broker.consumers[-1].stopped is True
