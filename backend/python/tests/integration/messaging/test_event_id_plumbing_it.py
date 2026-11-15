"""The verified envelope event id reaches the record handler as ``_event_id`` on the Kafka and
Redis Streams indexing consumers (the PostgreSQL leg is in
tests/integration/pg_runtime/test_replay_delivery_id_pg.py). A sender-supplied ``_event_id`` never survives.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.core.org_context import OrgContext, bind
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.redis.config import RedisConnectionConfig
from app.services.redis.standalone_provider import StandaloneRedisProvider
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    create_kafka_topic,
    delete_kafka_topic,
)
from tests.integration.messaging.test_replay_delivery_id import (
    _consumer as _redis_consumer,
)
from tests.integration.messaging.test_replay_delivery_id_kafka import (
    _consumer as _kafka_consumer,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
LOG = logging.getLogger("it-event-id")


def _recording(seen: list[dict[str, Any]]):  # noqa: ANN202
    async def handle(message: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        seen.append({k: message.payload.get(k) for k in ("_event_id", "_delivery_id")})
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)
    return handle


async def _until(pred) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while not pred():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.1)


PAYLOAD = {"recordId": "rec-1", "orgId": "org-1", "connectorId": "c1", "extension": "txt", "mimeType": "text/plain",
           "_event_id": "forged-by-sender"}


async def test_the_envelope_event_id_reaches_the_handler_kafka(kafka_available, unique_suffix, signing) -> None:
    topic = f"record-events-evt-{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 1)
    seen: list[dict[str, Any]] = []
    producer = KafkaMessagingProducer(LOG, KafkaProducerConfig(bootstrap_servers=[kafka_available], client_id="it-evt"))
    await producer.initialize()
    try:
        with bind(OrgContext("org-1", None, "service", "job:test", "t")):
            assert await producer.send_event(topic=topic, event_type="newRecord", payload=dict(PAYLOAD), key="rec-1")
    finally:
        await producer.cleanup()
    consumer = _kafka_consumer(kafka_available, topic, f"it-evt-{unique_suffix}")
    await consumer.start(_recording(seen))
    try:
        await _until(lambda: seen)
    finally:
        await consumer.stop()
        await delete_kafka_topic(kafka_available, topic)
    assert seen[0]["_event_id"] and seen[0]["_event_id"] != "forged-by-sender"
    uuid.UUID(seen[0]["_event_id"])
    assert seen[0]["_delivery_id"]


async def test_the_envelope_event_id_reaches_the_handler_redis_streams(redis_available, unique_suffix, signing) -> None:
    host, port = redis_available
    stream = f"record-events-evt-{unique_suffix}"
    seen: list[dict[str, Any]] = []
    provider = StandaloneRedisProvider(RedisConnectionConfig(host=host, port=port))
    producer = RedisStreamsProducer(LOG, RedisStreamsConfig(host=host, port=port, client_id="p", group_id="-",
                                                            topics=[stream]), provider=provider)
    await producer.initialize()
    try:
        with bind(OrgContext("org-1", None, "service", "job:test", "t")):
            assert await producer.send_event(topic=stream, event_type="newRecord", payload=dict(PAYLOAD), key="rec-1")
    finally:
        await producer.cleanup()
    consumer = _redis_consumer(host, port, stream, f"it-evt-{unique_suffix}", claim_min_idle_ms=500)
    await consumer.start(_recording(seen))
    try:
        await _until(lambda: seen)
    finally:
        await consumer.stop()
    assert seen[0]["_event_id"] and seen[0]["_event_id"] != "forged-by-sender"
    uuid.UUID(seen[0]["_event_id"])


async def test_an_unsigned_message_still_carries_an_event_id_kafka(kafka_available, unique_suffix) -> None:
    """A default install signs nothing; the producer's event id still reaches the handler."""
    topic = f"record-events-evt-unsigned-{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 1)
    seen: list[dict[str, Any]] = []
    producer = KafkaMessagingProducer(LOG, KafkaProducerConfig(bootstrap_servers=[kafka_available], client_id="it-evt"))
    await producer.initialize()
    try:
        assert await producer.send_event(topic=topic, event_type="newRecord", payload=dict(PAYLOAD), key="rec-1")
    finally:
        await producer.cleanup()
    consumer = _kafka_consumer(kafka_available, topic, f"it-evt-unsigned-{unique_suffix}")
    await consumer.start(_recording(seen))
    try:
        await _until(lambda: seen)
    finally:
        await consumer.stop()
        await delete_kafka_topic(kafka_available, topic)
    assert seen[0]["_event_id"] != "forged-by-sender"
    uuid.UUID(seen[0]["_event_id"])
