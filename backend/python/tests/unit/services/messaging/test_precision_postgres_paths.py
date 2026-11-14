"""The Postgres queue consumer hands back vector-writing events through its real settle path.

The real consumer, filter and verdict run; only the queue (the database boundary) is a fake. The handler is a
recorded stub, so "nothing ran" is what it saw.
"""

import json
import logging
import random
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.precision_check import (
    PrecisionFilter,
    PrecisionVerdict,
    Verdict,
)
from app.services.resource_governor.models import ParseTier

log = logging.getLogger("precision-pg")


def _delivery(event: str = "newRecord", attempts: int = 1, message_id: int = 5) -> Delivery:
    payload = {"recordId": "r1", "orgId": "org-a", "connectorId": "c1", "virtualRecordId": "v1", "extension": "txt", "mimeType": "text/plain"}
    value = json.dumps({"eventType": event, "payload": payload, "requestId": "q1"})
    return Delivery(topic="record-events", message_id=message_id, attempts=attempts, value=value, key=None)


class _Handler:
    """Records what reached the record handler."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def __call__(self, message) -> AsyncGenerator[PipelineEvent, None]:
        self.seen.append(message.eventType)
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)


def _consumer(mismatch: bool, *, with_filter: bool = True) -> PgMessagingConsumer:
    verdict = PrecisionVerdict(fetch_states=AsyncMock(), indexes=AsyncMock(), logger=log)
    verdict._verdict = Verdict(mismatch, ["x"] if mismatch else [])
    consumer = PgMessagingConsumer(
        log, RedisStreamsConfig(topics=["record-events"], group_id="g"), indexing=True,
        precision_filter=PrecisionFilter(verdict, log, rng=random.Random(3)) if with_filter else None,
    )
    consumer.message_handler = _Handler()
    consumer.queue = MagicMock(ack=AsyncMock(), nack=AsyncMock(), dead=AsyncMock())
    return consumer


@pytest.mark.asyncio
async def test_a_vector_writing_event_is_nacked_with_a_refund_and_the_handler_never_runs() -> None:
    consumer = _consumer(True)
    await consumer._run_one(_delivery())
    assert consumer.message_handler.seen == []
    consumer.queue.ack.assert_not_awaited()
    consumer.queue.dead.assert_not_awaited()
    args, kwargs = consumer.queue.nack.await_args
    assert 48 <= args[2] <= 72 and kwargs["refund"] is True  # the first hand-back waits about a minute


@pytest.mark.asyncio
async def test_the_next_hand_back_of_the_same_message_waits_longer() -> None:
    consumer = _consumer(True)
    await consumer._run_one(_delivery())
    await consumer._run_one(_delivery(attempts=1))
    first, second = (c.args[2] for c in consumer.queue.nack.await_args_list)
    assert second > first * 5


@pytest.mark.asyncio
async def test_a_hand_back_at_the_last_attempt_is_not_dead_lettered() -> None:
    consumer = _consumer(True)
    await consumer._run_one(_delivery(attempts=3))
    consumer.queue.dead.assert_not_awaited()
    assert consumer.queue.nack.await_args.kwargs["refund"] is True


@pytest.mark.asyncio
async def test_a_delete_and_a_match_verdict_reach_the_handler() -> None:
    consumer = _consumer(True)
    await consumer._run_one(_delivery(event="deleteRecord"))
    assert consumer.message_handler.seen == ["deleteRecord"]
    consumer.queue.nack.assert_not_awaited()
    consumer = _consumer(False)
    await consumer._run_one(_delivery())
    assert consumer.message_handler.seen == ["newRecord"]


@pytest.mark.asyncio
async def test_without_a_filter_nothing_is_handed_back() -> None:
    consumer = _consumer(True, with_filter=False)
    await consumer._run_one(_delivery())
    assert consumer.message_handler.seen == ["newRecord"]
