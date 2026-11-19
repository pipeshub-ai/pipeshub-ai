"""Claim steps on the PostgreSQL queue consumer, against a real database: one single-topic claim per step, in the
order and amounts the steps give, and every message is still delivered."""

import asyncio
from logging import getLogger
from typing import Any

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    RedisStreamsConfig,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.services.messaging.scheduling.interface import IndexingFairness
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor

pytestmark = pytest.mark.integration


async def test_steps_claim_single_topics_in_the_given_order_and_everything_is_delivered(runtime: Any, uid: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setenv("PG_QUEUE_ORDERED_TOPICS", "none")
    queue = PgQueue(runtime)
    first, second, group = f"first-{uid}", f"second-{uid}", f"g-{uid}"
    producer = PgMessagingProducer(getLogger("t"), queue=queue)
    for topic in (first, second):
        await queue.ensure_group(topic, group, from_start=True)
    for i in range(6):
        await producer.send_event(first, "newRecord", {"recordId": f"a{i}", "orgId": "o", "connectorId": "c"})
        await producer.send_event(second, "newRecord", {"recordId": f"b{i}", "orgId": "o", "connectorId": "c"})
    asked: list[tuple[str, int]] = []
    real_claim = queue.claim

    async def spy(group_id: str, topics: list[str], consumer: str, limit: int, visibility_s: float, **kw: Any) -> Any:  # noqa: ANN401
        asked.append((",".join(topics), limit))
        return await real_claim(group_id, topics, consumer, limit, visibility_s, **kw)

    queue.claim = spy  # type: ignore[method-assign]
    seen: list[str] = []

    async def handler(message: Any) -> Any:  # noqa: ANN401
        seen.append(message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    fairness = IndexingFairness(
        key_fields=("orgId", "connectorId"), key_extractor=CompositeKeyExtractor(fields=("orgId", "connectorId")),
        claim_steps=lambda free, _held: [(second, 1), (first, free)],
    )
    config = RedisStreamsConfig(topics=[first, second], group_id=group, client_id=f"c-{uid}", batch_size=4, block_ms=50)
    consumer = PgMessagingConsumer(getLogger("t"), config, indexing=True, queue=queue, fairness=fairness)
    await consumer.start(handler)
    try:
        for _ in range(200):
            if len(seen) == 12:
                break
            await asyncio.sleep(0.1)
    finally:
        await consumer.stop()
    assert sorted(seen) == sorted([f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)])
    assert asked and all("," not in topics for topics, _ in asked)
    assert asked[0] == (second, 1)
    assert all(limit == 1 for topics, limit in asked if topics == second)
