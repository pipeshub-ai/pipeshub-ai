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
from collections import deque
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import (
    indexing_consumer as indexing_consumer_module,
)
from app.services.messaging.kafka.consumer import remembered as remembered_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
    _insertion_point,
    _ReadOutcome,
    _WaitingEvent,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
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
        payloads.append(dict(parsed.payload))

    payloads: list[dict] = []
    consumer._IndexingKafkaConsumer__start_processing_task = AsyncMock(side_effect=start)
    consumer.started = started
    consumer.payloads = payloads
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
    # stop() drops its client, so keep the one the commits would go to.
    commit = consumer.consumer.commit

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await consumer.stop()

    commit.assert_not_awaited()
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

    def __init__(
        self,
        consumer: IndexingKafkaConsumer,
        record: str,
        gone: frozenset[int] = frozenset(),
        unparseable: frozenset[int] = frozenset(),
        hold: asyncio.Event | None = None,
    ) -> None:
        self.consumer = consumer
        self.record = record
        self.gone = gone
        self.unparseable = unparseable
        self.hold = hold
        self.calls: list[tuple[list[int], bool]] = []

    async def fetch(self, wanted: dict, **_: object) -> tuple[dict, set]:
        offsets = sorted(o for offsets in wanted.values() for o in offsets)
        self.calls.append((offsets, self.record in self.consumer._in_flight_records))
        if self.hold is not None:
            await self.hold.wait()
        found = {(tp, o): _message(o, self.record, tp=tp) for tp, os in wanted.items() for o in os if o not in self.gone}
        gone = {(tp, o) for tp, os in wanted.items() for o in os if o in self.gone}
        for (_tp, o), message in found.items():
            if o in self.unparseable:
                message.value = b"\xff not utf-8"
        return found, gone

    async def close(self) -> None:
        return None


class _Log:
    """The broker side of the main consumer: each partition read in order, honouring max_records, seek and pause.

    Driving the real read phase against it means every read is sized by the
    consumer's own buffer arithmetic, as in production.
    """

    def __init__(self, consumer: IndexingKafkaConsumer) -> None:
        self.messages: dict[TopicPartition, list[MagicMock]] = {}
        self.position: dict[TopicPartition, int] = {}
        self.paused: set[TopicPartition] = set()
        kafka = consumer.consumer
        kafka.getmany = AsyncMock(side_effect=self.getmany)
        kafka.seek = MagicMock(side_effect=self.seek)
        kafka.pause = MagicMock(side_effect=lambda *tps: self.paused.update(tps))
        kafka.resume = MagicMock(side_effect=lambda *tps: self.paused.difference_update(tps))

    def append(self, message: MagicMock, tp: TopicPartition = TP) -> None:
        self.messages.setdefault(tp, []).append(message)
        self.position.setdefault(tp, 0)

    async def getmany(self, timeout_ms: int = 0, max_records: int = 1) -> dict:
        batch: dict[TopicPartition, list[MagicMock]] = {}
        left = max_records
        for tp, messages in self.messages.items():
            if tp in self.paused or left <= 0:
                continue
            start = self.position[tp]
            taken = messages[start : start + left]
            if taken:
                batch[tp] = taken
                self.position[tp] = start + len(taken)
                left -= len(taken)
        return batch

    def seek(self, tp: TopicPartition, offset: int) -> None:
        messages = self.messages.get(tp, [])
        self.position[tp] = next((i for i, m in enumerate(messages) if m.offset >= offset), len(messages))

    @property
    def unread(self) -> int:
        return sum(len(messages) - self.position[tp] for tp, messages in self.messages.items())


async def _settle(consumer: IndexingKafkaConsumer) -> None:
    """One consume-loop pass, with the background read-back given time to finish."""
    consumer._IndexingKafkaConsumer__resume_drained_lanes()
    await consumer._IndexingKafkaConsumer__read_phase()
    for _ in range(5):
        await asyncio.sleep(0)
    await consumer._IndexingKafkaConsumer__read_phase()
    await _dispatch(consumer)


async def _burst(consumer: IndexingKafkaConsumer, record: str, offsets: range) -> _Log:
    """Put a burst for ``record`` on the log and run consume passes until it is all read."""
    log = _Log(consumer)
    for offset in offsets:
        log.append(_message(offset, record))
    for _ in range(100):
        if not log.unread:
            break
        await _settle(consumer)
    assert log.unread == 0, f"{log.unread} message(s) never read"
    return log


