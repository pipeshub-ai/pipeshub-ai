"""Tests for exception classification integration in consumers."""
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from app.services.messaging.error_classifier import MessageErrorType


class TestExceptionClassificationInConsumers:
    """Test that consumers use classify_by_exception for retry logic."""

    @pytest.mark.asyncio
    async def test_kafka_simple_consumer_terminal_error_logged(self):
        """Test that terminal errors return failure tuple from process_message."""
        from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
        from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

        config = KafkaConsumerConfig(
            topics=["test-topic"],
            client_id="test",
            group_id="test",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["localhost:9092"],
        )
        logger = Mock()
        consumer = KafkaMessagingConsumer(logger, config)

        async def handler_with_terminal_error(msg):
            raise ValueError("Invalid value")

        consumer.message_handler = handler_with_terminal_error

        message = MagicMock()
        message.value = b'{"eventType": "test", "payload": {}}'
        message.topic = "test-topic"
        message.partition = 0
        message.offset = 100

        success, exc = await consumer._KafkaMessagingConsumer__process_message(message)

        assert success is False
        assert isinstance(exc, ValueError)

    @pytest.mark.asyncio
    async def test_kafka_simple_consumer_transient_error_logged(self):
        """Test that transient errors return failure tuple from process_message."""
        from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
        from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

        config = KafkaConsumerConfig(
            topics=["test-topic"],
            client_id="test",
            group_id="test",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["localhost:9092"],
        )
        logger = Mock()
        consumer = KafkaMessagingConsumer(logger, config)

        async def handler_with_transient_error(msg):
            raise TimeoutError("Network timeout")

        consumer.message_handler = handler_with_transient_error

        message = MagicMock()
        message.value = b'{"eventType": "test", "payload": {}}'
        message.topic = "test-topic"
        message.partition = 0
        message.offset = 100

        success, exc = await consumer._KafkaMessagingConsumer__process_message(message)

        assert success is False
        assert isinstance(exc, TimeoutError)

    @pytest.mark.asyncio
    async def test_error_classifier_classify_by_exception_called(self):
        """Test that classify_by_exception is called during consume_loop error handling."""
        from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
        from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig

        config = KafkaConsumerConfig(
            topics=["test-topic"],
            client_id="test",
            group_id="test",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["localhost:9092"],
        )
        logger = Mock()
        consumer = KafkaMessagingConsumer(logger, config)

        async def handler_with_error(msg):
            raise ValueError("Invalid value")

        consumer.message_handler = handler_with_error

        message = MagicMock()
        message.value = b'{"eventType": "test", "payload": {}}'
        message.topic = "test-topic"
        message.partition = 0
        message.offset = 100

        tp = MagicMock()
        tp.topic = "test-topic"
        tp.partition = 0

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {tp: [message]}
            consumer.running = False
            return {}

        mock_aio = AsyncMock()
        mock_aio.getmany = mock_getmany
        mock_aio.commit = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.running = True

        with patch(
            "app.services.messaging.kafka.consumer.consumer.MessageErrorClassifier.classify_by_exception"
        ) as mock_classify:
            mock_classify.return_value = MessageErrorType.TERMINAL

            await consumer._KafkaMessagingConsumer__consume_loop()

            assert mock_classify.called
            called_exception = mock_classify.call_args[0][0]
            assert isinstance(called_exception, ValueError)

    @pytest.mark.asyncio
    async def test_redis_consumer_exception_classification(self):
        """Test that Redis consumer classifies handler exceptions."""
        from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
        from app.services.messaging.config import RedisStreamsConfig

        config = RedisStreamsConfig(
            host="localhost",
            port=6379,
            topics=["test-topic"],
            client_id="test",
            group_id="test",
        )
        logger = Mock()
        consumer = RedisStreamsConsumer(logger, config)

        async def handler_with_terminal_error(msg):
            raise json.JSONDecodeError("Bad JSON", "", 0)

        consumer.message_handler = handler_with_terminal_error

        fields = {"value": '{"eventType": "test", "payload": {}}'}

        with patch(
            "app.services.messaging.redis_streams.consumer.MessageErrorClassifier.classify_by_exception",
            return_value=MessageErrorType.TERMINAL,
        ):
            success, is_terminal = await consumer._process_message_with_classification(
                "test-topic", "1-0", fields
            )

        assert success is False
        assert is_terminal is True

        warning_calls = [
            call for call in logger.warning.call_args_list
            if "Terminal error" in str(call)
        ]
        assert len(warning_calls) > 0, "Terminal error should be logged"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
