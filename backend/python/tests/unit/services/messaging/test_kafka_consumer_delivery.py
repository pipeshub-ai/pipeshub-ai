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
        self.succeeded: list[int] = []
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
        self.succeeded.append(i)
        return True


class FlakyRetryManager(RetryManager):
    """A real RetryManager whose first ``failures`` calls to ``methods`` time out."""

    def __init__(self, failures: int, methods: tuple[str, ...] = ("increment_and_check", "clear", "get_count")) -> None:
        super().__init__(logging.getLogger("test"), redis_client=fakeredis.aioredis.FakeRedis())
        self.failures_left = failures
        self.methods = methods
        self.failed_calls = 0

    def _maybe_fail(self, method: str) -> None:
        if method in self.methods and self.failures_left > 0:
            self.failures_left -= 1
            self.failed_calls += 1
            raise TimeoutError("Redis timed out")

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        self._maybe_fail("increment_and_check")
        return await super().increment_and_check(message_id, max_attempts)

    async def clear(self, message_id: str) -> None:
        self._maybe_fail("clear")
        await super().clear(message_id)

    async def get_count(self, message_id: str) -> int:
        self._maybe_fail("get_count")
        return await super().get_count(message_id)


class FailingCommits:
    """Makes a fake consumer's first ``times`` commits fail, as a broker outage would."""

    def __init__(self, consumer: object, times: int) -> None:
        self.real_commit = consumer.commit
        self.times = times
        self.failed = 0
        consumer.commit = self

    async def __call__(self, offsets: dict | None = None) -> None:
        if self.failed < self.times:
            self.failed += 1
            raise ConnectionError("broker unavailable for commit")
        await self.real_commit(offsets)


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

    async def test_a_message_that_keeps_failing_is_tried_max_attempts_times_then_skipped(
        self, broker, retry_manager
    ) -> None:
        broker.produce(TOPIC, _event(0))
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: handler.seen.count(0) >= 3, timeout=3)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await consumer.stop()

    async def test_a_failure_in_the_middle_of_a_batch_is_retried_before_anything_after_it_is_committed(
        self, broker, retry_manager
    ) -> None:
        for i in range(4):
            broker.produce(TOPIC, _event(i))
        handler = Recorder(broker, fail={1: ConnectionError("graph database unreachable")})
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)
        await consumer.stop()

        assert handler.seen == [0, 1, 1, 2, 3]
        # The retry of 1 ran with only 0 committed: nothing jumped past it.
        assert handler.committed_when_seen[2] == 1
        assert await retry_manager.get_count(f"{TOPIC}-0-1") == 0

    async def test_a_restart_after_a_failure_still_delivers_the_failed_message_and_those_after_it(
        self, broker, retry_manager, monkeypatch
    ) -> None:
        # A long pause between attempts, so the stop lands before the retry.
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "30000")
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        handler = Recorder(broker, fail={0: ConnectionError("down")})
        first = await _start(handler, retry_manager)
        await _until(lambda: 0 in handler.seen)
        # A later message must not be committed past the failed one.
        broker.produce(TOPIC, _event(3))
        await _settle()
        await first.stop()
        assert broker.committed_offset(GROUP, TOPIC) is None

        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
        second = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 4)
        await second.stop()
        assert handler.seen == [0, 0, 1, 2, 3]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0

    async def test_giving_up_on_a_message_still_delivers_the_ones_after_it(self, broker, retry_manager) -> None:
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
        await _settle()
        await consumer.stop()

        assert handler.seen == [0, 0, 0, 1, 2]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0

    async def test_a_failure_on_one_partition_neither_blocks_nor_skips_another(
        self, broker, retry_manager
    ) -> None:
        broker.produce(TOPIC, _event(0), partition=0)
        broker.produce(TOPIC, _event(1), partition=0)
        broker.produce(TOPIC, _event(10), partition=1)
        broker.produce(TOPIC, _event(11), partition=1)
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=2)
        consumer = await _start(handler, retry_manager)
        await _until(
            lambda: broker.committed_offset(GROUP, TOPIC, 0) == 2
            and broker.committed_offset(GROUP, TOPIC, 1) == 2
        )
        await consumer.stop()

        # seen records every delivery: two failed attempts of 0, then its success.
        assert [i for i in handler.seen if i < 10] == [0, 0, 0, 1]
        assert [i for i in handler.succeeded if i < 10] == [0, 1]
        assert [i for i in handler.seen if i >= 10] == [10, 11]
        last_attempt_of_0 = len(handler.seen) - 1 - handler.seen[::-1].index(0)
        assert handler.seen.index(11) < last_attempt_of_0


    async def test_records_arriving_on_a_healthy_partition_during_a_retry_pause_are_not_held_back(
        self, broker, retry_manager, monkeypatch
    ) -> None:
        # A long retry pause, so anything that waits for it misses the deadline below.
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "5000")
        broker.produce(TOPIC, _event(0), partition=0)
        handler = Recorder(broker, fail={0: ConnectionError("down")})
        consumer = await _start(handler, retry_manager)
        await _until(lambda: 0 in handler.seen)

        broker.produce(TOPIC, _event(10), partition=1)
        await _until(lambda: 10 in handler.succeeded, timeout=2)
        assert handler.seen.count(0) == 1, "partition 0 is still waiting out its pause"
        assert broker.committed_offset(GROUP, TOPIC, 1) == 1
        await consumer.stop()

    async def test_a_retry_resumes_only_the_partition_it_paused(self, broker, retry_manager) -> None:
        broker.produce(TOPIC, _event(0), partition=0)
        broker.produce(TOPIC, _event(10), partition=1)
        handler = Recorder(broker, fail={0: ConnectionError("down")})
        consumer = await _start(handler, retry_manager)
        await _until(lambda: 10 in handler.seen)
        # Something else (for example sync backpressure) pauses partition 1.
        other = next(tp for tp in broker.logs if tp.partition == 1)
        broker.consumers[-1].pause(other)
        await _until(lambda: 0 in handler.succeeded)
        await _settle()
        await consumer.stop()
        assert broker.consumers[-1].paused() == {other}


