"""
Tests for MessagingFactory with Redis Streams broker type:
  - create_producer with redis broker
  - create_consumer with redis broker (simple and indexing)
  - type validation for Redis configs
"""

import logging
from unittest.mock import patch

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.messaging_factory import MessagingFactory


@pytest.fixture
def logger():
    return logging.getLogger("test_messaging_factory_redis")


@pytest.fixture
def redis_config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        client_id="test-client",
        group_id="test-group",
        topics=["test-topic"],
    )


@pytest.fixture
def kafka_producer_config():
    return KafkaProducerConfig(
        bootstrap_servers=["localhost:9092"],
        client_id="test-producer",
    )


@pytest.fixture
def kafka_consumer_config():
    return KafkaConsumerConfig(
        topics=["test-topic"],
        client_id="test-consumer",
        group_id="test-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["localhost:9092"],
    )


class TestCreateProducerRedis:
    def test_redis_broker_returns_redis_producer(self, logger, redis_config):
        from app.services.messaging.redis_streams.producer import RedisStreamsProducer

        producer = MessagingFactory.create_producer(
            logger, config=redis_config, broker_type="redis"
        )
        assert isinstance(producer, RedisStreamsProducer)

    def test_redis_broker_case_insensitive(self, logger, redis_config):
        from app.services.messaging.redis_streams.producer import RedisStreamsProducer

        producer = MessagingFactory.create_producer(
            logger, config=redis_config, broker_type="Redis"
        )
        assert isinstance(producer, RedisStreamsProducer)

    def test_none_config_raises_value_error(self, logger):
        with pytest.raises(ValueError, match="Redis Streams config is required"):
            MessagingFactory.create_producer(logger, config=None, broker_type="redis")

    def test_wrong_config_type_raises_type_error(self, logger, kafka_producer_config):
        with pytest.raises(TypeError, match="Expected RedisStreamsConfig"):
            MessagingFactory.create_producer(
                logger, config=kafka_producer_config, broker_type="redis"
            )

    def test_auto_detect_broker_type_redis(self, logger, redis_config):
        from app.services.messaging.redis_streams.producer import RedisStreamsProducer

        with patch.dict("os.environ", {"MESSAGE_BROKER": "redis"}):
            producer = MessagingFactory.create_producer(logger, config=redis_config)
            assert isinstance(producer, RedisStreamsProducer)


class TestCreateConsumerRedis:
    def test_redis_simple_consumer(self, logger, redis_config):
        from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

        consumer = MessagingFactory.create_consumer(
            logger, config=redis_config, broker_type="redis", consumer_type="simple"
        )
        assert isinstance(consumer, RedisStreamsConsumer)

    def test_redis_indexing_consumer(self, logger, redis_config):
        from app.services.messaging.redis_streams.indexing_consumer import (
            IndexingRedisStreamsConsumer,
        )

        consumer = MessagingFactory.create_consumer(
            logger, config=redis_config, broker_type="redis", consumer_type="indexing"
        )
        assert isinstance(consumer, IndexingRedisStreamsConsumer)

    def test_redis_default_consumer_type_is_simple(self, logger, redis_config):
        from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

        consumer = MessagingFactory.create_consumer(
            logger, config=redis_config, broker_type="redis"
        )
        assert isinstance(consumer, RedisStreamsConsumer)

    def test_none_config_raises_value_error(self, logger):
        with pytest.raises(ValueError, match="Redis Streams config is required"):
            MessagingFactory.create_consumer(logger, config=None, broker_type="redis")

    def test_wrong_config_type_raises_type_error(self, logger, kafka_consumer_config):
        with pytest.raises(TypeError, match="Expected RedisStreamsConfig"):
            MessagingFactory.create_consumer(
                logger, config=kafka_consumer_config, broker_type="redis"
            )

    def test_redis_broker_case_insensitive(self, logger, redis_config):
        from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

        consumer = MessagingFactory.create_consumer(
            logger, config=redis_config, broker_type="REDIS"
        )
        assert isinstance(consumer, RedisStreamsConsumer)

    def test_unknown_consumer_type_defaults_to_simple(self, logger, redis_config):
        from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

        consumer = MessagingFactory.create_consumer(
            logger,
            config=redis_config,
            broker_type="redis",
            consumer_type="unknown",
        )
        assert isinstance(consumer, RedisStreamsConsumer)
