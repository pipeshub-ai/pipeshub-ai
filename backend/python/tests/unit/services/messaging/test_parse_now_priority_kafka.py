"""The priority lane on Kafka: the consumer of ``record-events-priority`` shares its caps, admission and
backpressure rules with the Redis Streams one (``consumer_concurrency``); these check that the Kafka
consumer is wired to them."""

from __future__ import annotations

import logging

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import ConsumerType, MessageBrokerType, Topic
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.messaging_factory import MessagingFactory, lane_topics_for
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

PRIORITY = Topic.RECORD_EVENTS_PRIORITY.value
LANE = concurrency.PriorityLane(heavy_limit=1, light_limit=3)


def _config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=[PRIORITY], client_id="records_priority_consumer_client", group_id="records_priority_consumer_group",
        auto_offset_reset="earliest", enable_auto_commit=False, bootstrap_servers=["b:9092"],
    )


def _consumer(lane: concurrency.PriorityLane | None = LANE, **kwargs: object) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(
        logging.getLogger("t"), _config(), priority_lane=lane, governor=make_test_governor(),
        fair_scheduler_config=FairSchedulerConfig(enabled=True, parallel_partitions=True), **kwargs,  # type: ignore[arg-type]
    )


def test_the_consumer_carries_its_lane() -> None:
    assert _consumer().priority_lane is LANE
    assert _consumer(None).priority_lane is None


def test_a_consumer_is_a_stage_or_the_lane_not_both() -> None:
    with pytest.raises(ValueError, match="not both"):
        IndexingKafkaConsumer(
            logging.getLogger("t"), _config(), stage_admission=concurrency.StageAdmission(stage="classify", limit=2),
            priority_lane=LANE,
        )


def test_the_caps_bound_the_records_in_flight() -> None:
    # P2, P3
    consumer = _consumer()
    heavy = concurrency.GateWaiterToken(consumer, ParseTier.HEAVY)
    heavy.admit()

    budget = consumer._dispatch_budget()
    assert not budget.allows(ParseTier.HEAVY)
    assert budget.allows(ParseTier.LIGHT)
    assert concurrency.pending_task_ceiling(consumer) == 4


def test_full_index_pools_do_not_stop_the_lane() -> None:
    assert concurrency.index_gates_saturated(_consumer()) is False


def test_the_topic_is_one_topic_whatever_the_lane_count(monkeypatch) -> None:
    # P6: Kafka partitions are the lanes of record-events; this topic is not laned.
    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "8")

    assert lane_topics_for(PRIORITY, MessageBrokerType.KAFKA) == [PRIORITY]


def test_the_factory_gives_the_lane_consumer_the_fair_scheduler() -> None:
    consumer = MessagingFactory.create_consumer(
        logging.getLogger("t"),
        config=_config(),
        broker_type=MessageBrokerType.KAFKA,
        consumer_type=ConsumerType.INDEXING,
        fair_scheduler_config=FairSchedulerConfig(enabled=False),
        priority_lane=LANE,
    )

    assert consumer.priority_lane is LANE
    assert consumer.fair_scheduler_config.enabled is True
    assert consumer.fair_scheduler_config.parallel_partitions is True