class TestRetryBookkeepingFailures:
    """Redis holds the retry counts; losing it must not lose messages or loop forever."""

    async def test_a_redis_timeout_while_counting_a_retry_does_not_skip_the_message(self, broker) -> None:
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        retry_manager = FlakyRetryManager(failures=1, methods=("increment_and_check",))
        handler = Recorder(broker, fail={0: ConnectionError("graph database unreachable")})
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
        await consumer.stop()

        assert retry_manager.failed_calls == 1
        assert handler.seen == [0, 0, 1, 2]
        assert handler.succeeded == [0, 1, 2]

    async def test_with_redis_down_a_failing_message_is_still_given_up_on_after_max_attempts(
        self, broker
    ) -> None:
        for i in range(3):
            broker.produce(TOPIC, _event(i))
        retry_manager = FlakyRetryManager(failures=10**9)
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 3)
        await _settle()
        await consumer.stop()

        assert handler.seen == [0, 0, 0, 1, 2]
        assert handler.succeeded == [1, 2]


    async def test_giving_up_after_repeated_unexpected_errors_commits_that_offset(
        self, broker, retry_manager
    ) -> None:
        # An error outside the handler (here, while classifying its failure)
        # takes the loop's catch-all path rather than the classified one.
        broker.produce(TOPIC, _event(0))
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        with patch.object(
            consumer_module.MessageErrorClassifier, "classify_by_exception", side_effect=RuntimeError("boom")
        ):
            consumer = await _start(handler, retry_manager)
            await _until(lambda: handler.seen.count(0) == 3)
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
            broker.produce(TOPIC, _event(1))
            await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 2)
            await _settle()
            await consumer.stop()

        assert handler.seen == [0, 0, 0, 1]
        # Message 1 ran with 0 already committed, not with a gap before it.
        assert handler.committed_when_seen[-1] == 1


    async def test_a_message_given_up_on_whose_commit_fails_is_not_handled_again_after_a_restart(
        self, broker, retry_manager
    ) -> None:
        broker.produce(TOPIC, _event(0))
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        first = await _start(handler, retry_manager)
        commits = FailingCommits(broker.consumers[-1], times=99)
        await _until(lambda: handler.seen.count(0) == 3 and commits.failed == 1)
        await _settle()
        await first.stop()
        assert broker.committed_offset(GROUP, TOPIC) is None

        second = await _start(handler, retry_manager)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await _settle()
        await second.stop()
        assert handler.seen == [0, 0, 0]
        assert await retry_manager.get_count(f"{TOPIC}-0-0") == 0

    async def test_with_redis_down_a_message_given_up_on_whose_commit_fails_is_not_handled_again(
        self, broker
    ) -> None:
        broker.produce(TOPIC, _event(0))
        retry_manager = FlakyRetryManager(failures=10**9)
        handler = Recorder(broker, fail={0: ConnectionError("down")}, fail_times=99)
        consumer = await _start(handler, retry_manager)
        fake = broker.consumers[-1]
        commits = FailingCommits(fake, times=1)
        await _until(lambda: handler.seen.count(0) == 3 and commits.failed == 1)
        # A rebalance hands the partition back from before the failed commit.
        fake.seek(next(iter(broker.logs)), 0)
        await _until(lambda: broker.committed_offset(GROUP, TOPIC) == 1)
        await _settle()
        await consumer.stop()
        assert handler.seen == [0, 0, 0]


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
