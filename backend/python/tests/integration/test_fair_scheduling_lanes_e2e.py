"""E2E: lanes remove the head-of-line blocking that consumer-side fairness
alone could not.

``test_fair_scheduling_e2e.py`` pins the limit of a single lane: when one
user's entire backlog is published *before* another's first record, the
consumer cannot schedule what it has not read, so no amount of DRR reordering
reaches the small user early. That test asserts the small user still waits.

This one publishes the *same* segregated backlog through the lane-aware
producer, so the two users' records land on different partitions, and asserts
the small user is served immediately. Same workload, same consumer, same
scheduler -- the only difference is that the records are no longer in one
queue.

Only the Kafka transport is faked; the lane router, the producer decorator,
the consumer's read/dispatch split, per-lane pause, the scheduler and offset
tracking are all real.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.lanes.hash_router import stable_lane
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier

_TOPIC = "record-events"
_LANES = 8
_BIG_COUNT = 400
_SMALL_COUNT = 10


class _FakeLanedKafkaConsumer:
    """Multi-partition in-memory stand-in for ``AIOKafkaConsumer``.

    Faithful on the points this test turns on: ``getmany`` honours the
    per-consumer ``max_records`` cap *across* partitions (as aiokafka does),
    skips paused partitions entirely, and ``seek`` really rewinds a
    partition's position.
    """

    def __init__(self, partitions: dict[TopicPartition, list]) -> None:
        self._records = partitions
        self._positions = dict.fromkeys(partitions, 0)
        self._paused: set[TopicPartition] = set()
        self.commits: list[dict] = []

    @property
    def exhausted(self) -> bool:
        return all(
            self._positions[tp] >= len(records)
            for tp, records in self._records.items()
        )

    def assignment(self) -> set[TopicPartition]:
        return set(self._records)

    def paused(self) -> set[TopicPartition]:
        return set(self._paused)

    def pause(self, *tps: TopicPartition) -> None:
        self._paused.update(tps)

    def resume(self, *tps: TopicPartition) -> None:
        self._paused.difference_update(tps)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        self._positions[tp] = offset

    async def getmany(self, timeout_ms: int = 0, max_records: int = 1) -> dict:
        """Spread the record budget across partitions, one at a time.

        aiokafka fetches per partition and returns from every partition that
        has buffered records; draining the budget into whichever partition
        happens to be first would starve the others of the consumer's
        attention entirely, which is not how the real client behaves and
        would make this test measure the fake rather than the scheduler.
        """
        batch: dict[TopicPartition, list] = {}
        budget = max_records
        while budget > 0:
            progressed = False
            for tp, records in self._records.items():
                if budget <= 0:
                    break
                if tp in self._paused:
                    continue
                start = self._positions[tp]
                if start >= len(records):
                    continue
                batch.setdefault(tp, []).append(records[start])
                self._positions[tp] = start + 1
                budget -= 1
                progressed = True
            if not progressed:
                break
        return batch

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))


def _make_message(partition: int, offset: int, connector_id: str, record_id: str):
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{record_id}",
        "extension": "txt",
        "mimeType": "text/plain",
    }
    envelope = {"eventType": "newRecord", "payload": payload, "requestId": record_id}
    msg = MagicMock()
    msg.topic = _TOPIC
    msg.partition = partition
    msg.offset = offset
    msg.value = json.dumps(envelope).encode()
    return msg


def _build_laned_partitions() -> dict[TopicPartition, list]:
    """The segregated publish order, routed through the real lane hash.

    User A's whole backlog is published before User B's first record, exactly
    as in the single-lane test -- but ``stable_lane`` sends the two connectors
    to different partitions, so within a partition there is nothing for User B
    to queue behind.
    """
    partitions: dict[int, list] = {}

    def publish(connector_id: str, record_id: str) -> None:
        lane = stable_lane(connector_id, _LANES)
        records = partitions.setdefault(lane, [])
        records.append(
            _make_message(lane, len(records), connector_id, record_id)
        )

    for i in range(_BIG_COUNT):
        publish("user-a", f"big-{i}")
    for i in range(_SMALL_COUNT):
        publish("user-b", f"small-{i}")

    return {
        TopicPartition(_TOPIC, lane): records
        for lane, records in sorted(partitions.items())
    }


def _make_handler(completion_order: list):
    async def handler(parsed_message):
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.LIGHT),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        completion_order.append(parsed_message.payload["connectorId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handler


async def _settle(consumer: IndexingKafkaConsumer) -> None:
    with consumer._futures_lock:
        futures = list(consumer._active_futures)
    if futures:
        await asyncio.wait(
            [asyncio.wrap_future(f) for f in futures], timeout=5.0
        )
    await asyncio.sleep(0)


@pytest.mark.asyncio
class TestLanesRemoveHeadOfLineBlocking:
    async def test_small_user_is_served_immediately_despite_publishing_last(
        self,
    ) -> None:
        partitions = _build_laned_partitions()
        assert len(partitions) == 2, (
            "fixture requires the two connectors to hash to different lanes"
        )

        logger = logging.getLogger("test_fair_scheduling_lanes_e2e")
        consumer = IndexingKafkaConsumer(
            logger,
            KafkaConsumerConfig(
                topics=[_TOPIC],
                client_id="e2e",
                group_id="e2e-group",
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                bootstrap_servers=["broker:9092"],
            ),
            retry_manager=None,
            producer=AsyncMock(),
            fair_scheduler_config=FairSchedulerConfig(
                enabled=True,
                key_fields=("orgId", "connectorId"),
                default_quantum=1,
                max_buffered_messages=200,
                max_per_entity_messages=50,
                max_dwell_seconds=900.0,
            ),
        )
        completion_order: list[str] = []
        broker = _FakeLanedKafkaConsumer(partitions)
        consumer.consumer = broker
        consumer.message_handler = _make_handler(completion_order)
        consumer.running = True

        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)
        consumer.main_loop = asyncio.get_running_loop()

        try:
            for _ in range((_BIG_COUNT + _SMALL_COUNT) * 6):
                if broker.exhausted and consumer._scheduler.is_empty:
                    break
                consumer._IndexingKafkaConsumer__resume_drained_lanes()
                await consumer._IndexingKafkaConsumer__read_phase()
                await consumer._IndexingKafkaConsumer__dispatch_phase()
                await _settle(consumer)
            else:
                raise AssertionError(
                    f"not drained: {len(completion_order)} of "
                    f"{_BIG_COUNT + _SMALL_COUNT} indexed"
                )
        finally:
            consumer._IndexingKafkaConsumer__stop_worker_thread()

        assert completion_order.count("user-a") == _BIG_COUNT
        assert completion_order.count("user-b") == _SMALL_COUNT

        small = [
            i for i, conn in enumerate(completion_order) if conn == "user-b"
        ]
        # Single lane put User B's last record at ~409 (its FIFO position).
        # On its own lane it is visible from the first poll, so DRR alternates
        # the two connectors and User B finishes inside the first rounds.
        assert small[-1] < 25, (
            "with its own lane User B should finish in the first DRR rounds, "
            f"not behind User A's backlog (got {small})"
        )
        assert small[0] < 5

    async def test_nothing_is_lost_and_every_lane_commits_through(self) -> None:
        partitions = _build_laned_partitions()
        logger = logging.getLogger("test_fair_scheduling_lanes_e2e")
        consumer = IndexingKafkaConsumer(
            logger,
            KafkaConsumerConfig(
                topics=[_TOPIC],
                client_id="e2e",
                group_id="e2e-group",
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                bootstrap_servers=["broker:9092"],
            ),
            retry_manager=None,
            producer=AsyncMock(),
            fair_scheduler_config=FairSchedulerConfig(
                enabled=True,
                key_fields=("orgId", "connectorId"),
                default_quantum=1,
                max_buffered_messages=200,
                max_per_entity_messages=50,
                max_dwell_seconds=900.0,
            ),
        )
        completion_order: list[str] = []
        broker = _FakeLanedKafkaConsumer(partitions)
        consumer.consumer = broker
        consumer.message_handler = _make_handler(completion_order)
        consumer.running = True

        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)
        consumer.main_loop = asyncio.get_running_loop()

        try:
            for _ in range((_BIG_COUNT + _SMALL_COUNT) * 6):
                if broker.exhausted and consumer._scheduler.is_empty:
                    break
                consumer._IndexingKafkaConsumer__resume_drained_lanes()
                await consumer._IndexingKafkaConsumer__read_phase()
                await consumer._IndexingKafkaConsumer__dispatch_phase()
                await _settle(consumer)
        finally:
            consumer._IndexingKafkaConsumer__stop_worker_thread()

        assert len(completion_order) == _BIG_COUNT + _SMALL_COUNT
        # Per-lane pause must not strand a lane: every partition's watermark
        # has to clear its whole log.
        for tp, records in partitions.items():
            assert consumer._offset_tracker.watermark_lag(tp) == 0, (
                f"{tp} did not commit through its {len(records)} records"
            )
        assert not consumer._lane_paused
