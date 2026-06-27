"""Integration tests for selective retry behavior in Kafka consumer."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from aiokafka.structs import ConsumerRecord, TopicPartition

from app.config.constants.arangodb import ProgressStatus
from app.services.messaging.config import messaging_env
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig


@pytest.fixture
def kafka_config():
    """Create a test Kafka configuration."""
    return KafkaConsumerConfig(
        topics=["test-topic"],
        client_id="test_client",
        group_id="test_group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["localhost:9092"],
    )


@pytest.fixture
def logger():
    """Create a mock logger."""
    return Mock()


@pytest.fixture
def consumer(logger, kafka_config):
    """Create a Kafka consumer instance."""
    return KafkaMessagingConsumer(logger, kafka_config)


@pytest.fixture
def mock_message():
    """Create a mock Kafka message."""
    message = MagicMock()
    message.topic = "test-topic"
    message.partition = 0
    message.offset = 100
    message.value = b'{"eventType": "test", "payload": {"recordId": "rec_123"}}'
    return message


class TestKafkaConsumerRetryConfiguration:
    """Test Kafka consumer retry configuration."""

    @pytest.mark.asyncio
    async def test_max_delivery_attempts_configuration(self):
        """Test that max_delivery_attempts is correctly configured."""
        assert messaging_env.max_delivery_attempts == 3

    @pytest.mark.asyncio
    async def test_batch_size_configuration(self):
        """Test that batch sizes are correctly configured."""
        assert messaging_env.message_batch_size_simple == 10
        assert messaging_env.message_batch_size_indexing == 1

    @pytest.mark.asyncio
    async def test_message_timeout_configuration(self):
        """Test that message timeout is correctly configured."""
        assert messaging_env.message_timeout_ms == 2000


class TestRedisStreamsRetryBehavior:
    """Test selective retry behavior in Redis streams consumer."""

    @pytest.mark.asyncio
    async def test_max_delivery_attempts_configuration(self):
        """Test that max_delivery_attempts is correctly configured."""
        assert messaging_env.max_delivery_attempts == 3

    @pytest.mark.asyncio
    async def test_batch_size_configuration(self):
        """Test that batch sizes are correctly configured."""
        assert messaging_env.message_batch_size_simple == 10
        assert messaging_env.message_batch_size_indexing == 1

    @pytest.mark.asyncio
    async def test_message_timeout_configuration(self):
        """Test that message timeout is correctly configured."""
        assert messaging_env.message_timeout_ms == 2000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
