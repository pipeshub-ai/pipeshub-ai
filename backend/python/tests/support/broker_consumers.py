"""Indexing consumers driven through their real per-message wrappers, for tests that need a broker's behaviour."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.modules.indexing.embed_resume import DELIVERY_ID_KEY
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-broker-consumers")


def body(record_id: str, **payload: Any) -> str:  # noqa: ANN401
    return json.dumps({"eventType": "newRecord", "payload": {"recordId": record_id, "extension": "txt", **payload}})


class Recorder:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def handler(self, message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        self.payloads.append(dict(message.payload))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r"))

    @property
    def ids(self) -> list[str]:
        return [p[DELIVERY_ID_KEY] for p in self.payloads]


def redis_consumer() -> tuple[IndexingRedisStreamsConsumer, Recorder]:
    consumer = IndexingRedisStreamsConsumer(
        LOGGER, RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
        retry_manager=None, producer=None, governor=make_test_governor(logger_name="test-delivery-identity"),
    )
    consumer.running = True
    consumer._pending_message_is_owned = AsyncMock(return_value=True)  # type: ignore[method-assign]
    consumer._ack_message = AsyncMock()  # type: ignore[method-assign]
    consumer._requeue_message = AsyncMock()  # type: ignore[method-assign]
    recorder = Recorder()
    consumer.message_handler = recorder.handler  # type: ignore[assignment]
    return consumer, recorder


def kafka_consumer() -> tuple[IndexingKafkaConsumer, Recorder]:
    consumer = IndexingKafkaConsumer(
        LOGGER, KafkaConsumerConfig(
            topics=["idx"], client_id="c", group_id="g", auto_offset_reset="earliest", enable_auto_commit=False,
            bootstrap_servers=["broker:9092"], ssl=False, sasl=None,
        ),
    )
    consumer.parsing_semaphore = asyncio.Semaphore(1)
    consumer.indexing_semaphore = asyncio.Semaphore(1)
    recorder = Recorder()
    consumer.message_handler = recorder.handler  # type: ignore[assignment]
    return consumer, recorder


def kafka_record(offset: int, body: str) -> MagicMock:
    message = MagicMock()
    message.topic, message.partition, message.offset = "idx", 0, offset
    message.value = body.encode()
    return message
