"""Reading past a connector at its cap, on a real Kafka broker.

One partition, as an install that has not raised KAFKA_TOPIC_PARTITIONS has:
connector A's backlog sits ahead of connector B's. B must reach the handler
within a few reads while A is worked through in order, the commit must never
pass a remembered offset, an offset retention has deleted must be resolved
rather than stall the partition, and a restart must lose nothing.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from unittest.mock import patch

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import remembered as remembered_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    committed_offsets,
    create_kafka_topic,
    delete_kafka_topic,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(240)]

_A = 1_500
_B = 5


@pytest.fixture(autouse=True)
def _reads_and_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_BATCH_SIZE_INDEXING", "100")
    monkeypatch.setenv("MAX_CONCURRENT_INDEXING", "16")


def _fair() -> FairSchedulerConfig:
    return FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=200,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
        parallel_partitions=True,
        max_remembered_positions=200_000,
    )


def _envelope(record_id: str, connector_id: str) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id, "orgId": "org-1", "connectorId": connector_id,
            "extension": "txt", "mimeType": "text/plain",
        },
        "timestamp": 1,
    }


class Handler:
    def __init__(self, delay: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.seen: list[str] = []
        self.delay = delay

    async def __call__(self, message):  # noqa: ANN204
        with self._lock:
            self.seen.append(message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        if self.delay and message.payload["connectorId"] == "conn-a":
            await asyncio.sleep(self.delay)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    def first(self, prefix: str) -> int:
        with self._lock:
            return next((i for i, r in enumerate(self.seen) if r.startswith(prefix)), -1)


async def _until(predicate) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.1)


def _consumer(bootstrap: str, topic: str, group: str) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("it-consumer"),
        KafkaConsumerConfig(
            topics=[topic], client_id=f"{group}-client", group_id=group,
            auto_offset_reset="earliest", enable_auto_commit=False,
            bootstrap_servers=[bootstrap],
        ),
        fair_scheduler_config=_fair(),
    )


@pytest.fixture
async def backlog(kafka_available, unique_suffix):  # noqa: ANN201
    from aiokafka import AIOKafkaProducer

    topic = f"record-events-past-{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 1)
    producer = AIOKafkaProducer(bootstrap_servers=kafka_available)
    await producer.start()
    try:
        for i in range(_A):
            await producer.send(topic, json.dumps(_envelope(f"a-{i:05d}", "conn-a")).encode())
        for i in range(_B):
            await producer.send(topic, json.dumps(_envelope(f"b-{i}", "conn-b")).encode())
        await producer.flush()
    finally:
        await producer.stop()
    yield topic
    await delete_kafka_topic(kafka_available, topic)


def _a_in_order(seen: list[str]) -> bool:
    a = [r for r in seen if r.startswith("a-")]
    return a == sorted(a)


async def _committed(bootstrap: str, group: str, topic: str) -> int:
    return (await committed_offsets(bootstrap, group, topic)).get(0, 0)


async def test_the_connector_behind_a_backlog_is_reached_within_a_few_reads(
    kafka_available, backlog, unique_suffix
) -> None:
    group = f"it-past-{unique_suffix}"
    # Each conn-a record takes a while, as indexing does, so its backlog
    # outlasts the reads and the connector sits at its cap.
    handler = Handler(delay=0.05)
    consumer = _consumer(kafka_available, backlog, group)
    await consumer.start(handler)
    try:
        await _until(lambda: handler.first("b-") >= 0)
        first_b = handler.first("b-")

        async def all_committed() -> bool:
            return await _committed(kafka_available, group, backlog) == _A + _B

        await _until(all_committed)
    finally:
        await consumer.stop()

    print(f"real kafka: first conn-b record at position {first_b} of {_A + _B}")
    assert first_b <= 60
    assert _a_in_order(handler.seen)
    assert len(handler.seen) == _A + _B


async def test_commit_holds_at_a_remembered_offset_and_retention_deleted_offsets_are_resolved(
    kafka_available, backlog, unique_suffix
) -> None:
    from aiokafka import TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient, RecordsToDelete

    group = f"it-past-ret-{unique_suffix}"
    release = asyncio.Event()
    real_fetch = remembered_module.OffsetFetcher.fetch

    async def held_fetch(self, wanted):  # noqa: ANN202
        await release.wait()
        return await real_fetch(self, wanted)

    handler = Handler(delay=0.05)
    consumer = _consumer(kafka_available, backlog, group)
    with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
        await consumer.start(handler)
        try:
            await _until(lambda: len(handler.seen) == 50 + _B)
            await asyncio.sleep(1.0)
            held_at = await _committed(kafka_available, group, backlog)

            admin = AIOKafkaAdminClient(bootstrap_servers=kafka_available)
            await admin.start()
            try:
                await admin.delete_records(
                    {TopicPartition(backlog, 0): RecordsToDelete(before_offset=80)}
                )
            finally:
                await admin.close()
            release.set()

            async def all_committed() -> bool:
                return await _committed(kafka_available, group, backlog) == _A + _B

            await _until(all_committed)
        finally:
            await consumer.stop()

    assert held_at <= 50
    assert not any(f"a-{i:05d}" in handler.seen for i in range(50, 80))
    assert len(handler.seen) == _A + _B - 30
    assert _a_in_order(handler.seen)


async def test_a_restart_with_positions_remembered_loses_nothing(
    kafka_available, backlog, unique_suffix
) -> None:
    group = f"it-past-restart-{unique_suffix}"
    release = asyncio.Event()
    real_fetch = remembered_module.OffsetFetcher.fetch

    async def held_fetch(self, wanted):  # noqa: ANN202
        await release.wait()
        return await real_fetch(self, wanted)

    first = Handler(delay=0.05)
    consumer = _consumer(kafka_available, backlog, group)
    with patch.object(remembered_module.OffsetFetcher, "fetch", held_fetch):
        await consumer.start(first)
        try:
            await _until(lambda: len(first.seen) == 50 + _B)
            await asyncio.sleep(1.0)
        finally:
            release.set()
            await consumer.stop()
    committed_at_stop = await _committed(kafka_available, group, backlog)

    second = Handler()
    consumer = _consumer(kafka_available, backlog, group)
    await consumer.start(second)
    try:
        async def all_committed() -> bool:
            return await _committed(kafka_available, group, backlog) == _A + _B

        await _until(all_committed)
    finally:
        await consumer.stop()

    print(f"real kafka restart: committed {committed_at_stop} at stop, "
          f"{len({*first.seen} & {*second.seen})} records replayed")
    assert committed_at_stop <= 50
    assert {*first.seen} | {*second.seen} == (
        {f"a-{i:05d}" for i in range(_A)} | {f"b-{i}" for i in range(_B)}
    )