@pytest.mark.asyncio
async def test_a_burst_for_one_busy_record_does_not_stop_reads_for_another_connector() -> None:
    consumer = _consumer(max_buffered_messages=8)
    consumer._in_flight_records.add("R")

    log = await _burst(consumer, "R", range(30))

    # Reads pause for every lane when the buffer has no room.
    assert consumer._IndexingKafkaConsumer__buffer_has_room()
    # A quarter of the buffer keeps payloads; the rest wait by offset only.
    assert consumer._record_waiting_count == 2
    assert consumer._record_waiting_positions == 28
    log.append(_message(100, "X", connector="c2"))
    await _settle(consumer)
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


@pytest.mark.asyncio
async def test_a_read_back_event_that_no_longer_parses_is_settled_not_read_back_for_ever(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer = _consumer(max_buffered_messages=4)
    fetcher = _Fetcher(consumer, "R", unparseable=frozenset({1}))
    consumer._waiting_fetcher = fetcher
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(3))
    _finish(consumer, "R")
    await _dispatch(consumer)  # R@0 runs
    consumer._offset_tracker.mark_done(TP, 0)
    _finish(consumer, "R")
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        for _ in range(6):
            await _settle(consumer)
            _finish(consumer, "R")

    # R@1 is settled once; R@2 behind it is not held up.
    assert consumer.started == [(0, "R"), (2, "R")]
    assert sum(1 in offsets for offsets, _ in fetcher.calls) == 1
    assert any(
        "Event record-events-0-1, which waited for record R, no longer parses; settled as poison" in r.getMessage()
        for r in caplog.records
    )
    assert consumer._record_waiting == {}


# --- Review follow-ups -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_older_event_held_back_in_another_tier_runs_before_a_younger_one_of_its_record() -> None:
    consumer = _consumer()
    consumer.governor = make_test_governor()
    ceiling = concurrency.dispatch_budget(consumer).tiers[ParseTier.HEAVY].ceiling
    heavy = [concurrency.GateWaiterToken(consumer, ParseTier.HEAVY) for _ in range(ceiling)]
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(10, "R", extension="pdf"))
    await _enqueue(consumer, _message(11, "R"))
    await _dispatch(consumer)
    assert consumer.started == []

    # R is free, but its heavy event cannot leave the scheduler yet.
    _finish(consumer, "R")
    await _dispatch(consumer)
    assert consumer.started == []

    for token in heavy:
        token.release()
    for _ in range(4):
        await _dispatch(consumer)
        _finish(consumer, "R")

    assert consumer.started == [(10, "R"), (11, "R")]


@pytest.mark.asyncio
async def test_a_records_events_on_two_partitions_keep_each_partitions_order() -> None:
    other = TopicPartition(TP.topic, 1)
    consumer = _consumer()
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(10, "R"))
    await _enqueue(consumer, _message(0, "R", tp=other), tp=other)
    await _enqueue(consumer, _message(8, "R"))
    await _dispatch(consumer)

    for _ in range(6):
        _finish(consumer, "R")
        await _dispatch(consumer)

    on_first = [offset for offset, _ in consumer.started if offset in (8, 10)]
    assert on_first == [8, 10]
    assert len(consumer.started) == 3


@pytest.mark.asyncio
async def test_a_released_records_oldest_event_is_read_back_when_its_own_younger_payloads_fill_the_buffer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Past the offset-only budget, waiting events keep their payload again,
    # so the record's younger events can take every place in the buffer.
    monkeypatch.setattr(indexing_consumer_module, "_WAITING_POSITIONS_BUDGET", 2)
    consumer = _consumer(max_buffered_messages=8)
    consumer._waiting_fetcher = _Fetcher(consumer, "R")
    consumer._in_flight_records.add("R")
    log = _Log(consumer)
    for offset in range(14):
        log.append(_message(offset, "R"))

    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(60):
            await _settle(consumer)  # reads while R runs
            await _settle(consumer)
            _finish(consumer, "R")

    assert consumer.started == [(o, "R") for o in range(14)]
    assert any("to make room to read back its oldest waiting event" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_read_back_in_progress_holds_its_places_against_reads() -> None:
    consumer = _consumer(max_buffered_messages=8)
    hold = asyncio.Event()
    consumer._waiting_fetcher = _Fetcher(consumer, "R", hold=hold)
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(6))  # R@0, R@1 keep payloads; R@2..R@5 by offset
    for _ in range(4):
        _finish(consumer, "R")
        await _settle(consumer)
    assert consumer.started == [(0, "R"), (1, "R")]
    _finish(consumer, "R")
    await consumer._IndexingKafkaConsumer__read_phase()  # starts reading back R@2..R@5
    assert consumer._waiting_fetch_task is not None

    await consumer._IndexingKafkaConsumer__read_phase()

    # The four payloads on their way hold four of the eight places.
    assert consumer.consumer.getmany.await_args.kwargs["max_records"] == 4
    hold.set()
    for _ in range(12):
        await _settle(consumer)
        _finish(consumer, "R")
    assert consumer.started == [(o, "R") for o in range(6)]


