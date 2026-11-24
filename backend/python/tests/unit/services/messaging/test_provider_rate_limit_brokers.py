"""A record handed back for a provider's rate limit is uncounted on every broker, and bounded by time, not by a count.

Driven through the consumers' real per-message wrappers (Kafka, Redis Streams) and the PostgreSQL consumer's settle, with a
handler that always raises the hand-back: 20 hand-backs of 600 s (over three hours of rate limiting) neither dead-letter the
message nor charge the record a delivery attempt, and each comes back after the provider's wait.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.provider_rate_limit import ProviderRateLimitRequeue
from tests.support.broker_consumers import (
    body,
    kafka_consumer,
    kafka_record,
    redis_consumer,
)

LOGGER = logging.getLogger("provider-rate-limit-brokers")
WAIT = 600.0
HAND_BACKS = 20


def _handler() -> Any:  # noqa: ANN401
    async def handler(message: Any):  # noqa: ANN202, ANN401
        raise ProviderRateLimitRequeue("r1: provider rate limiting", delay_s=WAIT)
        yield  # pragma: no cover

    return handler


def _retry_manager() -> AsyncMock:
    manager = AsyncMock()
    manager.increment_and_check.return_value = (1, False)
    manager.unfinished_attempts.return_value = 0
    manager.start_attempt.return_value = 0
    manager.get_retry_count.return_value = 0
    deliveries = {"n": 0}

    async def record_delivery(_message_id: str) -> int:
        deliveries["n"] += 1
        return deliveries["n"]

    manager.record_delivery.side_effect = record_delivery
    return manager


async def _drive(kind: str, deliveries: int) -> dict[str, Any]:
    if kind == "kafka":
        consumer, _ = kafka_consumer()
        consumer.running = True
        consumer._requeue_message = AsyncMock()  # type: ignore[method-assign]
        consumer._IndexingKafkaConsumer__abandon_message = AsyncMock()  # type: ignore[attr-defined]
        abandon = consumer._IndexingKafkaConsumer__abandon_message  # type: ignore[attr-defined]
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
    else:
        consumer, _ = redis_consumer()
        consumer._abandon_message = AsyncMock()  # type: ignore[method-assign]
        abandon = consumer._abandon_message
    consumer.message_handler = _handler()  # type: ignore[assignment]
    manager = _retry_manager()
    consumer.retry_manager = manager
    for i in range(1, deliveries + 1):
        if kind == "kafka":
            await wrapper(kafka_record(i, body("r1", _retry_tracking_id="idx-0-1") if i > 1 else body("r1")))
        else:
            value = body("r1", _retry_tracking_id="1-0") if i > 1 else body("r1")
            await consumer._process_message_wrapper("t", f"{i}-0", {"value": value})
        if abandon.await_count:
            break
    return {
        "abandoned": abandon.await_count,
        "reason": abandon.await_args.kwargs.get("reason") if abandon.await_count else None,
        "delays": [c.kwargs.get("delay_s") for c in consumer._requeue_message.await_args_list],
        "retry_counts": {c.kwargs.get("retry_count") for c in consumer._requeue_message.await_args_list},
        "attempts_counted": manager.increment_and_check.await_count,
    }


@pytest.mark.parametrize("kind", ["kafka", "redis"])
async def test_a_handback_is_uncounted_and_carries_the_wait(kind: str) -> None:
    out = await _drive(kind, 1)
    assert out["attempts_counted"] == 0 and out["delays"] == [WAIT] and out["retry_counts"] == {0}


@pytest.mark.parametrize("kind", ["kafka", "redis"])
async def test_twenty_handbacks_neither_dead_letter_nor_charge_the_record(kind: str) -> None:
    out = await _drive(kind, HAND_BACKS)
    assert out["abandoned"] == 0, f"{kind}: dead-lettered after hand-backs: {out['reason']}"
    assert out["attempts_counted"] == 0, f"{kind}: a hand-back was charged as a delivery attempt"
    assert out["delays"] == [WAIT] * HAND_BACKS


async def test_postgres_never_charges_a_handback_either() -> None:
    from app.services.messaging.worker_loop import WorkerLoop

    worker = WorkerLoop(LOGGER, thread_name="provider-rate-limit")
    try:
        consumer = PgMessagingConsumer(LOGGER, RedisStreamsConfig(topics=["t"], group_id="g"), indexing=True, worker=worker)
        consumer.message_handler = _handler()  # type: ignore[assignment]
        consumer.queue = MagicMock(dead=AsyncMock(), ack=AsyncMock(), nack=AsyncMock())
        value = json.dumps({**json.loads(body("r1")), "requestId": "req", "orgId": "org-1"})
        for _ in range(260):  # well past the 200 hand-backs after which a counted one is charged
            await consumer._settle(Delivery(topic="t", message_id=1, attempts=1, value=value, key=None))
            call = consumer.queue.nack.await_args
            assert call.kwargs.get("refund") is True, "a hand-back was charged as a failed attempt"
            assert call.args[2] >= WAIT
        consumer.queue.dead.assert_not_awaited()
    finally:
        worker.stop()
