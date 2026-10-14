"""Both indexing consumers hold a connector to its share of the index slots
while another connector is waiting, and let it have them all when not.

The dispatch loop is run for real, down to ``_start_processing_task``; only
the hand-off to the worker loop is replaced, with futures the test finishes
by hand. That keeps the counting honest: a record counts against its
connector from dispatch until its task's done-callback runs.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig

# The legacy (no governor) single pool. Two waiting connectors split it 2/2.
SLOTS = 4


@pytest.fixture(autouse=True)
def _slots(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", str(SLOTS))
    # Read-ahead far above the slot count, as in production, so the only
    # thing that can stop a connector short is its share.
    monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "100")


@pytest.fixture
def futures(monkeypatch) -> list[Future]:
    """Every task handed to the worker loop, as an unfinished future."""
    started: list[Future] = []

    def fake_run_coroutine_threadsafe(coro, loop) -> Future:
        future: Future = Future()
        started.append(future)
        return future

    monkeypatch.setattr(
        "asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe
    )
    return started


def _fair_config(**overrides) -> FairSchedulerConfig:
    defaults = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "max_buffered_messages": 200,
        "max_per_entity_messages": 100,
    }
    defaults.update(overrides)
    return FairSchedulerConfig(**defaults)


def _payload(connector_id: str, record_id: str) -> dict:
    return {
        "recordId": record_id,
        "orgId": "org-a",
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{record_id}",
        "extension": "md",
        "mimeType": "text/markdown",
    }


# --------------------------------------------------------------------------
# Redis Streams
# --------------------------------------------------------------------------


def _redis_consumer() -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("slot-share-redis"),
        RedisStreamsConfig(
            host="h", port=6379, group_id="g", topics=["record-events"], batch_size=10
        ),
        fair_scheduler_config=_fair_config(),
    )
    consumer.running = True
    consumer.worker_loop = MagicMock()
    consumer._process_message_wrapper = MagicMock()
    return consumer


async def _redis_buffer(consumer, connector_id: str, count: int, start: int) -> None:
    for i in range(count):
        envelope = {
            "eventType": "newRecord",
            "payload": _payload(connector_id, f"{connector_id}-{i}"),
            "requestId": f"{connector_id}-{i}",
        }
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", f"{start + i}-0", {"value": json.dumps(envelope)}
        )


def _redis_dispatched(consumer, connector_id: str) -> int:
    return consumer._slot_share.in_flight(("org-a", connector_id), "index")


class TestRedisStreams:
    async def test_busy_connector_is_held_to_its_share_while_another_waits(
        self, futures
    ) -> None:
        consumer = _redis_consumer()
        await _redis_buffer(consumer, "github", 20, start=0)
        await _redis_buffer(consumer, "slack", 20, start=100)

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        # Counting records alone would have dispatched all 40.
        assert len(futures) == SLOTS
        assert _redis_dispatched(consumer, "github") == SLOTS // 2
        assert _redis_dispatched(consumer, "slack") == SLOTS // 2

    async def test_a_finished_record_frees_its_connectors_place(self, futures) -> None:
        consumer = _redis_consumer()
        await _redis_buffer(consumer, "github", 20, start=0)
        await _redis_buffer(consumer, "slack", 20, start=100)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        # Dispatch alternates, GitHub first: futures[0] is a GitHub record.
        futures[0].set_result(True)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert len(futures) == SLOTS + 1
        assert _redis_dispatched(consumer, "github") == SLOTS // 2
        assert _redis_dispatched(consumer, "slack") == SLOTS // 2

    async def test_a_connector_alone_gets_every_slot(self, futures) -> None:
        consumer = _redis_consumer()
        await _redis_buffer(consumer, "github", 20, start=0)

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert len(futures) == 20
        assert _redis_dispatched(consumer, "github") == 20

    async def test_a_failed_hand_off_does_not_count(self, monkeypatch) -> None:
        consumer = _redis_consumer()
        monkeypatch.setattr(
            "asyncio.run_coroutine_threadsafe",
            MagicMock(side_effect=RuntimeError("loop closed")),
        )
        await _redis_buffer(consumer, "github", 2, start=0)
        await _redis_buffer(consumer, "slack", 2, start=100)

        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert _redis_dispatched(consumer, "github") == 0
        assert _redis_dispatched(consumer, "slack") == 0


# --------------------------------------------------------------------------
# Kafka
# --------------------------------------------------------------------------


def _kafka_consumer() -> IndexingKafkaConsumer:
    consumer = IndexingKafkaConsumer(
        logging.getLogger("slot-share-kafka"),
        KafkaConsumerConfig(
            topics=["record-events"],
            client_id="idx-consumer",
            group_id="idx-group",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["broker:9092"],
            ssl=False,
            sasl=None,
        ),
        fair_scheduler_config=_fair_config(parallel_partitions=True),
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.running = True
    consumer.worker_loop = MagicMock()
    consumer._IndexingKafkaConsumer__process_message_wrapper = MagicMock()
    return consumer


async def _kafka_buffer(consumer, connector_id: str, count: int, partition: int) -> None:
    tp = TopicPartition("record-events", partition)
    for offset in range(count):
        envelope = {
            "eventType": "newRecord",
            "payload": _payload(connector_id, f"{connector_id}-{offset}"),
            "requestId": f"{connector_id}-{offset}",
        }
        message = MagicMock()
        message.topic = "record-events"
        message.partition = partition
        message.offset = offset
        message.value = json.dumps(envelope).encode()
        await consumer._IndexingKafkaConsumer__enqueue_message(tp, message)


def _kafka_dispatched(consumer, connector_id: str) -> int:
    return consumer._slot_share.in_flight(("org-a", connector_id), "index")


class TestKafka:
    async def test_busy_connector_is_held_to_its_share_while_another_waits(
        self, futures
    ) -> None:
        consumer = _kafka_consumer()
        await _kafka_buffer(consumer, "github", 20, partition=0)
        await _kafka_buffer(consumer, "slack", 20, partition=1)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert len(futures) == SLOTS
        assert _kafka_dispatched(consumer, "github") == SLOTS // 2
        assert _kafka_dispatched(consumer, "slack") == SLOTS // 2

    async def test_a_connector_alone_gets_every_slot(self, futures) -> None:
        consumer = _kafka_consumer()
        await _kafka_buffer(consumer, "github", 20, partition=0)

        await consumer._IndexingKafkaConsumer__dispatch_phase()

        assert len(futures) == 20
        assert _kafka_dispatched(consumer, "github") == 20
