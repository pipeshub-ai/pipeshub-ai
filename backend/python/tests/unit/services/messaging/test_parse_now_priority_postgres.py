"""The priority lane on PostgreSQL: a second consumer instance with a lease cap of its own. There is no
per-org share and no backpressure pause on this broker, for the normal consumer either."""

from __future__ import annotations

from logging import getLogger
from unittest.mock import AsyncMock, MagicMock

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
    Topic,
)
from app.services.messaging.consumer_concurrency import PriorityLane
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.consumer import PgMessagingConsumer

LOG = getLogger("pg-priority")
CFG = RedisStreamsConfig(topics=[Topic.RECORD_EVENTS_PRIORITY.value], group_id="records_priority_consumer_group")
LANE = PriorityLane(heavy_limit=1, light_limit=3, workers=2)


def _consumer(lane: PriorityLane | None = LANE, **kwargs: object) -> PgMessagingConsumer:
    return PgMessagingConsumer(LOG, CFG, indexing=True, priority_lane=lane, **kwargs)  # type: ignore[arg-type]


def test_the_factory_builds_a_lane_consumer() -> None:
    consumer = MessagingFactory.create_consumer(
        LOG, CFG, broker_type=MessageBrokerType.POSTGRES, consumer_type=ConsumerType.INDEXING, priority_lane=LANE,
    )

    assert isinstance(consumer, PgMessagingConsumer)
    assert consumer.priority_lane is LANE


def test_it_runs_no_more_handlers_than_its_cap() -> None:
    assert _consumer()._concurrency() == 4
    assert _consumer(None)._concurrency() > 0  # the normal consumer keeps MAX_CONCURRENT_INDEXING


def test_the_topic_is_not_ordered_like_record_events(monkeypatch) -> None:
    monkeypatch.delenv("PG_QUEUE_ORDERED_TOPICS", raising=False)
    ordered = {"record-events"}

    topic = Topic.RECORD_EVENTS_PRIORITY.value
    assert not (topic in ordered or topic.split(".")[0] in ordered)


async def test_it_takes_its_cluster_slot_from_a_pool_of_its_own() -> None:
    manager = MagicMock(try_acquire=AsyncMock(return_value=True))
    consumer = _consumer(concurrency_manager=manager)
    consumer.running = True

    slot = await consumer._take_slot(MagicMock())

    assert slot is not None and slot[0] == "indexing:priority"
    pool, _owner, limit, _seconds = manager.try_acquire.await_args.args
    # 4 records in a process, two processes in the container.
    assert (pool, limit) == ("indexing:priority", 8)
