"""Fair scheduling and lanes against a real Kafka broker.

Everything up to here has run against in-process fakes. They are faithful on
the points that matter, but four of the bugs found in review lived in exactly
the seams a fake cannot exercise -- real offset commits, real partition
assignment, real pause/resume. These tests use the real producer, the real
consumer, and read committed offsets back from the broker.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.lanes.hash_router import KafkaLaneRouter
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    committed_offsets,
    create_kafka_topic,
    delete_kafka_topic,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PARTITIONS = 4
_BIG = 120
_SMALL = 8


def _fair(**overrides) -> FairSchedulerConfig:
    base = dict(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=200,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    )
    base.update(overrides)
    return FairSchedulerConfig(**base)


async def _publish(bootstrap: str, topic: str, records: list[tuple[str, str]]) -> None:
    """Publish through the real lane-aware producer, so placement is the
    broker's own partitioner acting on the lane key."""
    inner = KafkaMessagingProducer(
        logging.getLogger("it-producer"),
        KafkaProducerConfig(bootstrap_servers=[bootstrap], client_id="it-producer"),
    )
    producer = LaneAwareProducer(
        logging.getLogger("it-producer"),
        inner,
        KafkaLaneRouter(_PARTITIONS),
        LaneConfig(lane_count=_PARTITIONS, laned_topics=(topic,)),
    )
    await producer.initialize()
    try:
        for connector_id, record_id in records:
            await producer.send_event(
                topic=topic,
                event_type="newRecord",
                payload={
                    "recordId": record_id,
                    "orgId": "org-1",
                    "connectorId": connector_id,
                    "extension": "txt",
                    "mimeType": "text/plain",
                },
            )
    finally:
        await producer.cleanup()


def _consumer(bootstrap: str, topic: str, group: str, **fair_overrides):
    return IndexingKafkaConsumer(
        logging.getLogger("it-consumer"),
        KafkaConsumerConfig(
            topics=[topic],
            client_id=f"{group}-client",
            group_id=group,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=[bootstrap],
        ),
        fair_scheduler_config=_fair(**fair_overrides),
    )


def _handler(completions: list[str], record_ids: list[str] | None = None):
    async def handle(parsed_message):
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.LIGHT),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        completions.append(parsed_message.payload["connectorId"])
        if record_ids is not None:
            record_ids.append(parsed_message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


async def _drain(consumer, completions: list, expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while len(completions) < expected:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"drained {len(completions)} of {expected} before timeout"
            )
        await asyncio.sleep(0.2)


@pytest.fixture
async def topic(kafka_available, unique_suffix):
    name = f"record-events-{unique_suffix}"
    await create_kafka_topic(kafka_available, name, _PARTITIONS)
    yield name
    await delete_kafka_topic(kafka_available, name)


class TestFairnessOnARealBroker:
    async def test_small_user_is_not_starved_by_a_segregated_backlog(
        self, kafka_available, topic, unique_suffix
    ):
        """The scenario the whole feature exists for, on a real broker: one
        user's entire sync is published before another user's first record."""
        records = [("user-a", f"big-{i}") for i in range(_BIG)]
        records += [("user-b", f"small-{i}") for i in range(_SMALL)]
        await _publish(kafka_available, topic, records)

        group = f"it-fair-{unique_suffix}"
        consumer = _consumer(kafka_available, topic, group)
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(consumer, completions, _BIG + _SMALL)
        finally:
            await consumer.stop()

        assert completions.count("user-b") == _SMALL
        assert completions.count("user-a") == _BIG

        last_small = max(
            i for i, conn in enumerate(completions) if conn == "user-b"
        )
        assert last_small < (_BIG + _SMALL) // 2, (
            "the small user should not be stuck behind the whole backlog "
            f"(last completion at {last_small} of {len(completions)})"
        )

    async def test_lane_key_puts_one_connector_on_one_partition(
        self, kafka_available, topic
    ):
        """Placement is the broker's, not ours -- assert the real thing."""
        from aiokafka import AIOKafkaConsumer, TopicPartition

        await _publish(
            kafka_available, topic, [("user-a", f"r-{i}") for i in range(30)]
        )

        reader = AIOKafkaConsumer(
            topic, bootstrap_servers=kafka_available, auto_offset_reset="earliest"
        )
        await reader.start()
        try:
            seen: set[int] = set()
            deadline = asyncio.get_running_loop().time() + 30.0
            count = 0
            while count < 30 and asyncio.get_running_loop().time() < deadline:
                batch = await reader.getmany(timeout_ms=1000, max_records=30)
                for tp, messages in batch.items():
                    assert isinstance(tp, TopicPartition)
                    seen.add(tp.partition)
                    count += len(messages)
        finally:
            await reader.stop()

        assert count == 30
        assert len(seen) == 1, f"one connector should occupy one lane, got {seen}"


