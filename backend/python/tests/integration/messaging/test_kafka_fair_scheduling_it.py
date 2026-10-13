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
import threading
from typing import TYPE_CHECKING

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
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    OneTopicProducer,
    committed_offsets,
    create_kafka_topic,
    delete_kafka_topic,
    held_handler,
    run_stranded_sweep,
    stop_mid_flight,
    wait_until_held,
)
from tests.support.fake_record_graph import FakeRecordGraph

if TYPE_CHECKING:
    from app.services.messaging.lanes.backlog import LaneBacklog

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


def _lane_producer(bootstrap: str, topic: str) -> LaneAwareProducer:
    inner = KafkaMessagingProducer(
        logging.getLogger("it-producer"),
        KafkaProducerConfig(bootstrap_servers=[bootstrap], client_id="it-producer"),
    )
    return LaneAwareProducer(
        logging.getLogger("it-producer"),
        inner,
        KafkaLaneRouter(_PARTITIONS),
        LaneConfig(lane_count=_PARTITIONS, laned_topics=(topic,)),
    )


async def _publish(bootstrap: str, topic: str, records: list[tuple[str, str]]) -> None:
    """Publish through the real lane-aware producer, so placement is the
    broker's own partitioner acting on the lane key."""
    producer = _lane_producer(bootstrap, topic)
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

        gate = threading.Event()
        parked: list[str] = []
        consumer = _consumer(kafka_available, topic, group)
        await consumer.start(held_handler(seen, total // 3, gate, parked))
        await stop_mid_flight(consumer, gate, parked)

        assert len(set(seen)) < total, "the first run was meant to stop partway"

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


async def _partition_timestamps(bootstrap: str, topic: str) -> dict[int, list[int]]:
    """Per partition, the timestamp of every record in offset order, read by a
    consumer that belongs to no group."""
    from aiokafka import AIOKafkaConsumer, TopicPartition

    partitions = [TopicPartition(topic, p) for p in range(_PARTITIONS)]
    reader = AIOKafkaConsumer(bootstrap_servers=bootstrap, auto_offset_reset="earliest")
    await reader.start()
    try:
        reader.assign(partitions)
        await reader.seek_to_beginning(*partitions)
        end = await reader.end_offsets(partitions)
        timestamps: dict[int, list[int]] = {tp.partition: [] for tp in partitions}
        deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
        while any(len(timestamps[tp.partition]) < end[tp] for tp in partitions):
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("could not read the topic back")
            for tp, batch in (await reader.getmany(timeout_ms=500)).items():
                timestamps[tp.partition].extend(record.timestamp for record in batch)
        return timestamps
    finally:
        await reader.stop()


async def _oldest_uncommitted(bootstrap: str, group: str, topic: str) -> dict[str, float]:
    """Worked out from the log and the group's committed offsets as the broker holds them."""
    timestamps = await _partition_timestamps(bootstrap, topic)
    committed = await committed_offsets(bootstrap, group, topic)
    return {
        f"{topic}-{partition}": float(log[committed.get(partition, 0)])
        for partition, log in timestamps.items()
        if committed.get(partition, 0) < len(log)
    }


async def _backlog_settles(
    consumer, bootstrap: str, group: str, topic: str, *, empty: bool
) -> LaneBacklog:
    """Commits land a moment after the handler returns, so compare against the
    broker read both before and after the backlog itself."""
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while True:
        before = await _oldest_uncommitted(bootstrap, group, topic)
        backlog = await consumer.lane_backlog(topic)
        after = await _oldest_uncommitted(bootstrap, group, topic)
        if before == after == dict(backlog.oldest_waiting_ms) and bool(before) != empty:
            return backlog
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(
                f"backlog {dict(backlog.oldest_waiting_ms)} never matched the broker's {after}"
            )
        await asyncio.sleep(0.5)


class TestLaneBacklogOnARealBroker:
    """What the stranded-record sweep asks the broker before re-sending a record."""

    @pytest.fixture(autouse=True)
    def _one_hour_threshold(self, monkeypatch) -> None:
        monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")

    async def test_each_partition_reports_its_oldest_uncommitted_record_until_it_is_drained(
        self, kafka_available, topic, unique_suffix
    ) -> None:
        records = [("user-a", f"a-{i}") for i in range(12)]
        records += [("user-b", f"b-{i}") for i in range(4)]
        await _publish(kafka_available, topic, records)

        group = f"it-backlog-{unique_suffix}"
        # A low per-key cap, so a partition is paused with records unread.
        consumer = _consumer(kafka_available, topic, group, max_per_entity_messages=3)
        seen: list[str] = []
        parked: list[str] = []
        gate = threading.Event()
        await consumer.start(held_handler(seen, 5, gate, parked))
        try:
            await wait_until_held(seen, parked)

            backlog = await _backlog_settles(consumer, kafka_available, group, topic, empty=False)

            # Any partition may hold an event, so the oldest of them all answers.
            assert backlog.oldest_waiting_for({"connectorId": "user-b"}) == min(
                backlog.oldest_waiting_ms.values()
            )
            # Looking did not move the group: the consumer carries on to the end.
            gate.set()
            await _drain(consumer, seen, len(records))
            await _backlog_settles(consumer, kafka_available, group, topic, empty=True)
        finally:
            gate.set()
            await consumer.stop()

    async def test_the_sweep_resends_only_the_record_the_queue_has_moved_past(
        self, kafka_available, topic, unique_suffix
    ) -> None:
        records = [("user-a", f"a-{i}") for i in range(8)]
        graph = FakeRecordGraph()
        queued_at = get_epoch_timestamp_in_ms()
        for connector_id, record_id in records:
            graph.add_queued(record_id, connector_id, queued_at)
        # Queued with the rest, but its event never reached the broker.
        graph.add_queued("lost", "user-a", queued_at)
        await _publish(kafka_available, topic, records)

        group = f"it-sweep-{unique_suffix}"
        consumer = _consumer(kafka_available, topic, group)
        seen: list[str] = []
        parked: list[str] = []
        gate = threading.Event()
        lane_producer = _lane_producer(kafka_available, topic)
        await lane_producer.initialize()
        producer = OneTopicProducer(lane_producer, topic)
        await consumer.start(held_handler(seen, 2, gate, parked))
        try:
            await wait_until_held(seen, parked)
            for record_id in seen:
                graph.mark_indexed(record_id)

            # Two hours on, the unindexed records and the lost one are all still
            # QUEUED, and the topic still holds events as old as they are.
            assert await run_stranded_sweep(graph, producer, consumer, topic, hours_later=2) == 0
            published = await _partition_timestamps(kafka_available, topic)
            assert sum(len(log) for log in published.values()) == len(records)

            gate.set()
            await _drain(consumer, seen, len(records))
            for record_id in seen:
                graph.mark_indexed(record_id)
            await _backlog_settles(consumer, kafka_available, group, topic, empty=True)

            assert graph.still_queued() == ["lost"]
            assert await run_stranded_sweep(graph, producer, consumer, topic, hours_later=2) == 1
            await _drain(consumer, seen, len(records) + 1)
            assert seen.count("lost") == 1
            assert graph.records["lost"]["republishCount"] == 1
        finally:
            gate.set()
            await consumer.stop()
            await lane_producer.cleanup()
