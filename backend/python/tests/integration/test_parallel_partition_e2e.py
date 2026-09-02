"""E2E: parallel dispatch raises Kafka concurrency above one record at a time.

The consumer holds a partition for a record's whole lifetime, and every
compose file ships ``record-events`` with a single partition, so today the
indexing service processes exactly one record at a time on Kafka no matter
how high ``MAX_CONCURRENT_INDEXING`` is set (``helm/values.yaml`` documents
this). The commit watermark is what makes lifting that safe: out-of-order
completion within a partition is precisely what it exists to handle.

Measures peak observed concurrency through the real read/dispatch path with
the flag off and on, against one partition.
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
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier

_TOPIC = "record-events"
_RECORDS = 24


class _FakeSinglePartitionConsumer:
    def __init__(self, records: list) -> None:
        self._records = records
        self._position = 0
        self._paused: set[TopicPartition] = set()
        self.commits: list[dict] = []

    @property
    def tp(self) -> TopicPartition:
        return TopicPartition(_TOPIC, 0)

    @property
    def exhausted(self) -> bool:
        return self._position >= len(self._records)

    def assignment(self):
        return {self.tp}

    def paused(self):
        return set(self._paused)

    def pause(self, *tps):
        self._paused.update(tps)

    def resume(self, *tps):
        self._paused.difference_update(tps)

    def seek(self, _tp, offset):
        self._position = offset

    async def getmany(self, timeout_ms: int = 0, max_records: int = 1) -> dict:
        if self.tp in self._paused:
            return {}
        batch = self._records[self._position : self._position + max_records]
        if not batch:
            return {}
        self._position += len(batch)
        return {self.tp: batch}

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))


def _make_message(offset: int, record_id: str):
    envelope = {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": "conn-1",
            "extension": "txt",
            "mimeType": "text/plain",
        },
        "requestId": record_id,
    }
    msg = MagicMock()
    msg.topic = _TOPIC
    msg.partition = 0
    msg.offset = offset
    msg.value = json.dumps(envelope).encode()
    return msg


class _ConcurrencyProbe:
    """Handler that reports the high-water mark of records in flight."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.completed = 0

    def handler(self):
        async def handle(_parsed_message):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(tier=ParseTier.LIGHT),
            )
            # Yield control so siblings admitted in the same dispatch pass
            # actually overlap here rather than running back to back.
            await asyncio.sleep(0.01)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
            self.in_flight -= 1
            self.completed += 1
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

        return handle


async def _run(parallel: bool) -> tuple[_ConcurrencyProbe, IndexingKafkaConsumer]:
    logger = logging.getLogger("test_parallel_partition_e2e")
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
            max_per_entity_messages=200,
            max_dwell_seconds=900.0,
            parallel_partitions=parallel,
        ),
    )
    probe = _ConcurrencyProbe()
    broker = _FakeSinglePartitionConsumer(
        [_make_message(i, f"rec-{i}") for i in range(_RECORDS)]
    )
    consumer.consumer = broker
    consumer.message_handler = probe.handler()
    consumer.running = True

    consumer._IndexingKafkaConsumer__start_worker_thread()
    assert consumer.worker_loop_ready.wait(timeout=5.0)
    consumer.main_loop = asyncio.get_running_loop()

    def _idle() -> bool:
        with consumer._futures_lock:
            return not consumer._active_futures

    try:
        for _ in range(_RECORDS * 20):
            if broker.exhausted and consumer._scheduler.is_empty and _idle():
                break
            consumer._IndexingKafkaConsumer__resume_drained_lanes()
            await consumer._IndexingKafkaConsumer__read_phase()
            await consumer._IndexingKafkaConsumer__dispatch_phase()
            # Keep the main loop turning: worker tasks bridge their commits
            # back onto it, so a busy loop here would stall their resolution.
            await asyncio.sleep(0.005)
        else:
            raise AssertionError(
                f"only {probe.completed} of {_RECORDS} records completed"
            )
    finally:
        # Mirrors stop(): the worker's resolutions run on this loop, so
        # blocking it while waiting for them would deadlock.
        await asyncio.get_running_loop().run_in_executor(
            None, consumer._IndexingKafkaConsumer__stop_worker_thread
        )

    return probe, consumer


@pytest.mark.asyncio
class TestParallelPartitionThroughput:
    async def test_disabled_processes_one_record_at_a_time(self) -> None:
        probe, _consumer = await _run(parallel=False)

        assert probe.completed == _RECORDS
        assert probe.peak == 1, (
            "with one partition and per-partition serialisation the consumer "
            f"can only ever run one record at a time (saw {probe.peak})"
        )

    async def test_enabled_overlaps_records_on_one_partition(self) -> None:
        probe, _consumer = await _run(parallel=True)

        assert probe.completed == _RECORDS
        assert probe.peak > 1, (
            "parallel dispatch should overlap records on a single partition "
            f"(saw peak {probe.peak})"
        )

    async def test_every_record_completes_and_the_watermark_clears(self) -> None:
        """Out-of-order completion is the point; the watermark still has to
        commit the whole log exactly once."""
        probe, consumer = await _run(parallel=True)

        assert probe.completed == _RECORDS
        assert probe.in_flight == 0
        tp = TopicPartition(_TOPIC, 0)
        assert consumer._offset_tracker.watermark_lag(tp) == 0
        assert consumer._in_flight_records == set()
