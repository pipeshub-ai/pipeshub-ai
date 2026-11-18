"""Kafka fair scheduling: a finished delivery wakes the consume loop.

The fair loop alternates a read phase and a dispatch phase. While the
scheduler holds work it cannot dispatch yet (the partition's previous record
is still in flight), the read phase used to wait in getmany() for the busy
poll timeout, and a finish does not wake getmany(). So a partition was paced
at one record per timeout (50 ms: ~20 records/s) however fast its handler.
Parallel dispatch keeps that pacing for now (see __read_phase).
These tests run the real consume loop and worker thread against the
in-memory broker in ``tests.support.fake_kafka``. Where they need "only a
finish can make progress", they raise the busy timeout to a minute.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as consumer_module
from app.services.messaging.kafka.consumer import remembered as remembered_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.support import fake_kafka
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

TOPIC = "record-events"
GROUP = "records_consumer_group"
A_MINUTE_MS = 60_000


def _envelope(record_id: str, connector_id: str, **extra: object) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
            **extra,
        },
        "timestamp": 1,
    }


class Handler:
    """Records what reached it, in order, and how many records of one partition
    were in it at once. A message whose ``tag`` is in ``block`` waits on
    ``release`` (a thread event: the handler runs on the worker thread)."""

    def __init__(self, block: frozenset[str] = frozenset()) -> None:
        self._lock = threading.Lock()
        self.seen: list[str] = []
        self.running: Counter[str] = Counter()
        self.most_at_once: Counter[str] = Counter()
        self.block = block
        self.release = threading.Event()

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        tag = str(message.payload["tag"])
        lane = tag.split("-")[0]
        with self._lock:
            self.seen.append(tag)
            self.running[lane] += 1
            self.most_at_once[lane] = max(self.most_at_once[lane], self.running[lane])
        try:
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
            if tag in self.block:
                await asyncio.to_thread(self.release.wait, 30)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)
        finally:
            with self._lock:
                self.running[lane] -= 1

    def of(self, lane: str) -> list[str]:
        with self._lock:
            return [t for t in self.seen if t.split("-")[0] == lane]


async def _until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.02)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "100")  # every record below is buffered by the first read
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "16")
    broker = FakeKafkaBroker()
    factory = broker.consumer_factory()
    with patch.object(consumer_module, "AIOKafkaConsumer", factory), \
            patch.object(remembered_module, "AIOKafkaConsumer", factory):
        yield broker


@pytest.fixture
def polls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """getmany() calls so far, in a one-element list."""
    count = [0]
    real = fake_kafka.FakeAIOKafkaConsumer.getmany

    async def counting(self, *partitions, **kwargs):  # noqa: ANN202
        count[0] += 1
        return await real(self, *partitions, **kwargs)

    monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "getmany", counting)
    return count


@pytest.fixture
def poll_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The timeout_ms of every getmany() call so far."""
    timeouts: list[int] = []
    real = fake_kafka.FakeAIOKafkaConsumer.getmany

    async def recording(self, *partitions, **kwargs):  # noqa: ANN202
        timeouts.append(kwargs.get("timeout_ms", 0))
        return await real(self, *partitions, **kwargs)

    monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "getmany", recording)
    return timeouts


@pytest.fixture
def only_a_finish_wakes_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(consumer_module, "_BUSY_POLL_TIMEOUT_MS", A_MINUTE_MS)


class Harness:
    def __init__(self, broker: FakeKafkaBroker) -> None:
        self.broker = broker
        self.consumers: list[IndexingKafkaConsumer] = []

    def produce(self, partition: int, count: int, record_id: str | None = None, **extra: object) -> None:
        for i in range(count):
            tag = f"p{partition}-{i:03d}"
            self.broker.produce(TOPIC, _envelope(record_id or tag, f"conn-{partition}", tag=tag, **extra),
                                partition=partition)

    def committed(self, partition: int = 0) -> int:
        return self.broker.committed_offset(GROUP, TOPIC, partition) or 0

    async def start(self, handler: Handler, *, parallel: bool = False) -> IndexingKafkaConsumer:
        consumer = IndexingKafkaConsumer(
            logging.getLogger("test"),
            KafkaConsumerConfig(
                topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["kafka:9092"],
            ),
            fair_scheduler_config=FairSchedulerConfig(
                enabled=True, key_fields=("orgId", "connectorId"), default_quantum=1,
                max_buffered_messages=200, max_per_entity_messages=100, max_dwell_seconds=900.0,
                parallel_partitions=parallel, max_remembered_positions=0,
            ),
        )
        self.consumers.append(consumer)
        await consumer.start(handler)
        return consumer

    async def stop(self) -> None:
        for consumer in self.consumers:
            await consumer.stop()


