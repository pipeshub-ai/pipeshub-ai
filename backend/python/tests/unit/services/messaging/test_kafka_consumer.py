"""
Tests for KafkaMessagingConsumer:
  - __init__
  - kafka_config_to_dict (plain, SSL, SASL)
  - __process_message (bytes, string, double-encoded JSON, invalid JSON, duplicates)
  - __is_message_processed / __mark_message_processed
  - is_running
  - initialize / cleanup / start / stop lifecycle
  - __cleanup_completed_tasks
"""

import asyncio
import json
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import StreamMessage
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_kafka_consumer")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["topic-a", "topic-b"],
        client_id="test-consumer",
        group_id="test-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker1:9092", "broker2:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def ssl_config():
    return KafkaConsumerConfig(
        topics=["topic-a"],
        client_id="ssl-consumer",
        group_id="ssl-group",
        auto_offset_reset="latest",
        enable_auto_commit=True,
        bootstrap_servers=["broker:9093"],
        ssl=True,
        sasl=None,
    )


@pytest.fixture
def sasl_config():
    return KafkaConsumerConfig(
        topics=["topic-a"],
        client_id="sasl-consumer",
        group_id="sasl-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9094"],
        ssl=True,
        sasl={
            "username": "user",
            "password": "pass",
            "mechanism": "SCRAM-SHA-256",
        },
    )


@pytest.fixture
def consumer(logger, plain_config):
    return KafkaMessagingConsumer(logger, plain_config)


def _make_message(topic="test-topic", partition=0, offset=0, value=None):
    """Helper to create a mock Kafka message."""
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    """Test constructor state."""

    def test_default_state(self, logger, plain_config):
        c = KafkaMessagingConsumer(logger, plain_config)
        assert c.consumer is None
        assert c.running is False
        assert c.kafka_config is plain_config
        assert c.processed_messages == {}
        assert c.consume_task is None
        assert c.message_handler is None
        assert c.retry_manager is None


# ===========================================================================
# kafka_config_to_dict
# ===========================================================================


