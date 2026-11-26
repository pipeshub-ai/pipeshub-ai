"""Each indexing consumer names the event it delivers, the same way across its retries.

A resumed embedding trusts a marker only when the delivery id matches, so a consumer that put a
different id on a re-queue of the same event would switch the whole feature off without any error.
The consumers are driven through their real per-message wrappers with a fake handler that records the
payload it was given.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.indexing.embed_resume import DELIVERY_ID_KEY, RESUME_DELIVERY_ID_KEY
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
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.worker_loop import WorkerLoop
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-delivery-identity")


def _body(record_id: str, **payload: Any) -> str:  # noqa: ANN401
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


# ---- Redis Streams -------------------------------------------------------------------------------------------------------------
def _redis() -> tuple[IndexingRedisStreamsConsumer, Recorder]:
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


@pytest.mark.asyncio
class TestRedisStreams:
    async def test_a_requeue_of_the_same_event_carries_the_first_deliverys_id(self) -> None:
        # Kills: stamping the raw stream entry id, which changes on every re-queue.
        consumer, recorder = _redis()
        await consumer._process_message_wrapper("t", "100-0", {"value": _body("r1")})
        await consumer._process_message_wrapper("t", "205-0", {"value": _body("r1", _retry_tracking_id="100-0")})
        await consumer._process_message_wrapper("t", "300-0", {"value": _body("r1", _retry_tracking_id="100-0")})
        assert recorder.ids == ["100-0", "100-0", "100-0"]

    async def test_the_same_entry_delivered_again_keeps_its_id(self) -> None:
        consumer, recorder = _redis()
        for _ in range(2):
            await consumer._process_message_wrapper("t", "100-0", {"value": _body("r1")})
        assert recorder.ids == ["100-0", "100-0"]

    async def test_a_new_event_for_the_same_record_has_another_id(self) -> None:
        consumer, recorder = _redis()
        await consumer._process_message_wrapper("t", "100-0", {"value": _body("r1")})
        await consumer._process_message_wrapper("t", "400-0", {"value": _body("r1")})
        assert recorder.ids[0] != recorder.ids[1]

    async def test_a_republish_by_stale_recovery_continues_the_interrupted_run(self) -> None:
        consumer, recorder = _redis()
        await consumer._process_message_wrapper(
            "t", "500-0", {"value": _body("r1", _retry_tracking_id="recovery:r1:3", **{RESUME_DELIVERY_ID_KEY: "100-0"})},
        )
        assert recorder.ids == ["100-0"]


# ---- Kafka ---------------------------------------------------------------------------------------------------------------------
def _kafka() -> tuple[IndexingKafkaConsumer, Recorder]:
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


def _record(offset: int, body: str) -> MagicMock:
    message = MagicMock()
    message.topic, message.partition, message.offset = "idx", 0, offset
    message.value = body.encode()
    return message


@pytest.mark.asyncio
class TestKafka:
    async def test_a_requeue_of_the_same_event_carries_the_first_deliverys_id(self) -> None:
        consumer, recorder = _kafka()
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
        await wrapper(_record(7, _body("r1")))
        await wrapper(_record(90, _body("r1", _retry_tracking_id="idx-0-7")))
        assert recorder.ids == ["idx-0-7", "idx-0-7"]

    async def test_a_new_event_for_the_same_record_has_another_id(self) -> None:
        consumer, recorder = _kafka()
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
        await wrapper(_record(7, _body("r1")))
        await wrapper(_record(8, _body("r1")))
        assert recorder.ids[0] != recorder.ids[1]

    async def test_a_republish_by_stale_recovery_continues_the_interrupted_run(self) -> None:
        consumer, recorder = _kafka()
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
        await wrapper(_record(9, _body("r1", _retry_tracking_id="recovery:r1:3", **{RESUME_DELIVERY_ID_KEY: "idx-0-7"})))
        assert recorder.ids == ["idx-0-7"]


# ---- PostgreSQL ----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestPostgres:
    async def _deliver(self, rows: list[tuple[int, int, str]]) -> list[str]:
        worker = WorkerLoop(LOGGER, thread_name="test-delivery-identity")
        try:
            recorder = Recorder()
            consumer = PgMessagingConsumer(
                LOGGER, RedisStreamsConfig(topics=["t"], group_id="g"), indexing=True, worker=worker,
            )
            consumer.message_handler = recorder.handler  # type: ignore[assignment]
            for message_id, attempts, body in rows:
                value = json.dumps({**json.loads(body), "requestId": "req", "orgId": "org-1"})
                await consumer._process(
                    Delivery(topic="t", message_id=message_id, attempts=attempts, value=value, key=None), final=False,
                )
            return recorder.ids
        finally:
            worker.stop()

    async def test_a_hand_back_of_the_same_row_keeps_its_id_and_a_new_row_has_another(self) -> None:
        # A hand-back is a nack of the same row: the row names the event, not the delivery.
        ids = await self._deliver([(11, 1, _body("r1")), (11, 2, _body("r1")), (11, 3, _body("r1")), (12, 1, _body("r1"))])
        assert ids[0] == ids[1] == ids[2] != ids[3]

    async def test_a_republish_by_stale_recovery_continues_the_interrupted_run(self) -> None:
        ids = await self._deliver([(13, 1, _body("r1", **{RESUME_DELIVERY_ID_KEY: "t:11"}))])
        assert ids == ["t:11"]