@pytest.mark.asyncio
async def test_a_read_never_buffers_past_the_combined_budget() -> None:
    consumer = _consumer(max_buffered_messages=8)
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _enqueue(consumer, _message(1, "R"))
    await _dispatch(consumer)
    assert consumer._record_waiting_count == 2
    for offset in range(2, 7):
        await _enqueue(consumer, _message(offset, f"B{offset}", connector="c2"))

    outcomes = [
        (await consumer._IndexingKafkaConsumer__enqueue_message(TP, _message(offset, f"C{offset}", connector="c3")))[0]
        for offset in range(7, 10)
    ]

    assert outcomes == [_ReadOutcome.BUFFERED, _ReadOutcome.STOP_PARTITION, _ReadOutcome.STOP_PARTITION]
    assert _held(consumer) == 8


@pytest.mark.asyncio
async def test_a_failed_commit_while_settling_unparseable_events_strands_none_of_them() -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._waiting_fetcher = _Fetcher(consumer, "R", unparseable=frozenset({1, 2}))
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(4))
    _finish(consumer, "R")
    await _dispatch(consumer)  # R@0 runs
    tracker = consumer._offset_tracker
    tracker.mark_done(TP, 0)
    consumer.consumer.commit = AsyncMock(side_effect=RuntimeError("broker unavailable"))
    _finish(consumer, "R")
    for _ in range(6):
        await _settle(consumer)
        _finish(consumer, "R")

    assert consumer.started == [(0, "R"), (3, "R")]
    # Both unparseable offsets were marked done despite the failed commit, so R@3 lets the watermark past all four.
    assert tracker.mark_done(TP, 3) == 4


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Reader:
    """A fake ``AIOKafkaConsumer`` for ``OffsetFetcher``.

    A ``slow`` partition hands back one record a poll and moves ``clock`` on a
    second each time; a ``slow_lookup`` partition never answers its first-offset lookup.
    """

    def __init__(
        self,
        *,
        start_error: BaseException | None = None,
        slow: frozenset[int] = frozenset(),
        slow_lookup: frozenset[int] = frozenset(),
        clock: _Clock | None = None,
    ) -> None:
        self.start_error = start_error
        self.slow = slow
        self.slow_lookup = slow_lookup
        self.clock = clock
        self.stopped = 0
        self.positions: dict[TopicPartition, int] = {}

    def __call__(self, **_: object) -> _Reader:
        return self

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error

    async def stop(self) -> None:
        self.stopped += 1

    def assign(self, partitions: list) -> None:
        return None

    async def beginning_offsets(self, partitions: list) -> dict:
        if any(tp.partition in self.slow_lookup for tp in partitions):
            await asyncio.Event().wait()
        return dict.fromkeys(partitions, 0)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        self.positions[tp] = offset

    async def getmany(self, tp: TopicPartition, timeout_ms: int = 0, max_records: int = 1) -> dict:
        offset = self.positions[tp]
        if tp.partition in self.slow:
            assert self.clock is not None
            self.clock.now += 1.0
            self.positions[tp] = offset + 1
            return {tp: [_message(offset, "R", tp=tp)]}
        records = [_message(o, "R", tp=tp) for o in range(offset, offset + max_records)]
        self.positions[tp] = offset + max_records
        return {tp: records}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.TimeoutError(), asyncio.CancelledError()])