class TestKafkaConfigToDict:
    """Static method converting KafkaConsumerConfig to aiokafka dict."""

    def test_plain_config(self, plain_config):
        result = KafkaMessagingConsumer.kafka_config_to_dict(plain_config)
        assert result["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert result["group_id"] == "test-group"
        assert result["auto_offset_reset"] == "earliest"
        assert result["enable_auto_commit"] is False
        assert result["client_id"] == "test-consumer"
        assert result["topics"] == ["topic-a", "topic-b"]
        assert "ssl_context" not in result
        assert "security_protocol" not in result

    def test_ssl_without_sasl(self, ssl_config):
        result = KafkaMessagingConsumer.kafka_config_to_dict(ssl_config)
        assert isinstance(result["ssl_context"], ssl.SSLContext)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_sasl_ssl_config(self, sasl_config):
        result = KafkaMessagingConsumer.kafka_config_to_dict(sasl_config)
        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-256"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"
        assert isinstance(result["ssl_context"], ssl.SSLContext)

    def test_sasl_default_mechanism(self):
        """Missing mechanism should default to SCRAM-SHA-512."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl={"username": "u", "password": "p"},
        )
        result = KafkaMessagingConsumer.kafka_config_to_dict(config)
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"

    def test_ssl_true_empty_sasl(self):
        """ssl=True with empty sasl dict -> SSL only."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl={},
        )
        result = KafkaMessagingConsumer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_sasl_none_with_ssl(self):
        """ssl=True with sasl=None -> SSL only."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl=None,
        )
        result = KafkaMessagingConsumer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result


# ===========================================================================
# __is_message_processed / __mark_message_processed
# ===========================================================================


class TestMessageTracking:
    """Test deduplication tracking helpers."""

    def test_new_message_not_processed(self, consumer):
        # Access name-mangled method
        result = consumer._KafkaMessagingConsumer__is_message_processed(
            "test-topic-0-42"
        )
        assert result is False

    def test_mark_then_check(self, consumer):
        consumer._KafkaMessagingConsumer__mark_message_processed("test-topic-0-42")
        assert (
            consumer._KafkaMessagingConsumer__is_message_processed("test-topic-0-42")
            is True
        )

    def test_different_offset_not_processed(self, consumer):
        consumer._KafkaMessagingConsumer__mark_message_processed("test-topic-0-42")
        assert (
            consumer._KafkaMessagingConsumer__is_message_processed("test-topic-0-99")
            is False
        )

    def test_different_partition_not_processed(self, consumer):
        consumer._KafkaMessagingConsumer__mark_message_processed("test-topic-0-42")
        assert (
            consumer._KafkaMessagingConsumer__is_message_processed("test-topic-1-42")
            is False
        )

    def test_multiple_messages_tracked(self, consumer):
        consumer._KafkaMessagingConsumer__mark_message_processed("t-0-1")
        consumer._KafkaMessagingConsumer__mark_message_processed("t-0-2")
        consumer._KafkaMessagingConsumer__mark_message_processed("t-1-1")

        assert consumer._KafkaMessagingConsumer__is_message_processed("t-0-1") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("t-0-2") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("t-1-1") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("t-1-2") is False


# ===========================================================================
# __process_message
# ===========================================================================


class TestProcessMessage:
    """Test message decoding, deduplication, and handler invocation."""

    @pytest.mark.asyncio
    async def test_valid_json_bytes(self, consumer):
        """Bytes message with valid JSON should be decoded and handled."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        msg = _make_message(
            value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"),
            offset=10,
        )

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result == (True, None)
        handler.assert_awaited_once()
        called_arg = handler.call_args[0][0]
        assert isinstance(called_arg, StreamMessage)
        assert called_arg.eventType == "test"

    @pytest.mark.asyncio
    async def test_valid_json_string(self, consumer):
        """String message with valid JSON should be parsed and handled."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        msg = _make_message(value='{"eventType": "test", "payload": {"key": "val"}}', offset=11)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result == (True, None)
        handler.assert_awaited_once()
        called_arg = handler.call_args[0][0]
        assert isinstance(called_arg, StreamMessage)

    @pytest.mark.asyncio
    async def test_double_encoded_json(self, consumer):
        """Double-encoded JSON string should be unwrapped twice."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        inner = json.dumps({"eventType": "test", "payload": {"nested": True}})
        double_encoded = json.dumps(inner)  # string wrapping JSON
        msg = _make_message(value=double_encoded.encode("utf-8"), offset=12)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result == (True, None)
        handler.assert_awaited_once()
        called_arg = handler.call_args[0][0]
        assert isinstance(called_arg, StreamMessage)
        assert called_arg.payload == {"nested": True}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self, consumer):
        """Invalid JSON should return False."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        msg = _make_message(value=b"not-valid-json{{", offset=13)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result[0] is False
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_message_skipped(self, consumer):
        """Already-processed messages should be skipped."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=14)

        result1 = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result1 == (True, None)

        consumer._KafkaMessagingConsumer__mark_message_processed("test-topic-0-14")

        result2 = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result2 == (True, None)
        assert handler.await_count == 1

    @pytest.mark.asyncio
    async def test_unexpected_value_type_returns_false(self, consumer):
        """Non-bytes, non-string value should return False."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        msg = _make_message(value=12345, offset=15)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_no_handler_returns_false(self, consumer):
        """When no message_handler is set, should return False."""
        consumer.message_handler = None

        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=16)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_handler_exception_returns_false(self, consumer):
        """When handler raises, should return False."""
        handler = AsyncMock(side_effect=Exception("handler boom"))
        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=17)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_unicode_decode_error(self, consumer):
        """Bytes that can't be decoded to UTF-8 should return False."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        # Invalid UTF-8 sequence
        msg = _make_message(value=b"\xff\xfe", offset=18)

        result = await consumer._KafkaMessagingConsumer__process_message(msg)
        assert result[0] is False

    @pytest.mark.asyncio
    async def test_message_not_marked_processed_on_handler_failure(self, consumer):
        """Handler failure does not mark message processed; commit loop does that."""
        handler = AsyncMock(side_effect=Exception("fail"))
        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"a": 1}}).encode("utf-8"), offset=19)
        result = await consumer._KafkaMessagingConsumer__process_message(msg)

        assert result[0] is False
        assert (
            consumer._KafkaMessagingConsumer__is_message_processed(
                "test-topic-0-19"
            )
            is False
        )


# ===========================================================================
# is_running
# ===========================================================================


class TestIsRunning:
    """Test is_running state check."""

    def test_initially_false(self, consumer):
        assert consumer.is_running() is False

    def test_true_after_setting(self, consumer):
        consumer.running = True
        assert consumer.is_running() is True

    def test_false_after_unsetting(self, consumer):
        consumer.running = True
        consumer.running = False
        assert consumer.is_running() is False


# ===========================================================================
# initialize
# ===========================================================================


class TestInitialize:
    """Test consumer initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_consumer(self, consumer):
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ) as MockCls:
            await consumer.initialize()

        # Should be called with unpacked topics as positional args
        MockCls.assert_called_once()
        args = MockCls.call_args
        # First two positional args should be the topics
        assert args[0] == ("topic-a", "topic-b")
        mock_aio.start.assert_awaited_once()
        assert consumer.consumer is mock_aio

    @pytest.mark.asyncio
    async def test_initialize_with_invalid_config_raises(self, logger):
        c = KafkaMessagingConsumer(logger, None)  # type: ignore
        with pytest.raises(ValueError, match="not valid"):
            await c.initialize()

    @pytest.mark.asyncio
    async def test_initialize_start_failure_raises(self, consumer):
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock(side_effect=Exception("start failed"))

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ):
            with pytest.raises(Exception, match="start failed"):
                await consumer.initialize()


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    """Test consumer cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_stops_consumer(self, consumer):
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio

        await consumer.cleanup()
        mock_aio.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_consumer(self, consumer):
        assert consumer.consumer is None
        await consumer.cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_handles_stop_exception(self, consumer):
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock(side_effect=Exception("stop error"))
        consumer.consumer = mock_aio

        # Should not raise
        await consumer.cleanup()


