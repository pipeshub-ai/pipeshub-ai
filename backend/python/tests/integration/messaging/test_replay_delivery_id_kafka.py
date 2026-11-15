"""Kafka replay legs.

The backend-matrix "messaging · kafka" job runs this folder with ``-k kafka``, so every test name here says kafka.

Kafka: a message whose first delivery never finished (the consumer stopped before it could commit) is delivered
again from the committed offset to the next consumer of the group, with the same ``_delivery_id``.

Replayed window: two completed updates of one record, neither committed (the process stopped between the COMPLETED write and
the offset commit), replayed to the next consumer of the group: the real record handler, reading the real replay set
in a real Redis, acknowledges both without a download.

Requires Kafka (KAFKA_IT_BOOTSTRAP) and Redis (REDIS_IT_HOST / REDIS_IT_PORT).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from redis.asyncio import Redis

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.modules.indexing import replay_set
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
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    REDIS_HOST,
    REDIS_PORT,
    create_kafka_topic,
    delete_kafka_topic,
)
from tests.support.record_handler import make_record_handler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
LOG = logging.getLogger("it-replay-kafka")


async def _until(predicate, what: str, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


def _consumer(bootstrap: str, topic: str, group: str) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        LOG,
        KafkaConsumerConfig(
            topics=[topic], client_id=f"{group}-client", group_id=group, auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=[bootstrap],
        ),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=False, key_fields=("orgId", "connectorId"), max_buffered_messages=10, max_per_entity_messages=10
        ),
    )


async def _publish(bootstrap: str, topic: str, events: list[tuple[str, dict]]) -> None:
    producer = KafkaMessagingProducer(LOG, KafkaProducerConfig(bootstrap_servers=[bootstrap], client_id="it-replay"))
    await producer.initialize()
    try:
        for event_type, payload in events:
            assert await producer.send_event(topic=topic, event_type=event_type, payload=payload, key=payload["recordId"])
    finally:
        await producer.cleanup()


@pytest.fixture
async def topic(kafka_available: str, unique_suffix: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    # The stopped consumer must give up its parked handler quickly: that is the crash before the commit.
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "2")
    name = f"record-events-replay-{unique_suffix}"
    await create_kafka_topic(kafka_available, name, 1)
    yield name
    await delete_kafka_topic(kafka_available, name)


async def test_redelivery_keeps_the_delivery_id_kafka(kafka_available, topic, unique_suffix) -> None:
    payload = {"recordId": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "extension": "txt", "mimeType": "text/plain"}
    await _publish(kafka_available, topic, [("newRecord", payload)])
    group = f"it-replay-id-{unique_suffix}"
    first: list[str | None] = []
    second: list[str | None] = []
    gate = threading.Event()

    async def parks(message: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        first.append(message.payload.get("_delivery_id"))
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        await asyncio.to_thread(gate.wait, DRAIN_TIMEOUT_SECONDS)  # never finishes before the stop
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    async def completes(message: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        second.append(message.payload.get("_delivery_id"))
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    consumer = _consumer(kafka_available, topic, group)
    await consumer.start(parks)
    try:
        await _until(lambda: first, "the first delivery")
    finally:
        await consumer.stop()
        gate.set()
    consumer = _consumer(kafka_available, topic, group)
    await consumer.start(completes)
    try:
        await _until(lambda: second, "the delivery from the committed offset")
    finally:
        await consumer.stop()

    assert first[0], "the consumer stamps a delivery id before the handler reads it"
    assert second[0] == first[0], "a redelivery from the committed offset carries the same delivery id"


def _completed_record(record_id: str) -> dict[str, Any]:
    """The record as the graph holds it: COMPLETED, and stamped by each COMPLETED write."""
    return {
        "_key": record_id, "virtualRecordId": "vr1", "contentRev": "rev-1", "mimeType": "application/pdf",
        "indexingStatus": ProgressStatus.COMPLETED.value, "extractionStatus": ProgressStatus.COMPLETED.value,
        "lastIndexTimestamp": None,
    }


def _record_handler(record: dict[str, Any]):  # noqa: ANN202
    """The real RecordEventHandler over ``record``, with a real Redis for the replay set."""
    handler = make_record_handler()
    gp = handler.event_processor.graph_provider
    gp.get_document = AsyncMock(side_effect=lambda *_a, **_k: dict(record))
    gp.update_queued_duplicates_status = AsyncMock(return_value=0)
    handler.event_processor.processor.indexing_pipeline = AsyncMock()
    clients: dict[int, Redis] = {}

    async def redis_client() -> Redis:  # one client per loop, as the handler's own
        loop = id(asyncio.get_running_loop())
        if loop not in clients:
            clients[loop] = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        return clients[loop]

    handler._redis_client = redis_client
    return handler


@pytest.mark.parametrize("first_entry_lost", [False, True], ids=["remembered", "first-entry-lost"])
async def test_window_of_two_events_for_one_record_kafka(
    kafka_available, redis_available, topic, unique_suffix, first_entry_lost: bool
) -> None:
    # Its own record per case: the replay-set keys are the record's, and cases may run on parallel workers.
    record_id = f"r-{unique_suffix}"
    redis = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    await redis.delete(replay_set.hash_key(record_id), replay_set.order_key(record_id))
    updates = [(EventTypes.UPDATE_RECORD.value, {"recordId": record_id, "orgId": "org-1", "virtualRecordId": "vr1",
                                                  "mimeType": "application/pdf", "extension": "pdf", "n": n})
               for n in (1, 2)]
    # A default install: nothing is signed, and each message still carries the event id the producer
    # stamped, which is what identifies a replay.
    await _publish(kafka_available, topic, updates)
    group = f"it-replay-window-{unique_suffix}"
    first_downloads: list[str] = []
    replay_downloads: list[str] = []
    replay_done: list[str] = []
    record = _completed_record(record_id)
    completions = iter(range(1_000, 2_000))

    def wrap(handler: Any, downloads: list[str], done: list[str]):  # noqa: ANN202, ANN401
        async def handle(message: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
            delivery = message.payload.get("_event_id")
            async for event in handler.process_event(message.eventType, message.payload):
                if event.event == IndexingEvent.START_DOWNLOAD:
                    downloads.append(delivery)
                    # The run goes on to its COMPLETED write; the sink then remembers the delivery.
                    record["lastIndexTimestamp"] = next(completions)
                    await replay_set.note_record_completed(record_id, record["lastIndexTimestamp"])
                    yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)
                    done.append(delivery)
                    return
                yield event
            done.append(delivery)
        return handle

    # First run: U1 and U2 both complete and are committed.
    first_done: list[str] = []
    consumer = _consumer(kafka_available, topic, group)
    await consumer.start(wrap(_record_handler(record), first_downloads, first_done))
    try:
        await _until(lambda: len(first_done) >= 2, "both updates to complete")
        await asyncio.sleep(1.0)  # let the commit land
    finally:
        await consumer.stop()
    stored = await redis.hgetall(replay_set.hash_key(record_id))
    assert len(first_downloads) == 2 and set(first_downloads) <= set(stored), (first_downloads, stored)
    if first_entry_lost:
        # U1's write to the set failed: only U2 is remembered.
        await redis.hdel(replay_set.hash_key(record_id), first_downloads[0])
        await redis.zrem(replay_set.order_key(record_id), first_downloads[0])

    # The group's committed offset goes back to before U1 (a lost commit, then a rebalance).
    from aiokafka import AIOKafkaConsumer, TopicPartition

    rewind = AIOKafkaConsumer(bootstrap_servers=kafka_available, group_id=group, enable_auto_commit=False)
    await rewind.start()
    try:
        tp = TopicPartition(topic, 0)
        rewind.assign([tp])
        await rewind.commit({tp: 0})
    finally:
        await rewind.stop()

    consumer = _consumer(kafka_available, topic, group)
    await consumer.start(wrap(_record_handler(record), replay_downloads, replay_done))
    try:
        await _until(lambda: len(replay_done) >= 2, "the replayed window", timeout=30)
    finally:
        await consumer.stop()
        await redis.delete(replay_set.hash_key(record_id), replay_set.order_key(record_id))
        await redis.aclose()

    assert sorted(replay_done) == sorted(first_downloads), "both replays reached the handler and finished"
    if first_entry_lost:
        # U1 runs again and may index its own, older bytes, so U2 must run after it.
        assert replay_downloads == first_downloads, "an older update that ran again makes the newer one run"
    else:
        assert replay_downloads == [], "a replayed window of completed deliveries downloads nothing"