@pytest.fixture
async def harness(broker: FakeKafkaBroker) -> AsyncGenerator[Harness, None]:
    h = Harness(broker)
    yield h
    await h.stop()


@pytest.mark.usefixtures("only_a_finish_wakes_the_loop")
class TestAFinishWakesTheLoop:
    async def test_a_partition_drains_at_its_handlers_pace_not_one_record_per_busy_timeout(
        self, harness: Harness
    ) -> None:
        harness.produce(0, 20)
        handler = Handler()
        await harness.start(handler)

        await _until(lambda: harness.committed() == 20)

        assert handler.of("p0") == [f"p0-{i:03d}" for i in range(20)]
        assert handler.most_at_once["p0"] == 1

    async def test_a_finish_that_lands_during_the_poll_is_not_lost(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Each poll takes 50 ms, so an instant record's finish lands while the
        # loop is still polling, after the dispatch phase and before the wait:
        # the wait must still see it.
        real = fake_kafka.FakeAIOKafkaConsumer.getmany

        cancelled: list[int] = []

        async def slow_poll(self, *partitions, **kwargs):  # noqa: ANN202
            try:
                await asyncio.sleep(0.05)
                return await real(self, *partitions, **kwargs)
            except asyncio.CancelledError:
                # aiokafka never ends a cancelled poll's fetch_context, which turns off max_poll_interval_ms.
                cancelled.append(kwargs.get("timeout_ms", 0))
                raise

        monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "getmany", slow_poll)
        harness.produce(0, 10)
        handler = Handler()
        await harness.start(handler)

        await _until(lambda: harness.committed() == 10)

        assert handler.of("p0") == [f"p0-{i:03d}" for i in range(10)]
        assert cancelled == [], "a poll was cancelled"

    async def test_a_finish_lets_a_partition_paused_by_the_budget_be_read(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One record a read, one index permit, one task allowed to queue for it:
        # the first record holds the permit, the second queues, and the budget
        # pauses every partition before the third is read. Nothing is buffered,
        # and neither finish resumes the third partition (a finish resumes only
        # its own), so the loop must come round on a finish, not sit in a
        # full-length poll (a minute here).
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", str(A_MINUTE_MS))
        monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "1")
        monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "1")
        monkeypatch.setenv("MAX_PENDING_INDEXING_TASKS", "1")
        for partition in range(3):
            harness.produce(partition, 1)
        handler = Handler(block=frozenset({"p0-000", "p1-000", "p2-000"}))
        consumer = await harness.start(handler)
        try:
            await _until(lambda: len(consumer.consumer.paused()) == 3 and len(handler.seen) == 1)
        finally:
            handler.release.set()  # a failed wait must not leave the handler blocked for teardown
        await _until(lambda: all(harness.committed(p) == 1 for p in range(3)))

        assert sorted(handler.seen) == ["p0-000", "p1-000", "p2-000"]

    async def test_two_partitions_both_drain_in_order_one_record_each_at_a_time(self, harness: Harness) -> None:
        harness.produce(0, 20)
        harness.produce(1, 20)
        handler = Handler()
        await harness.start(handler)

        await _until(lambda: harness.committed(0) == 20 and harness.committed(1) == 20)

        for lane in ("p0", "p1"):
            assert handler.of(lane) == [f"{lane}-{i:03d}" for i in range(20)]
            assert handler.most_at_once[lane] == 1


