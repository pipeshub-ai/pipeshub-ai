"""F34: a Postgres-queue message that json can never decode is dead-lettered on its first claim.

Before, its RecursionError / ValueError escaped ``_process``: the claim was neither acked nor nacked, so the message
came back each time its visibility ran out, with an ERROR and a stack each time, until the delivery limit.
"""
from logging import getLogger
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from tests.support.undecodable_json import UNDECODABLE


@pytest.mark.parametrize("indexing", [True, False], ids=["indexing", "generic"])
@pytest.mark.parametrize("value", UNDECODABLE.values(), ids=UNDECODABLE.keys())
async def test_dead_lettered_on_the_first_claim(indexing: bool, value: str) -> None:
    sink = AsyncMock()
    consumer = PgMessagingConsumer(getLogger("test.f34.pg"), RedisStreamsConfig(topics=["t"], group_id="g"),
                                   indexing=indexing, disposition_sink=sink)
    consumer.message_handler = AsyncMock(return_value=True)
    consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())

    await consumer._run_one(Delivery(topic="t", message_id=1, attempts=1, value=value, key=None))

    consumer.queue.dead.assert_awaited_once()
    reason = consumer.queue.dead.await_args.args[2]
    assert reason.startswith("JSON parsing failed") or reason == "envelope rejected: malformed"
    consumer.queue.nack.assert_not_awaited()
    consumer.message_handler.assert_not_awaited()
    sink.on_message_abandoned.assert_awaited_once()
