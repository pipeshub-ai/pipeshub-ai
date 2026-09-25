"""Delivery guarantees of the indexing Kafka consumer (``IndexingKafkaConsumer``).

This consumer feeds every record event (new, updated, re-indexed, deleted
documents) into the indexing pipeline. These tests run its real consume
loop and worker thread, with fair scheduling both off and on, against the
in-memory broker in ``tests.support.fake_kafka``. Only Kafka and the Redis
retry counters are stubbed; the handler is an ordinary async generator
shaped like the real record handler.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

TOPIC = "record-events"
GROUP = "records_consumer_group"


def test_the_kafka_client_library_is_real_not_a_conftest_stand_in() -> None:
    assert not isinstance(sys.modules["aiokafka"], MagicMock)
    assert isinstance(module.AIOKafkaConsumer, type)
    assert isinstance(module.TopicPartition, type)


class InMemoryRetryTracker(IRetryTracker):
    """Retry counters as Redis would keep them, shared by both event loops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.failures: dict[str, int] = {}
        self.deliveries: dict[str, int] = {}

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        with self._lock:
            self.failures[message_id] = self.failures.get(message_id, 0) + 1
            count = self.failures[message_id]
        return count, count >= max_attempts

    async def record_delivery(self, message_id: str) -> int:
        with self._lock:
            self.deliveries[message_id] = self.deliveries.get(message_id, 0) + 1
            return self.deliveries[message_id]

    async def get_count(self, message_id: str) -> int:
        with self._lock:
            return self.failures.get(message_id, 0)

    async def clear(self, message_id: str) -> None:
        with self._lock:
            self.failures.pop(message_id, None)
            self.deliveries.pop(message_id, None)

    async def clear_batch(self, message_ids: list[str]) -> int:
        for message_id in message_ids:
            await self.clear(message_id)
        return len(message_ids)

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        with self._lock:
            return any(self.failures.get(m) for m in message_ids)


class BrokerProducer:
    """The retry producer, publishing straight back onto the fake broker."""

    def __init__(self, broker: FakeKafkaBroker) -> None:
        self.broker = broker
        self.sent: list[dict] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        envelope = {"eventType": event_type, "payload": payload, "timestamp": int(time.time() * 1000)}
        self.sent.append(envelope)
        self.broker.produce(topic, envelope)
        return True


class AbandonSink:
    def __init__(self) -> None:
        self.abandoned: list[tuple[str | None, str, int]] = []

    async def on_message_abandoned(self, message: StreamMessage | None, *, reason: str, attempts: int) -> None:
        record_id = message.payload.get("recordId") if message else None
        self.abandoned.append((record_id, reason, attempts))


class Pipeline:
    """Stands in for the record handler: yields the two completion events,
    or raises on chosen records."""

    def __init__(self, fail: dict[str, BaseException] | None = None, fail_times: int = 1,
                 delay: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.seen: list[str] = []
        self.completed: list[str] = []
        self.fail = fail or {}
        self.fail_times = fail_times
        self.delay = delay
        self._failures: dict[str, int] = {}
        self.started = threading.Event()

    async def __call__(self, message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
        record_id = str(message.payload["recordId"])
        with self._lock:
            self.seen.append(record_id)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        with self._lock:
            should_fail = record_id in self.fail and self._failures.get(record_id, 0) < self.fail_times
            if should_fail:
                self._failures[record_id] = self._failures.get(record_id, 0) + 1
        if should_fail:
            raise self.fail[record_id]
        data = PipelineEventData(record_id=record_id)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=data)
        with self._lock:
            self.completed.append(record_id)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=data)


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=[TOPIC],
        client_id="indexing-test",
        group_id=GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["kafka:9092"],
    )


def _record_event(record_id: str, org_id: str = "org-a", connector_id: str = "conn-1") -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": org_id,
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
        },
        "timestamp": 1,
    }


def _fair() -> FairSchedulerConfig:
    return FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=100,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    )


@pytest.fixture(params=["fifo", "fair"])
def fair_scheduler_config(request: pytest.FixtureRequest) -> FairSchedulerConfig | None:
    return _fair() if request.param == "fair" else None


@pytest.fixture
def broker() -> Iterator[FakeKafkaBroker]:
    broker = FakeKafkaBroker()
    with patch.object(module, "AIOKafkaConsumer", broker.consumer_factory()), \
            patch.object(module, "_compute_retry_backoff_seconds", lambda _count: 0.0):
        yield broker


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")


async def _until(predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.02)


class Harness:
    def __init__(self, broker: FakeKafkaBroker, fair: FairSchedulerConfig | None) -> None:
        self.broker = broker
        self.fair = fair
        self.retries = InMemoryRetryTracker()
        self.producer = BrokerProducer(broker)
        self.sink = AbandonSink()
        self.consumer: IndexingKafkaConsumer | None = None

    async def start(self, handler: Pipeline) -> IndexingKafkaConsumer:
        self.consumer = IndexingKafkaConsumer(
            logging.getLogger("test"),
            _config(),
            retry_manager=self.retries,
            producer=self.producer,
            fair_scheduler_config=self.fair,
            disposition_sink=self.sink,
        )
        await self.consumer.start(handler)
        return self.consumer

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()

    def committed(self) -> int:
        return self.broker.committed_offset(GROUP, TOPIC) or 0

    def log_length(self) -> int:
        return len(self.broker.logs[next(iter(self.broker.logs))])


@pytest.fixture
async def harness(
    broker: FakeKafkaBroker, fair_scheduler_config: FairSchedulerConfig | None
) -> AsyncIterator[Harness]:
    h = Harness(broker, fair_scheduler_config)
    yield h
    await h.stop()