class TestOnlyAFinishThatCanFreeSomethingIsWaitedFor:
    async def test_buffered_work_with_nothing_in_flight_keeps_the_short_poll(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A buffered record waits out its backoff and nothing is in flight, so no
        # finish can come: the loop keeps the busy poll, which a new record also
        # ends, rather than a non-blocking poll and a wait for a finish.
        handler = Handler()
        owner: list[IndexingKafkaConsumer] = []
        waiting: list[int] = []  # timeouts of the polls made before the record was dispatched
        real = fake_kafka.FakeAIOKafkaConsumer.getmany

        async def recording(self, *partitions, **kwargs):  # noqa: ANN202
            if owner and not owner[0]._in_flight_partitions and not handler.seen:
                waiting.append(kwargs.get("timeout_ms", 0))
            return await real(self, *partitions, **kwargs)

        monkeypatch.setattr(fake_kafka.FakeAIOKafkaConsumer, "getmany", recording)
        # Started first, so the 0.5 s backoff is all spent with the consumer running.
        owner.append(await harness.start(handler))
        harness.produce(0, 1, _retry_not_before=time.time() + 0.5)

        await _until(lambda: harness.committed() == 1, timeout=5.0)

        assert 0 not in waiting
        assert waiting.count(consumer_module._BUSY_POLL_TIMEOUT_MS) >= 5

    async def test_the_parallel_path_keeps_its_poll_until_its_commits_are_coalesced(
        self, harness: Harness, poll_timeouts: list[int]
    ) -> None:
        # Parallel dispatch commits once per record and aiokafka commits one at a
        # time: waking on every finish there outruns the commits. It keeps the
        # busy poll timeout, never a non-blocking poll and a wait.
        harness.produce(0, 20, record_id="same-record")
        handler = Handler()
        await harness.start(handler, parallel=True)

        await _until(lambda: harness.committed() == 20)

        assert handler.of("p0") == [f"p0-{i:03d}" for i in range(20)]
        assert 0 not in poll_timeouts
        assert consumer_module._BUSY_POLL_TIMEOUT_MS in poll_timeouts


class TestTheWaitStaysAWait:
    async def test_a_slow_delivery_does_not_turn_the_wait_into_a_spin(
        self, harness: Harness, polls: list[int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(consumer_module, "_BUSY_POLL_TIMEOUT_MS", 200)
        harness.produce(0, 5)
        # p0-000 finishes at once (its finish is the wake-up that must not stick);
        # p0-001 holds the partition while p0-002.. wait in the buffer.
        handler = Handler(block=frozenset({"p0-001"}))
        await harness.start(handler)
        try:
            await _until(lambda: "p0-001" in handler.seen)
            before = polls[0]
            await asyncio.sleep(1.0)
            during = polls[0] - before
        finally:
            handler.release.set()
        await _until(lambda: harness.committed() == 5)

        assert during <= 10, f"{during} polls in 1 s while waiting on one delivery (200 ms bound: ~5)"

    async def test_a_record_on_another_partition_is_read_while_one_is_in_flight(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The wait for a finish is bounded: a record arriving on another
        # partition is read within _BUSY_POLL_TIMEOUT_MS, not when the record in
        # flight ends (or a full-length poll later).
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", str(A_MINUTE_MS))
        harness.produce(0, 1)
        handler = Handler(block=frozenset({"p0-000"}))
        await harness.start(handler)
        try:
            await _until(lambda: "p0-000" in handler.seen)
            harness.produce(1, 1)
            await _until(lambda: harness.committed(1) == 1, timeout=2.0)
        finally:
            handler.release.set()
        await _until(lambda: harness.committed(0) == 1)

        assert handler.seen == ["p0-000", "p1-000"]

    async def test_an_idle_consumer_still_waits_in_one_long_poll_and_wakes_on_a_record(
        self, harness: Harness, polls: list[int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "2000")
        harness.produce(0, 1)
        handler = Handler()
        await harness.start(handler)
        await _until(lambda: harness.committed() == 1)

        before = polls[0]
        await asyncio.sleep(1.0)
        idle_polls = polls[0] - before
        harness.broker.produce(TOPIC, _envelope("late", "conn-0", tag="p0-001"), partition=0)
        await _until(lambda: harness.committed() == 2, timeout=1.0)

        assert idle_polls <= 2

    async def test_a_buffered_record_that_comes_due_is_dispatched_without_any_finish(
        self, harness: Harness
    ) -> None:
        # Nothing is in flight, so no finish will ever come: the wait's bound is
        # what dispatches it once due.
        handler = Handler()
        await harness.start(handler)
        harness.produce(0, 1, _retry_not_before=time.time() + 0.3)

        await _until(lambda: harness.committed() == 1, timeout=5.0)

        assert handler.of("p0") == ["p0-000"]
