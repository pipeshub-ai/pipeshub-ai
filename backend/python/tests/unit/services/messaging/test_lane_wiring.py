"""Lane wiring: the factory decision, consumer subscription, and the payoff.

These are the tests that catch a lane feature that is correct in isolation
but not actually connected to anything.
"""
from __future__ import annotations

import logging
from collections import Counter

import pytest

from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.lanes.hash_router import RedisLaneRouter
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.messaging_factory import (
    MessagingFactory,
    lane_config_from_env,
    lane_topics_for,
)
from tests.unit.services.messaging.test_lane_aware_producer import (
    _RecordingProducer,
)

_TOPIC = "record-events"


@pytest.fixture
def logger():
    return logging.getLogger("test_lane_wiring")


@pytest.fixture
def redis_config():
    return RedisStreamsConfig(host="localhost", port=6379)


class TestFactoryWrapsOnlyWhenLaned:
    def test_producer_is_unwrapped_when_laning_is_off(self, logger, redis_config):
        """A single lane means no routing decision to make, so the producer
        is handed back untouched and the publish path is unchanged."""
        producer = MessagingFactory.create_producer(
            logger,
            redis_config,
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(lane_count=1),
        )
        assert not isinstance(producer, LaneAwareProducer)

    def test_producer_is_wrapped_when_lanes_are_configured(
        self, logger, redis_config
    ):
        producer = MessagingFactory.create_producer(
            logger,
            redis_config,
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(lane_count=8),
        )
        assert isinstance(producer, LaneAwareProducer)

    def test_kafka_producer_is_wrapped_too(self, logger):
        producer = MessagingFactory.create_producer(
            logger,
            KafkaProducerConfig(bootstrap_servers=["b:9092"], client_id="p"),
            MessageBrokerType.KAFKA,
            lane_config=LaneConfig(lane_count=8),
        )
        assert isinstance(producer, LaneAwareProducer)

    def test_env_default_matches_the_shipped_config(self):
        """Pins the deployment default so a change to it is deliberate:
        laning decides which physical stream/partition a record lands on."""
        from app.services.messaging.config import messaging_env

        config = lane_config_from_env()
        assert config.lane_count == messaging_env.fair_scheduling_lane_count
        assert config.enabled == (config.lane_count > 1)


class TestConsumerSubscription:
    def test_subscribes_to_just_the_topic_when_laning_is_off(self, monkeypatch):
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "1")
        assert lane_topics_for(_TOPIC, MessageBrokerType.REDIS) == [_TOPIC]

    def test_redis_subscribes_to_every_lane_plus_the_legacy_stream(
        self, monkeypatch
    ):
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "4")
        topics = lane_topics_for(_TOPIC, MessageBrokerType.REDIS)
        assert topics == [_TOPIC] + [f"{_TOPIC}.{i}" for i in range(4)]

    def test_kafka_subscribes_to_one_topic_because_lanes_are_partitions(
        self, monkeypatch
    ):
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "4")
        assert lane_topics_for(_TOPIC, MessageBrokerType.KAFKA) == [_TOPIC]

    def test_non_laned_topics_are_never_expanded(self, monkeypatch):
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "4")
        assert lane_topics_for("entity-events", MessageBrokerType.REDIS) == [
            "entity-events"
        ]


class TestSegregatedBacklogIsSpreadAcrossLanes:
    """The payoff, at the routing level.

    Phase 0/1 left one limitation: when a large producer's whole backlog is
    published *before* a small producer's first record, no consumer-side
    reordering can reach the small one early, because the consumer cannot
    schedule what it has not read. Lanes fix that upstream -- the two
    producers' records are no longer in the same queue at all.
    """

    async def _publish_segregated(self, inner, lane_count):
        producer = LaneAwareProducer(
            logging.getLogger("test_lane_wiring"),
            inner,
            RedisLaneRouter(lane_count),
            LaneConfig(lane_count=lane_count),
        )
        for i in range(1000):
            await producer.send_event(
                topic=_TOPIC,
                event_type="newRecord",
                payload={"recordId": f"big-{i}", "connectorId": "user-a"},
            )
        for i in range(10):
            await producer.send_event(
                topic=_TOPIC,
                event_type="newRecord",
                payload={"recordId": f"small-{i}", "connectorId": "user-b"},
            )
        return producer

    async def test_the_two_users_do_not_share_a_stream(self):
        inner = _RecordingProducer()
        await self._publish_segregated(inner, lane_count=16)

        per_stream = Counter(topic for topic, _t, _p, _k in inner.events)
        assert len(per_stream) == 2, (
            "two connectors should occupy two lanes, not one queue"
        )

        streams = list(per_stream)
        big_stream = max(streams, key=lambda s: per_stream[s])
        small_stream = min(streams, key=lambda s: per_stream[s])
        assert per_stream[big_stream] == 1000
        assert per_stream[small_stream] == 10

        # The small user's first record is the first entry on its own stream,
        # so a consumer reading every lane reaches it immediately instead of
        # after the 1000 ahead of it.
        small_events = [e for e in inner.events if e[0] == small_stream]
        assert small_events[0][2]["recordId"] == "small-0"

    async def test_one_lane_reproduces_the_head_of_line_problem(self):
        """Control: with a single lane both users share one stream and the
        small one's first record really is 1000 entries deep."""
        inner = _RecordingProducer()
        await self._publish_segregated(inner, lane_count=1)

        assert len({topic for topic, _t, _p, _k in inner.events}) == 1
        first_small = next(
            i
            for i, (_t, _e, payload, _k) in enumerate(inner.events)
            if payload["connectorId"] == "user-b"
        )
        assert first_small == 1000
