"""Kafka parallel dispatch: an event whose record is busy waits for that record, not for its connector.

The scheduler judges a leaf by its head. A second event for a record already
being processed (a duplicate reindex event during a long reindex) used to sit
at the head of its connector's leaf and stall every later event of that
connector and tier until the first finished.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from app.services.messaging.config import StreamMessage

TP = TopicPartition("record-events", 0)
LOGGER = "test_kafka_record_busy_hol"


def _message(
    offset: int, record_id: str, extension: str = "txt", tp: TopicPartition = TP, connector: str = "c1"
) -> MagicMock:
    payload = {
        "recordId": record_id,
        "orgId": "org-1",
        "connectorId": connector,
        "extension": extension,
        "mimeType": "application/pdf" if extension == "pdf" else "text/plain",
    }
    msg = MagicMock()
    msg.topic = tp.topic
    msg.partition = tp.partition
    msg.offset = offset
    msg.value = json.dumps({"eventType": "newRecord", "payload": payload}).encode()
    return msg


def _consumer(**fair: object) -> IndexingKafkaConsumer:
    config: dict[str, object] = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "max_buffered_messages": 500,
        "max_per_entity_messages": 500,
        "parallel_partitions": True,
    }
    config.update(fair)
    consumer = IndexingKafkaConsumer(
        logging.getLogger(LOGGER),
        KafkaConsumerConfig(
            topics=[TP.topic],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
        ),
        fair_scheduler_config=FairSchedulerConfig(**config),
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.consumer.stop = AsyncMock()
    consumer.consumer.getmany = AsyncMock(return_value={})
    consumer.running = True
    started: list[tuple[int, str]] = []

    async def start(
        message: MagicMock, parsed: StreamMessage, *_: object, **__: object
    ) -> None:
        started.append((message.offset, parsed.payload["recordId"]))

    consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock(side_effect=start)
    consumer.started = started
    return consumer


async def _enqueue(consumer: IndexingKafkaConsumer, message: MagicMock, tp: TopicPartition = TP) -> None:
    await consumer._IndexingKafkaConsumer__enqueue_message(tp, message)


async def _dispatch(consumer: IndexingKafkaConsumer) -> None:
    await consumer._IndexingKafkaConsumer__dispatch_phase()


def _finish(consumer: IndexingKafkaConsumer, record_id: str, offset: int = 0) -> None:
    consumer._IndexingKafkaConsumer__finish_partition(_message(offset, record_id), False, record_id)


def _held(consumer: IndexingKafkaConsumer) -> int:
    return consumer._scheduler.pending_count + consumer._IndexingKafkaConsumer__held_beside_scheduler()


@pytest.mark.asyncio
async def test_a_busy_record_at_the_head_does_not_stall_its_connector() -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(1, "R"))
    for offset, record in ((2, "A"), (3, "B"), (4, "C")):
        await _enqueue(consumer, _message(offset, record))

    await _dispatch(consumer)

    assert consumer.started == [(2, "A"), (3, "B"), (4, "C")]


@pytest.mark.asyncio
async def test_a_records_events_still_run_one_at_a_time_oldest_first() -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    for offset, record in ((0, "R"), (1, "A"), (2, "R"), (3, "B")):
        await _enqueue(consumer, _message(offset, record))

    await _dispatch(consumer)
    assert consumer.started == [(1, "A"), (3, "B")]

    _finish(consumer, "R")
    await _dispatch(consumer)
    # The next event for R waits for the one just dispatched.
    assert consumer.started[2:] == [(0, "R")]

    await _dispatch(consumer)
    assert consumer.started[3:] == []

    _finish(consumer, "R")
    await _dispatch(consumer)
    assert consumer.started[3:] == [(2, "R")]
    assert _held(consumer) == 0
    assert consumer._record_waiting == {}
    assert consumer._record_offered == {}


@pytest.mark.asyncio
async def test_events_of_one_record_in_two_tiers_still_run_in_offset_order() -> None:
    # Quantum 2 serves the light leaf twice, so R's later light event is
    # dequeued before its earlier heavy one.
    consumer = _consumer(default_quantum=2)
    consumer.governor = make_test_governor()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "X"))
    await _enqueue(consumer, _message(1, "R", extension="pdf"))
    await _enqueue(consumer, _message(2, "R"))

    await _dispatch(consumer)
    assert consumer.started == [(0, "X")]

    _finish(consumer, "R")
    await _dispatch(consumer)
    _finish(consumer, "R")
    await _dispatch(consumer)

    assert consumer.started[1:] == [(1, "R"), (2, "R")]


@pytest.mark.asyncio
async def test_parking_and_re_offering_are_logged_with_the_record_and_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(7, "R"))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _dispatch(consumer)
        parked = [r.getMessage() for r in caplog.records if "waits for it" in r.getMessage()]
        assert parked == [
            "Record R is busy: event record-events-0-7 waits for it, not for its connector "
            "(1 waiting for this record)"
        ]

        _finish(consumer, "R")
        offered = [r.getMessage() for r in caplog.records if "re-offered" in r.getMessage()]
        assert offered == ["Record R is free: re-offered its waiting event record-events-0-7 (0 more waiting)"]

    await _dispatch(consumer)
    assert consumer.started == [(7, "R")]


@pytest.mark.asyncio
async def test_a_released_record_with_no_room_is_offered_once_room_frees() -> None:
    consumer = _consumer(max_per_entity_messages=2)
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _dispatch(consumer)
    assert consumer._record_waiting_count == 1

    # The connector is at its cap when R is released.
    await _enqueue(consumer, _message(1, "X"))
    await _enqueue(consumer, _message(2, "Y"))
    _finish(consumer, "R")
    assert consumer._record_waiting_count == 1
    assert "R" in consumer._records_released

    await _dispatch(consumer)
    await _dispatch(consumer)

    assert consumer.started == [(1, "X"), (2, "Y"), (0, "R")]
    assert _held(consumer) == 0


@pytest.mark.asyncio
async def test_a_waiting_event_floors_the_commit_watermark() -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    for offset, record in ((1, "A"), (2, "B")):
        await _enqueue(consumer, _message(offset, record))
    await _dispatch(consumer)
    assert consumer.started == [(1, "A"), (2, "B")]

    tracker = consumer._offset_tracker
    watermarks = [tracker.mark_done(TP, offset) for offset in (1, 2)]

    # Offset 0 is still waiting, so nothing may be committed past it.
    assert all(w is None or w <= 0 for w in watermarks)
    assert tracker.stale_offsets(0.0) == []


@pytest.mark.asyncio
async def test_revocation_drops_waiting_events_of_the_revoked_partition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    other = TopicPartition(TP.topic, 1)
    consumer = _consumer()
    consumer._in_flight_records.update({"R", "S"})
    await _enqueue(consumer, _message(0, "R"))
    await _enqueue(consumer, _message(1, "A"))
    await _enqueue(consumer, _message(0, "S", tp=other), tp=other)
    await _dispatch(consumer)
    assert consumer.started == [(1, "A")]
    assert consumer._record_waiting_count == 2

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await consumer._on_partitions_revoked([TP])

    assert any("dropped 1 event(s) waiting for a busy record" in r.getMessage() for r in caplog.records)
    assert list(consumer._record_waiting) == ["S"]
    assert consumer._record_waiting_count == 1
    _finish(consumer, "R")
    _finish(consumer, "S")
    await _dispatch(consumer)
    # R's event belongs to the partition's new owner; S's is still ours.
    assert consumer.started[1:] == [(0, "S")]
    consumer.consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_revocation_forgets_an_offered_event_of_the_revoked_partition() -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _dispatch(consumer)
    _finish(consumer, "R")
    assert "R" in consumer._record_offered

    await consumer._on_partitions_revoked([TP])

    assert consumer._record_offered == {}
    assert _held(consumer) == 0
    await _dispatch(consumer)
    assert consumer.started == []


@pytest.mark.asyncio
async def test_shutdown_discards_waiting_events_without_committing_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _enqueue(consumer, _message(1, "A"))
    await _dispatch(consumer)
    assert consumer.started == [(1, "A")]

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await consumer.stop()

    assert any(
        "Discarded 1 event(s) waiting for a busy record at shutdown" in r.getMessage() for r in caplog.records
    )
    assert consumer._record_waiting == {}
    assert consumer._record_waiting_count == 0
    assert consumer._records_released == set()



@pytest.mark.asyncio
async def test_waiting_payloads_count_against_the_buffer_budget() -> None:
    consumer = _consumer(max_buffered_messages=11)
    consumer._in_flight_records.add("R")
    for offset, record in ((0, "R"), (1, "R"), (2, "A")):
        await _enqueue(consumer, _message(offset, record))
    await _dispatch(consumer)
    assert consumer.started == [(2, "A")]
    assert consumer._scheduler.pending_count == 0

    await consumer._IndexingKafkaConsumer__read_phase()

    # Two events wait for R with their payloads, so a read may take only the places left.
    assert consumer.consumer.getmany.await_args.kwargs["max_records"] == 9


class _Fetcher:
    """Stands in for the record fetch-back reader: serves each offset as an event for ``record``."""

    def __init__(self, consumer: IndexingKafkaConsumer, record: str, gone: frozenset[int] = frozenset()) -> None:
        self.consumer = consumer
        self.record = record
        self.gone = gone
        self.calls: list[tuple[list[int], bool]] = []

    async def fetch(self, wanted: dict) -> tuple[dict, set]:
        offsets = sorted(o for offsets in wanted.values() for o in offsets)
        self.calls.append((offsets, self.record in self.consumer._in_flight_records))
        found = {(tp, o): _message(o, self.record, tp=tp) for tp, os in wanted.items() for o in os if o not in self.gone}
        gone = {(tp, o) for tp, os in wanted.items() for o in os if o in self.gone}
        return found, gone

    async def close(self) -> None:
        return None


async def _burst(consumer: IndexingKafkaConsumer, record: str, offsets: range) -> None:
    """Read a burst for ``record`` in buffer-sized batches, dispatching between them as the consume loop does."""
    capacity = consumer.fair_scheduler_config.max_buffered_messages
    batch: list[int] = []
    for offset in offsets:
        batch.append(offset)
        if len(batch) == capacity:
            for o in batch:
                await _enqueue(consumer, _message(o, record))
            await _dispatch(consumer)
            batch = []
    for o in batch:
        await _enqueue(consumer, _message(o, record))
    await _dispatch(consumer)


async def _settle(consumer: IndexingKafkaConsumer) -> None:
    """One consume-loop pass, with the background read-back given time to finish."""
    await consumer._IndexingKafkaConsumer__read_phase()
    for _ in range(5):
        await asyncio.sleep(0)
    await consumer._IndexingKafkaConsumer__read_phase()
    await _dispatch(consumer)


@pytest.mark.asyncio
async def test_a_burst_for_one_busy_record_does_not_stop_reads_for_another_connector() -> None:
    consumer = _consumer(max_buffered_messages=8)
    consumer._in_flight_records.add("R")

    await _burst(consumer, "R", range(30))

    # Reads pause for every lane when the buffer has no room.
    assert consumer._IndexingKafkaConsumer__buffer_has_room()
    # A quarter of the buffer keeps payloads; the rest wait by offset only.
    assert consumer._record_waiting_count == 2
    assert consumer._record_waiting_positions == 28
    await _enqueue(consumer, _message(100, "X", connector="c2"))
    await _dispatch(consumer)
    assert consumer.started == [(100, "X")]


@pytest.mark.asyncio
async def test_events_kept_by_offset_run_in_order_after_release_and_are_read_back_only_then() -> None:
    consumer = _consumer(max_buffered_messages=8)
    fetcher = _Fetcher(consumer, "R")
    consumer._waiting_fetcher = fetcher
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(12))
    await _settle(consumer)
    assert fetcher.calls == []

    for _ in range(40):
        _finish(consumer, "R")
        await _settle(consumer)

    assert consumer.started == [(o, "R") for o in range(12)]
    assert fetcher.calls
    assert all(not busy for _, busy in fetcher.calls)
    read_back = [o for offsets, _ in fetcher.calls for o in offsets]
    assert read_back == sorted(read_back)
    assert consumer._record_waiting == {}
    assert consumer._record_waiting_count == consumer._record_waiting_positions == 0


@pytest.mark.asyncio
async def test_overflow_and_read_back_are_logged_with_the_record_and_offset(caplog: pytest.LogCaptureFixture) -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._waiting_fetcher = _Fetcher(consumer, "R")
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _enqueue(consumer, _message(1, "R"))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await _dispatch(consumer)
        _finish(consumer, "R")
        await _dispatch(consumer)  # R@0 runs
        _finish(consumer, "R")
        await _settle(consumer)

    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Record R is busy: event record-events-0-1 waits for it by offset only, its payload "
        "to be read back when the record is free (2 waiting for this record)"
    ) in messages
    assert "Record R is free: reading back 1 event(s) that waited for it by offset only, from record-events-0-1" in messages
    assert "Record R: read back its waiting event record-events-0-1" in messages
    assert consumer.started == [(0, "R"), (1, "R")]


@pytest.mark.asyncio
async def test_nothing_is_committed_past_an_event_kept_by_offset() -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._in_flight_records.update({"R", "S"})
    for offset, record in ((0, "S"), (1, "R"), (2, "A")):
        await _enqueue(consumer, _message(offset, record))
    await _dispatch(consumer)
    assert consumer.started == [(2, "A")]
    assert consumer._record_waiting_positions == 1  # R@1: the one held payload is S@0's

    tracker = consumer._offset_tracker
    watermarks = [tracker.mark_done(TP, offset) for offset in (0, 2)]

    assert all(w is None or w <= 1 for w in watermarks)
    assert tracker.stale_offsets(0.0) == []


@pytest.mark.asyncio
async def test_revocation_drops_events_kept_by_offset_and_ignores_a_late_read_back() -> None:
    consumer = _consumer(max_buffered_messages=4)
    fetcher = _Fetcher(consumer, "R")
    consumer._waiting_fetcher = fetcher
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(6))
    assert consumer._record_waiting_positions == 5
    _finish(consumer, "R")
    await _dispatch(consumer)  # R@0 runs
    _finish(consumer, "R")
    await consumer._IndexingKafkaConsumer__read_phase()  # starts reading back R@1..
    assert consumer._waiting_fetch_task is not None

    await consumer._on_partitions_revoked([TP])
    assert consumer._record_waiting == {}
    assert consumer._record_waiting_positions == 0
    await _settle(consumer)

    assert consumer.started == [(0, "R")]
    assert consumer._record_waiting_count == consumer._record_waiting_positions == 0
    consumer.consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_an_event_deleted_by_retention_before_read_back_is_resolved_not_stuck() -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._waiting_fetcher = _Fetcher(consumer, "R", gone=frozenset({1}))
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(3))
    _finish(consumer, "R")
    await _dispatch(consumer)  # R@0 runs
    consumer._offset_tracker.mark_done(TP, 0)
    _finish(consumer, "R")
    for _ in range(4):
        await _settle(consumer)
        _finish(consumer, "R")

    assert consumer.started == [(0, "R"), (2, "R")]
    consumer.consumer.commit.assert_awaited()