class TestCommitWatermarkOnARealBroker:
    async def test_committed_offsets_cover_every_record(
        self, kafka_available, topic, unique_suffix
    ):
        """The number a restart actually resumes from, read back from the
        broker rather than from the consumer's own tracker."""
        total = 40
        await _publish(
            kafka_available,
            topic,
            [(f"user-{i % 3}", f"r-{i}") for i in range(total)],
        )

        group = f"it-commit-{unique_suffix}"
        consumer = _consumer(kafka_available, topic, group)
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(consumer, completions, total)
            # Let the final watermark commits land before reading them back.
            await asyncio.sleep(2.0)
        finally:
            await consumer.stop()

        committed = await committed_offsets(kafka_available, group, topic)
        assert sum(committed.values()) == total, (
            f"committed {committed} should account for all {total} records"
        )

    async def test_a_restart_replays_nothing_that_was_committed(
        self, kafka_available, topic, unique_suffix
    ):
        """Start, drain, stop, start again: a correct watermark means the
        second run has nothing left to do."""
        total = 30
        await _publish(
            kafka_available,
            topic,
            [(f"user-{i % 2}", f"r-{i}") for i in range(total)],
        )
        group = f"it-restart-{unique_suffix}"

        first: list[str] = []
        consumer = _consumer(kafka_available, topic, group)
        await consumer.start(_handler(first))
        try:
            await _drain(consumer, first, total)
            await asyncio.sleep(2.0)
        finally:
            await consumer.stop()
        assert len(first) == total

        second: list[str] = []
        consumer = _consumer(kafka_available, topic, group)
        await consumer.start(_handler(second))
        try:
            await asyncio.sleep(8.0)
        finally:
            await consumer.stop()

        assert second == [], f"restart reprocessed {len(second)} committed records"


class TestParallelPartitionsOnARealBroker:
    async def test_nothing_is_lost_with_parallel_dispatch(
        self, kafka_available, topic, unique_suffix
    ):
        """Out-of-order completion within a partition, against real commits."""
        total = 60
        await _publish(
            kafka_available,
            topic,
            [(f"user-{i % 4}", f"r-{i}") for i in range(total)],
        )

        group = f"it-parallel-{unique_suffix}"
        consumer = _consumer(
            kafka_available, topic, group, parallel_partitions=True
        )
        completions: list[str] = []
        await consumer.start(_handler(completions))
        try:
            await _drain(consumer, completions, total)
            await asyncio.sleep(2.0)
        finally:
            await consumer.stop()

        assert len(completions) == total
        committed = await committed_offsets(kafka_available, group, topic)
        assert sum(committed.values()) == total


class TestCrashRecoveryOnARealBroker:
    async def test_a_mid_flight_restart_loses_nothing(
        self, kafka_available, topic, unique_suffix
    ):
        """Stop the consumer while it is still draining, then bring it back.

        At-least-once is the contract, so duplicates are allowed -- losing a
        record is not. The watermark is what makes that true: it must never
        have committed past work that had not finished.
        """
        total = 80
        expected = {f"r-{i}" for i in range(total)}
        await _publish(
            kafka_available,
            topic,
            [(f"user-{i % 4}", f"r-{i}") for i in range(total)],
        )

        group = f"it-crash-{unique_suffix}"
        seen: list[str] = []

        first_completions: list[str] = []
        consumer = _consumer(kafka_available, topic, group)
        await consumer.start(_handler(first_completions, seen))
        try:
            # Interrupt partway through rather than after a clean drain.
            deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
            while len(seen) < total // 3:
                if asyncio.get_running_loop().time() > deadline:
                    raise AssertionError("consumer never made progress")
                await asyncio.sleep(0.05)
            # Captured before stop(): draining in-flight tasks keeps adding
            # to `seen`, so a bound read after shutdown is a moving target.
            first_run = len(seen)
        finally:
            await consumer.stop()

        assert first_run > 0, "the first run never made progress"
        if len(seen) >= total:
            pytest.skip(
                "the broker drained the whole backlog before shutdown, so "
                "there is no partial state left to recover from"
            )

        consumer = _consumer(kafka_available, topic, group)
        await consumer.start(_handler([], seen))
        try:
            deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
            while set(seen) != expected:
                if asyncio.get_running_loop().time() > deadline:
                    missing = expected - set(seen)
                    raise AssertionError(
                        f"{len(missing)} record(s) never indexed: "
                        f"{sorted(missing)[:10]}"
                    )
                await asyncio.sleep(0.2)
        finally:
            await consumer.stop()

        assert set(seen) == expected
