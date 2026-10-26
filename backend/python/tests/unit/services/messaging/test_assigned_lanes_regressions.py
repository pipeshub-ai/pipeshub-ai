"""Regressions for connectors that share a Redis Streams lane by chance.

A community user's Slack connector waited behind about 200,000 GitLab events
because both connector ids hashed to the same one of eight lanes, while the
other seven sat empty. These tests drive only the seams every publisher and
the indexing consumer already use (the producer factory, the consumer's
backlog read and its retry), so they read the same before and after the lane
map exists, and fail on the code that only hashed.
"""
from __future__ import annotations

import importlib
import logging
from itertools import count
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

from app.services.messaging.config import (
    MessageBrokerType,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

TOPIC = "record-events"
GROUP = "records_consumer_group"
LANES = 8
LANE_MAP = "{record-events}:lane-map"


def _colliding(how_many: int) -> list[str]:
    by_lane: dict[int, list[str]] = {}
    for i in count():
        name = f"connector-{i}"
        same = by_lane.setdefault(stable_lane(name, LANES), [])
        same.append(name)
        if len(same) == how_many:
            return same
    raise AssertionError("unreachable")


GITLAB, SLACK = _colliding(2)


def _lane(connector_id: str) -> str:
    return f"{TOPIC}.{stable_lane(connector_id, LANES)}"


def _another_lane(*avoid: str) -> str:
    taken = {_lane(c) for c in (*avoid, "__default__")}
    return next(f"{TOPIC}.{lane}" for lane in range(LANES) if f"{TOPIC}.{lane}" not in taken)


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider()


@pytest.fixture(autouse=True)
def _lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", str(LANES))
    try:
        assignment = importlib.import_module("app.services.messaging.lanes.assignment")
    except ImportError:
        return
    # A fresh process's worth of lane maps for each test.
    monkeypatch.setattr(assignment, "_shared", {})


async def test_two_connectors_that_hash_to_one_lane_publish_to_different_streams(
    provider: FakeRedisConnectionProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the factory and the real Redis Streams producer, the way the
    connectors service gets every producer it publishes with."""
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_ASSIGNMENT", "assigned")
    monkeypatch.setattr(
        "app.services.messaging.redis_streams.producer.get_redis_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(
        "app.services.messaging.messaging_factory.get_redis_provider",
        lambda *_args, **_kwargs: provider,
        raising=False,
    )
    producer = MessagingFactory.create_producer(
        logging.getLogger("t"), RedisStreamsConfig(), MessageBrokerType.REDIS
    )
    await producer.initialize()
    try:
        for connector in (GITLAB, SLACK):
            for i in range(3):
                await producer.send_event(
                    TOPIC,
                    "newRecord",
                    {"recordId": f"{connector}-r{i}", "orgId": "org-1", "connectorId": connector},
                )
    finally:
        await producer.cleanup()

    client = provider.get_client()
    holding = {}
    for stream in RedisLaneRouter(LANES).lane_topics(TOPIC):
        if await client.exists(stream):
            holding[stream] = await client.xlen(stream)
    assert sorted(holding.values()) == [3, 3], f"both connectors' events landed on {holding}"


def _consumer(provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("test"),
        RedisStreamsConfig(
            topics=RedisLaneRouter(LANES).lane_topics(TOPIC),
            group_id=GROUP,
            client_id="indexing-1",
        ),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    return consumer


@pytest.fixture
async def consumer(provider: FakeRedisConnectionProvider) -> IndexingRedisStreamsConsumer:
    consumer = _consumer(provider)
    for topic in consumer.config.topics:
        await consumer.redis.xgroup_create(topic, GROUP, id="0", mkstream=True)
    return consumer


async def test_a_record_whose_event_waits_on_its_assigned_lane_is_not_re_sent(
    provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
) -> None:
    """The stranded-record sweep asks the broker whether a record's event is
    still queued. Looking only at the hash lane, it finds that lane empty and
    re-sends a record that is merely waiting its turn on its real lane."""
    assigned = _another_lane(SLACK)
    client = provider.get_client()
    await client.hset(LANE_MAP, SLACK, f"v1|{assigned.rsplit('.', 1)[1]}|team|live|||")
    await client.xadd(assigned, {"value": "{}"}, id="1000-0")

    backlog = await consumer.lane_backlog(TOPIC)

    assert backlog.oldest_waiting_for({"connectorId": SLACK}) == 1000.0


async def test_an_event_left_on_the_lane_a_connector_moved_off_still_holds_its_record(
    provider: FakeRedisConnectionProvider, consumer: IndexingRedisStreamsConsumer
) -> None:
    old = _another_lane(SLACK)
    client = provider.get_client()
    await client.hset(
        LANE_MAP,
        SLACK,
        f"v1|{stable_lane(SLACK, LANES)}|team|live|{old.rsplit('.', 1)[1]}|500|",
    )
    await client.xadd(old, {"value": "{}"}, id="1000-0")

    backlog = await consumer.lane_backlog(TOPIC)

    assert backlog.oldest_waiting_for({"connectorId": SLACK}) == 1000.0


async def test_a_retry_is_placed_by_the_router_not_sent_back_to_its_old_stream(
    consumer: IndexingRedisStreamsConsumer,
) -> None:
    """A moved connector's retries must follow it, or its old lane keeps
    receiving events after the move."""
    consumer.producer = AsyncMock()
    consumer._run_on_main_loop = lambda coro: coro  # type: ignore[method-assign]
    message = StreamMessage(eventType="newRecord", payload={"recordId": "r1", "connectorId": SLACK})

    await consumer._requeue_message(_lane(SLACK), message, "stable-1")

    assert consumer.producer.send_event.await_args.kwargs["topic"] == TOPIC