# ===========================================================================
# start
# ===========================================================================


class TestStart:
    """Test consumer start method."""

    @pytest.mark.asyncio
    async def test_start_sets_running_and_handler(self, consumer):
        handler = AsyncMock()
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()
        mock_aio.getmany = AsyncMock(return_value={})

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ):
            await consumer.start(handler)

        assert consumer.running is True
        assert consumer.message_handler is handler
        assert consumer.consume_task is not None

        # Clean up
        consumer.running = False
        consumer.consume_task.cancel()
        try:
            await consumer.consume_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_initializes_consumer_if_none(self, consumer):
        handler = AsyncMock()
        mock_aio = AsyncMock()
        mock_aio.start = AsyncMock()
        mock_aio.getmany = AsyncMock(return_value={})

        assert consumer.consumer is None

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer",
            return_value=mock_aio,
        ):
            await consumer.start(handler)

        assert consumer.consumer is mock_aio

        # Clean up
        consumer.running = False
        consumer.consume_task.cancel()
        try:
            await consumer.consume_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_with_existing_consumer(self, consumer):
        handler = AsyncMock()
        mock_aio = AsyncMock()
        mock_aio.getmany = AsyncMock(return_value={})
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio

        with patch(
            "app.services.messaging.kafka.consumer.consumer.AIOKafkaConsumer"
        ) as MockCls:
            await consumer.start(handler)
            # Should NOT create a new consumer
            MockCls.assert_not_called()

        # Clean up
        consumer.running = False
        consumer.consume_task.cancel()
        try:
            await consumer.consume_task
        except asyncio.CancelledError:
            pass


# ===========================================================================
# stop
# ===========================================================================


class TestStop:
    """Test consumer stop method."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, consumer):
        consumer.running = True
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio
        consumer.message_handler = None

        await consumer.stop()

        assert consumer.running is False
        mock_aio.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_call_handler(self, consumer):
        """stop() no longer calls handler with None."""
        handler = AsyncMock()
        consumer.message_handler = handler
        consumer.running = True
        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock()
        consumer.consumer = mock_aio

        await consumer.stop()

        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_cancels_consume_task(self, consumer):
        consumer.running = True
        consumer.message_handler = None

        mock_aio = AsyncMock()
        mock_aio.stop = AsyncMock()
        mock_aio.getmany = AsyncMock(return_value={})
        consumer.consumer = mock_aio

        # Create a dummy task
        async def dummy():
            while True:
                await asyncio.sleep(0.1)

        consumer.consume_task = asyncio.create_task(dummy())

        await consumer.stop()

        assert consumer.consume_task.cancelled() or consumer.consume_task.done()

    @pytest.mark.asyncio
    async def test_stop_without_consumer(self, consumer):
        """stop() with no consumer should still work."""
        consumer.running = True
        consumer.message_handler = None
        consumer.consumer = None

        await consumer.stop()
        assert consumer.running is False


# ===================================================================
# stop - various states
# ===================================================================

class TestStopExtended:

    @pytest.mark.asyncio
    async def test_stop_with_handler_and_task(self, consumer):
        """Stop cancels task; no longer calls handler with None."""
        handler = AsyncMock()
        consumer.message_handler = handler
        consumer.running = True
        consumer.consumer = AsyncMock()

        async def dummy():
            while True:
                await asyncio.sleep(0.1)

        consumer.consume_task = asyncio.create_task(dummy())

        await consumer.stop()

        handler.assert_not_awaited()
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_no_handler(self, consumer):
        """Stop works when no handler is set."""
        consumer.running = True
        consumer.message_handler = None
        consumer.consumer = AsyncMock()

        await consumer.stop()
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_no_consume_task(self, consumer):
        """Stop works when no consume task exists."""
        consumer.running = True
        consumer.message_handler = None
        consumer.consumer = AsyncMock()
        consumer.consume_task = None

        await consumer.stop()
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_no_consumer(self, consumer):
        """Stop works when consumer is None."""
        consumer.running = True
        consumer.message_handler = None
        consumer.consumer = None

        await consumer.stop()
        assert consumer.running is False


# ===================================================================
# start - edge cases
# ===================================================================

class TestStartExtended:

    @pytest.mark.asyncio
    async def test_start_exception_propagated(self, logger):
        """Exception during start is propagated."""
        c = KafkaMessagingConsumer(logger, None)
        handler = AsyncMock()
        with pytest.raises(ValueError):
            await c.start(handler)
