"""A redelivered message keeps its ``_delivery_id``: the assumption the replay set rests on.

The replay guard recognises a replay by the delivery id (and the envelope event id) the consumer stamps on the
payload. It only works if a redelivery of one message carries the same id as the first delivery. This pins that
on a real broker, with the consumer's own stamping and nothing patched: the first delivery of a message fails
with a transient error, and the redelivery sees the same ``_delivery_id``.

Covered here: Redis Streams (a failed delivery redelivered). The Kafka leg (a redelivery from the committed offset)
is in test_replay_delivery_id_kafka.py, and the PostgreSQL queue leg (a nack of the same row) in
tests/integration/pg_runtime/test_replay_delivery_id_pg.py.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d redis-messaging-it
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import DRAIN_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.messaging.config import StreamMessage

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _until(predicate, what: str, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


def _consumer(host: str, port: int, stream: str, group: str, *, claim_min_idle_ms: int) -> IndexingRedisStreamsConsumer:
    config = RedisStreamsConfig(
        host=host, port=port, client_id=f"{group}-client", group_id=group, topics=[stream], batch_size=10,
        block_ms=200, claim_min_idle_ms=claim_min_idle_ms,
    )
    fair = FairSchedulerConfig(
        enabled=False, key_fields=("orgId", "connectorId"), max_buffered_messages=10, max_per_entity_messages=10
    )
    return IndexingRedisStreamsConsumer(logging.getLogger("it-delivery-id"), config, fair_scheduler_config=fair)


@pytest.fixture
async def stream(redis_available: tuple[str, int], unique_suffix: str) -> AsyncIterator[tuple[Redis, str]]:
    host, port = redis_available
    client = Redis(host=host, port=port, decode_responses=True)
    name = f"record-events-delivery-id-{unique_suffix}"
    yield client, name
    await client.delete(name)
    await client.aclose()


async def test_redelivery_keeps_the_delivery_id_redis_streams(
    redis_available: tuple[str, int], stream: tuple[Redis, str], unique_suffix: str
) -> None:
    host, port = redis_available
    client, name = stream
    group = f"it-delivery-id-{unique_suffix}"
    payload = {"recordId": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "extension": "txt", "mimeType": "text/plain"}
    await client.xadd(name, {"value": json.dumps({"eventType": "newRecord", "payload": payload})})

    seen: list[str | None] = []

    async def handler(message: StreamMessage) -> AsyncIterator[PipelineEvent]:
        seen.append(message.payload.get("_delivery_id"))
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        if len(seen) == 1:
            # The first delivery fails with a transient error: the consumer redelivers the same message.
            raise ConnectionError("transient")
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    consumer = _consumer(host, port, name, group, claim_min_idle_ms=500)
    await consumer.start(handler)
    try:
        await _until(lambda: len(seen) >= 2, "the redelivery")
    finally:
        await consumer.stop()

    assert seen[0], "the consumer stamps a delivery id before the handler reads it"
    assert seen[1] == seen[0], "a redelivery of the same message carries the same delivery id"
