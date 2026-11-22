"""``MessagingFactory.create_consumer`` hands the precision filter to the indexing consumer of every broker."""

import logging
from unittest.mock import MagicMock

import pytest

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.messaging_factory import MessagingFactory

log = logging.getLogger("precision-factory")


def _redis_config() -> RedisStreamsConfig:
    return RedisStreamsConfig(topics=["record-events"], group_id="g", client_id="c")


def _kafka_config() -> KafkaConsumerConfig:
    return KafkaConsumerConfig(
        topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None,
    )


@pytest.mark.parametrize(
    ("broker", "config"),
    [
        (MessageBrokerType.POSTGRES, _redis_config),
        (MessageBrokerType.REDIS, _redis_config),
        (MessageBrokerType.KAFKA, _kafka_config),
    ],
    ids=["postgres", "redis", "kafka"],
)
def test_the_indexing_consumer_of_every_broker_gets_the_filter(broker, config) -> None:
    hand_back = MagicMock()
    consumer = MessagingFactory.create_consumer(
        broker_type=broker, logger=log, config=config(), consumer_type=ConsumerType.INDEXING, precision_filter=hand_back,
    )
    assert consumer.precision_filter is hand_back


@pytest.mark.parametrize("broker", [MessageBrokerType.POSTGRES, MessageBrokerType.REDIS])
def test_a_consumer_that_is_not_an_indexing_one_gets_none(broker) -> None:
    consumer = MessagingFactory.create_consumer(
        broker_type=broker, logger=log, config=_redis_config(), consumer_type=ConsumerType.SIMPLE, precision_filter=MagicMock(),
    )
    assert getattr(consumer, "precision_filter", None) is None