async def test_a_reader_whose_start_is_interrupted_is_stopped(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    reader = _Reader(start_error=error)
    monkeypatch.setattr(remembered_module, "AIOKafkaConsumer", reader)
    fetcher = remembered_module.OffsetFetcher({}, logging.getLogger(LOGGER))

    with pytest.raises(type(error)):
        await fetcher.fetch({TP: [0]})

    assert reader.stopped == 1
    assert fetcher._consumer is None


@pytest.mark.asyncio
async def test_a_fetch_that_runs_out_of_time_keeps_what_it_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fast, slow = TP, TopicPartition(TP.topic, 1)
    clock = _Clock()
    monkeypatch.setattr(remembered_module, "AIOKafkaConsumer", _Reader(slow=frozenset({1}), clock=clock))
    fetcher = remembered_module.OffsetFetcher({}, logging.getLogger(LOGGER), clock=clock)

    found, gone = await fetcher.fetch({fast: [0, 1], slow: list(range(200))}, timeout_seconds=3.0)

    # Three slow polls fit before the deadline; the rest are neither found nor gone, so they are asked for again.
    assert set(found) == {(fast, 0), (fast, 1), (slow, 0), (slow, 1), (slow, 2)}
    assert gone == set()


@pytest.mark.asyncio
async def test_a_partition_whose_first_offset_lookup_hangs_does_not_hold_back_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fast, slow = TP, TopicPartition(TP.topic, 1)
    monkeypatch.setattr(remembered_module, "AIOKafkaConsumer", _Reader(slow_lookup=frozenset({1})))
    fetcher = remembered_module.OffsetFetcher({}, logging.getLogger(LOGGER))

    found, gone = await asyncio.wait_for(
        fetcher.fetch({fast: [0, 1], slow: [0, 1]}, timeout_seconds=0.5), timeout=5.0
    )

    assert set(found) == {(fast, 0), (fast, 1)}
    assert gone == set()


class _BacklogReader:
    """A fake reader whose every poll costs a second on ``clock`` and returns two records.

    Two a poll makes a long run of wanted offsets a backlog that outlasts the
    deadline. A ``late_lookup`` partition answers its first-offset lookup only
    after the first poll of another partition.
    """

    def __init__(self, clock: _Clock, late_lookup: frozenset[int] = frozenset()) -> None:
        self.clock = clock
        self.late_lookup = late_lookup
        self.polled = asyncio.Event()
        self.positions: dict[TopicPartition, int] = {}
        self.polls: list[int] = []

    def __call__(self, **_: object) -> _BacklogReader:
        return self

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def assign(self, partitions: list) -> None:
        return None

    async def beginning_offsets(self, partitions: list) -> dict:
        if any(tp.partition in self.late_lookup for tp in partitions):
            await self.polled.wait()
        return dict.fromkeys(partitions, 0)

    def seek(self, tp: TopicPartition, offset: int) -> None:
        self.positions[tp] = offset

    async def getmany(self, tp: TopicPartition, timeout_ms: int = 0, max_records: int = 1) -> dict:
        self.polls.append(tp.partition)
        self.clock.now += 1.0
        self.polled.set()
        # Let a lookup the poll released finish before this poll returns.
        for _ in range(3):
            await asyncio.sleep(0)
        offset = self.positions[tp]
        count = min(max_records, 2)
        self.positions[tp] = offset + count
        return {tp: [_message(o, "R", tp=tp) for o in range(offset, offset + count)]}


@pytest.mark.asyncio
async def test_a_backlogged_partition_numbered_first_does_not_take_the_whole_read(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backlog, small = TP, TopicPartition(TP.topic, 1)
    clock = _Clock()
    monkeypatch.setattr(remembered_module, "AIOKafkaConsumer", _BacklogReader(clock))
    fetcher = remembered_module.OffsetFetcher({}, logging.getLogger(LOGGER), clock=clock)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        found, gone = await fetcher.fetch({backlog: list(range(1000)), small: [0, 1]}, timeout_seconds=4.0)

    assert {(small, 0), (small, 1)} <= set(found)
    assert {(backlog, 0), (backlog, 1)} <= set(found)
    assert gone == set()
    unread = 1000 - len([p for p in found if p[0] == backlog])
    assert (
        f"Fetch-back reached its deadline with {unread} offset(s) unread on record-events-0; they are asked for again"
    ) in [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_a_backlogged_partition_whose_lookup_answers_first_does_not_take_the_whole_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late, backlog = TP, TopicPartition(TP.topic, 1)
    clock = _Clock()
    reader = _BacklogReader(clock, late_lookup=frozenset({0}))
    monkeypatch.setattr(remembered_module, "AIOKafkaConsumer", reader)
    fetcher = remembered_module.OffsetFetcher({}, logging.getLogger(LOGGER), clock=clock)

    found, gone = await fetcher.fetch({late: [0, 1], backlog: list(range(1000))}, timeout_seconds=4.0)

    assert reader.polls[0] == 1
    assert {(late, 0), (late, 1)} <= set(found)
    assert gone == set()


@pytest.mark.asyncio
async def test_a_read_back_in_progress_keeps_the_poll_short() -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._waiting_fetcher = _Fetcher(consumer, "R", hold=asyncio.Event())
    consumer._in_flight_records.add("R")
    await _burst(consumer, "R", range(3))  # R@0 keeps its payload; R@1, R@2 by offset
    _finish(consumer, "R")
    await _dispatch(consumer)  # R@0 runs
    _finish(consumer, "R")
    assert consumer._scheduler.pending_count == 0

    await consumer._IndexingKafkaConsumer__read_phase()  # starts reading back R@1, R@2

    assert consumer._waiting_fetch_task is not None
    timeout_ms = consumer.consumer.getmany.await_args.kwargs["timeout_ms"]
    assert timeout_ms == indexing_consumer_module._BUSY_POLL_TIMEOUT_MS


@pytest.mark.asyncio
async def test_a_remembered_fetch_back_never_buffers_past_the_combined_budget() -> None:
    consumer = _consumer(max_buffered_messages=4, max_remembered_positions=100)
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(0, "R"))
    await _dispatch(consumer)
    assert consumer._record_waiting_count == 1
    entity = ("org-1", "c2")
    positions = [(TP, offset) for offset in range(1, 5)]
    for tp, offset in positions:
        consumer._offset_tracker.track(tp, offset)
        consumer._remembered.remember(entity, tp, offset)

    await consumer._IndexingKafkaConsumer__buffer_fetched_back(
        {entity: positions},
        {(tp, offset): _message(offset, f"B{offset}", connector="c2") for tp, offset in positions},
        set(),
    )

    # The room was measured when the fetch was planned; one place is the waiting payload's.
    assert _held(consumer) == 4
    assert consumer._remembered.count(entity) == 1


class _CountingDeque(deque):
    """Counts indexed reads: one away from the ends is linear in a deque."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self.indexed = 0

    def __getitem__(self, index: int) -> object:
        self.indexed += 1
        return super().__getitem__(index)


def test_finding_where_an_event_waits_walks_the_queue_without_indexing_it() -> None:
    other = TopicPartition(TP.topic, 1)
    waiting = _CountingDeque(_WaitingEvent(TP, offset, None, None) for offset in range(2000))

    position = _insertion_point(waiting, other, 0)
    middle = _insertion_point(waiting, TP, 999)

    assert (position, middle) == (2000, 999)
    assert waiting.indexed == 0


@pytest.mark.asyncio
async def test_a_record_held_back_only_by_a_revoked_partition_is_released_for_its_other_partition() -> None:
    other = TopicPartition(TP.topic, 1)
    consumer = _consumer()
    consumer.governor = make_test_governor()
    ceiling = concurrency.dispatch_budget(consumer).tiers[ParseTier.HEAVY].ceiling
    heavy = [concurrency.GateWaiterToken(consumer, ParseTier.HEAVY) for _ in range(ceiling)]
    consumer._in_flight_records.add("R")
    await _enqueue(consumer, _message(10, "R", extension="pdf"))  # held: its tier is at its ceiling
    await _enqueue(consumer, _message(11, "R"))
    await _enqueue(consumer, _message(20, "R", tp=other), tp=other)
    await _dispatch(consumer)
    _finish(consumer, "R")
    await _dispatch(consumer)
    # R@11 waits for R@10, which is still in the scheduler, and R@20 waits behind R@11.
    assert consumer.started == []

    await consumer._on_partitions_revoked([TP])
    for _ in range(3):
        await _dispatch(consumer)

    assert consumer.started == [(20, "R")]
    for token in heavy:
        token.release()


@pytest.mark.asyncio
async def test_a_failed_hand_back_waits_by_offset_when_the_buffer_is_full(caplog: pytest.LogCaptureFixture) -> None:
    consumer = _consumer(max_buffered_messages=4)
    consumer._waiting_fetcher = _Fetcher(consumer, "R")
    consumer._in_flight_records.add("R")
    for offset in range(1, 5):
        await _enqueue(consumer, _message(offset, f"B{offset}", connector="c2"))
    message = _message(0, "R")
    consumer._offset_tracker.track(TP, 0)
    parsed = await consumer._IndexingKafkaConsumer__parse_message(message)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await consumer._IndexingKafkaConsumer__rebuffer_after_failed_handback(
            message, parsed, indexing_consumer_module._InFlightOffset(TP, 0), 0.0
        )

    assert _held(consumer) == 4
    assert consumer._record_waiting_positions == 1
    assert any(
        "Record R: its retry record-events-0-0, which could not be re-published, waits by offset only" in r.getMessage()
        for r in caplog.records
    )
    # The watermark floors at it while it waits.
    assert all(consumer._offset_tracker.mark_done(TP, offset) in (None, 0) for offset in range(1, 5))

    await _dispatch(consumer)  # the other connector's four run, freeing the buffer
    for _ in range(4):
        _finish(consumer, "R")
        await _settle(consumer)

    assert (0, "R") in consumer.started
    waited = next(p for p in consumer.payloads if p["recordId"] == "R")
    assert "_retry_not_before" in waited
