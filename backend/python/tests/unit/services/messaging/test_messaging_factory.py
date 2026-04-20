"""
Tests for MessagingFactory: create_producer and create_consumer.
"""

import logging
import os
from unittest.mock import patch

import pytest

from app.services.messaging.config import ConsumerType, MessageBrokerType
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.messaging_factory import MessagingFactory


@pytest.fixture
def logger():
    return logging.getLogger("test_messaging_factory")


@pytest.fixture
def producer_config():
    return KafkaProducerConfig(
        bootstrap_servers=["localhost:9092"],
        client_id="test-producer",
    )


@pytest.fixture
def consumer_config():
    return KafkaConsumerConfig(
        topics=["test-topic"],
        client_id="test-consumer",
        group_id="test-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["localhost:9092"],
    )


# ===========================================================================
# create_producer
# ===========================================================================


class TestCreateProducer:
    """MessagingFactory.create_producer()"""

    def test_kafka_broker_returns_kafka_producer(self, logger, producer_config):
        producer = MessagingFactory.create_producer(
            logger, config=producer_config, broker_type=MessageBrokerType.KAFKA
        )
        from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
        assert isinstance(producer, KafkaMessagingProducer)

    def test_none_config_raises_value_error(self, logger):
        with pytest.raises(ValueError, match="Kafka producer config is required"):
            MessagingFactory.create_producer(
                logger, config=None, broker_type=MessageBrokerType.KAFKA
            )

    def test_default_broker_is_kafka(self, logger, producer_config):
        """When broker_type is omitted and MESSAGE_BROKER=kafka, defaults to kafka."""
        from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
        with patch.dict(os.environ, {"MESSAGE_BROKER": "kafka"}):
            producer = MessagingFactory.create_producer(logger, config=producer_config)
        assert isinstance(producer, KafkaMessagingProducer)


# ===========================================================================
# create_consumer
# ===========================================================================


class TestCreateConsumer:
    """MessagingFactory.create_consumer()"""

    def test_kafka_simple_consumer(self, logger, consumer_config):
        from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
        consumer = MessagingFactory.create_consumer(
            logger,
            config=consumer_config,
            broker_type=MessageBrokerType.KAFKA,
            consumer_type=ConsumerType.SIMPLE,
        )
        assert isinstance(consumer, KafkaMessagingConsumer)

    def test_kafka_indexing_consumer(self, logger, consumer_config):
        from app.services.messaging.kafka.consumer.indexing_consumer import IndexingKafkaConsumer
        consumer = MessagingFactory.create_consumer(
            logger,
            config=consumer_config,
            broker_type=MessageBrokerType.KAFKA,
            consumer_type=ConsumerType.INDEXING,
        )
        assert isinstance(consumer, IndexingKafkaConsumer)

    def test_default_consumer_type_is_simple(self, logger, consumer_config):
        from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
        consumer = MessagingFactory.create_consumer(
            logger, config=consumer_config, broker_type=MessageBrokerType.KAFKA
        )
        assert isinstance(consumer, KafkaMessagingConsumer)