class TestOffsets:
    async def test_every_record_is_indexed_once_and_its_offset_committed(self, broker, harness) -> None:
        for i in range(4):
            broker.produce(TOPIC, _record_event(f"r{i}", org_id="org-a" if i % 2 else "org-b"))
        pipeline = Pipeline()
        await harness.start(pipeline)
        await _until(lambda: harness.committed() == 4)
        await harness.stop()
        assert sorted(pipeline.completed) == ["r0", "r1", "r2", "r3"]
        assert harness.producer.sent == []

    async def test_an_offset_is_not_committed_while_its_record_is_still_indexing(self, broker, harness) -> None:
        broker.produce(TOPIC, _record_event("slow"))
        pipeline = Pipeline(delay=0.5)
        await harness.start(pipeline)
        await _until(pipeline.started.is_set)
        assert harness.committed() == 0
        await _until(lambda: harness.committed() == 1)
        assert pipeline.completed == ["slow"]


class TestPoisonMessages:
    async def test_malformed_json_is_committed_and_reported_without_reaching_the_pipeline(
        self, broker, harness
    ) -> None:
        broker.produce(TOPIC, b"{not json")
        broker.produce(TOPIC, _record_event("r1"))
        pipeline = Pipeline()
        await harness.start(pipeline)
        await _until(lambda: harness.committed() == 2)
        await harness.stop()
        assert pipeline.seen == ["r1"]
        assert harness.sink.abandoned == [(None, "terminal error", 1)]

    async def test_a_terminal_pipeline_error_is_committed_once_and_the_record_is_reported(
        self, broker, harness
    ) -> None:
        broker.produce(TOPIC, _record_event("broken"))
        broker.produce(TOPIC, _record_event("fine"))
        pipeline = Pipeline(fail={"broken": FileNotFoundError("parser binary missing")}, fail_times=99)
        await harness.start(pipeline)
        await _until(lambda: harness.committed() == 2)
        await harness.stop()
        assert pipeline.seen.count("broken") == 1
        assert pipeline.completed == ["fine"]
        assert harness.producer.sent == []
        assert harness.sink.abandoned == [("broken", "terminal error", 1)]


class TestRetries:
    async def test_a_transient_failure_is_republished_then_indexed(self, broker, harness) -> None:
        broker.produce(TOPIC, _record_event("flaky"))
        broker.produce(TOPIC, _record_event("next"))
        pipeline = Pipeline(fail={"flaky": ConnectionError("vector store unreachable")})
        await harness.start(pipeline)
        await _until(lambda: "flaky" in pipeline.completed and "next" in pipeline.completed)
        await _until(lambda: harness.committed() == harness.log_length())
        await harness.stop()

        assert pipeline.seen.count("flaky") == 2
        assert len(harness.producer.sent) == 1
        assert harness.producer.sent[0]["payload"]["_retry_tracking_id"] == f"{TOPIC}-0-0"
        assert harness.retries.failures == {}
        assert harness.sink.abandoned == []

    async def test_a_record_that_keeps_failing_is_given_up_after_max_attempts_and_reported(
        self, broker, harness
    ) -> None:
        broker.produce(TOPIC, _record_event("doomed"))
        pipeline = Pipeline(fail={"doomed": ConnectionError("down")}, fail_times=99)
        await harness.start(pipeline)
        await _until(lambda: len(harness.sink.abandoned) == 1)
        await _until(lambda: harness.committed() == harness.log_length())
        await harness.stop()

        assert pipeline.seen.count("doomed") == 3
        assert len(harness.producer.sent) == 2
        assert harness.sink.abandoned == [("doomed", "3 transient failures (max 3)", 3)]
        assert harness.retries.failures == {}


class TestGracefulShutdown:
    async def test_stop_lets_an_in_flight_record_finish_and_commit(self, broker, harness) -> None:
        broker.produce(TOPIC, _record_event("in-flight"))
        pipeline = Pipeline(delay=0.3)
        await harness.start(pipeline)
        await _until(pipeline.started.is_set)
        await harness.stop()
        assert pipeline.completed == ["in-flight"]
        assert harness.committed() == 1
        assert broker.consumers[-1].stopped is True

    async def test_a_record_cut_off_by_the_shutdown_deadline_is_redelivered_on_restart(
        self, broker, harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "0.2")
        broker.produce(TOPIC, _record_event("stuck"))
        stuck = Pipeline(delay=30)
        await harness.start(stuck)
        await _until(stuck.started.is_set)
        await harness.stop()
        assert stuck.completed == []
        assert harness.committed() == 0

        pipeline = Pipeline()
        await harness.start(pipeline)
        await _until(lambda: harness.committed() == 1)
        await harness.stop()
        assert pipeline.completed == ["stuck"]


class TestDuplicates:
    async def test_a_replayed_retry_copy_shares_the_original_retry_budget(self, broker, harness) -> None:
        # A re-published copy carries the original's tracking id, so its
        # failures count against the same budget rather than starting over.
        event = _record_event("copy")
        event["payload"]["_retry_tracking_id"] = "record-events-0-99"
        harness.retries.failures["record-events-0-99"] = 2
        broker.produce(TOPIC, json.loads(json.dumps(event)))
        pipeline = Pipeline(fail={"copy": ConnectionError("down")})
        await harness.start(pipeline)
        await _until(lambda: len(harness.sink.abandoned) == 1)
        await harness.stop()
        assert pipeline.seen == ["copy"]
        assert harness.producer.sent == []
