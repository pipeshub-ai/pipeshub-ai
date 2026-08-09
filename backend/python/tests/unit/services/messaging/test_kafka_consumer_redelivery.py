"""A handler returning False means "redeliver me", and that has to mean the
same thing on both brokers.

The Redis Streams consumer honours it natively: an un-ACKed message stays in
the pending-entries list and is re-read. Kafka has no PEL, and simply
declining to commit is not enough -- `getmany()` has already advanced the
consumer's in-memory fetch position, so without an explicit `seek()` the next
poll returns the *following* offset and the failure is silently dropped until
a rebalance or restart.

That asymmetry is not academic: `AppEventConsumer` returns False when the
event fan-out hits a transient store error, so on Kafka a brief database blip
used to discard app events rather than retry them.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as consumer_module
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer

TOPIC = "app_events"
PARTITION = 0
OFFSET = 42


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=[TOPIC],
        client_id="c",
        group_id="g",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


def _record() -> MagicMock:
    record = MagicMock()
    record.topic = TOPIC
    record.partition = PARTITION
    record.offset = OFFSET
    record.value = json.dumps({"eventType": "appEvent", "payload": {}}).encode()
    return record


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff pauses instead of serving them, so the suite asserts on
    the delay without waiting it out."""
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(seconds: float) -> None:
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(consumer_module.asyncio, "sleep", _fake_sleep)
    return recorded


def _consumer_with_one_failing_message(handler) -> tuple[KafkaMessagingConsumer, MagicMock]:
    consumer = KafkaMessagingConsumer(logging.getLogger("t"), _config())
    consumer.message_handler = handler
    consumer.running = True

    topic_partition = MagicMock()
    batches = [{topic_partition: [_record()]}]

    async def _getmany(**_kwargs: object) -> dict:
        if batches:
            return batches.pop()
        consumer.running = False
        return {}

    kafka_client = MagicMock()
    kafka_client.getmany = AsyncMock(side_effect=_getmany)
    kafka_client.commit = AsyncMock()
    kafka_client.seek = MagicMock()
    kafka_client.stop = AsyncMock()
    consumer.consumer = kafka_client
    return consumer, topic_partition


async def _run_loop(consumer: KafkaMessagingConsumer) -> None:
    # __consume_loop is name-mangled; reach it the way the class does.
    await consumer._KafkaMessagingConsumer__consume_loop()


@pytest.mark.asyncio
async def test_a_failing_handler_rewinds_the_partition_and_does_not_commit(sleeps) -> None:
    async def handler(_message) -> bool:
        return False

    consumer, topic_partition = _consumer_with_one_failing_message(handler)
    consumer.retry_manager = AsyncMock()
    consumer.retry_manager.increment_and_check = AsyncMock(return_value=(1, False))

    await _run_loop(consumer)

    consumer.consumer.seek.assert_called_once_with(topic_partition, OFFSET)
    consumer.consumer.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_succeeding_handler_commits_and_does_not_rewind(sleeps) -> None:
    async def handler(_message) -> bool:
        return True

    consumer, topic_partition = _consumer_with_one_failing_message(handler)

    await _run_loop(consumer)

    consumer.consumer.seek.assert_not_called()
    consumer.consumer.commit.assert_awaited_once_with({topic_partition: OFFSET + 1})


@pytest.mark.asyncio
async def test_exhausted_retries_commit_instead_of_rewinding_forever(sleeps) -> None:
    """The rewind must be bounded, or a permanently failing message parks the
    partition on the same offset for good."""

    async def handler(_message) -> bool:
        return False

    consumer, topic_partition = _consumer_with_one_failing_message(handler)
    consumer.retry_manager = AsyncMock()
    consumer.retry_manager.increment_and_check = AsyncMock(return_value=(5, True))

    await _run_loop(consumer)

    consumer.consumer.seek.assert_not_called()
    consumer.consumer.commit.assert_awaited_once_with({topic_partition: OFFSET + 1})


@pytest.mark.asyncio
async def test_the_rewind_waits_out_a_bounded_backoff_window(sleeps) -> None:
    """Retrying immediately burns every delivery attempt within milliseconds,
    which turns a recoverable outage into a dead-letter -- but this consumer
    processes sequentially, so the wait also has to stay bounded."""

    async def handler(_message) -> bool:
        return False

    consumer, _ = _consumer_with_one_failing_message(handler)
    consumer.retry_manager = AsyncMock()
    consumer.retry_manager.increment_and_check = AsyncMock(return_value=(1, False))

    await _run_loop(consumer)

    backoffs = [s for s in sleeps if s > 1]
    assert backoffs, "expected a backoff pause before redelivery"
    assert max(backoffs) <= consumer_module._MAX_REDELIVERY_BACKOFF_SECONDS
