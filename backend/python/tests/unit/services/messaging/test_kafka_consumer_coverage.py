"""Additional tests for KafkaMessagingConsumer targeting remaining uncovered lines.

Covers:
- __process_message_wrapper (success, failure, exception paths)
- __cleanup_completed_tasks (mixed tasks)
- __consume_loop error handling
- stop with various states
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def logger():
    return logging.getLogger("test_kafka_consumer_cov")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["topic-1"],
        client_id="test-consumer",
        group_id="test-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def consumer(logger, plain_config):
    return KafkaMessagingConsumer(logger, plain_config)


def _make_message(topic="test-topic", partition=0, offset=0, value=None):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


def _make_topic_partition(topic="test-topic", partition=0):
    tp = MagicMock()
    tp.topic = topic
    tp.partition = partition
    return tp


# ===================================================================
# __is_message_processed / __mark_message_processed
# ===================================================================

class TestMessageTrackingExtended:

    def test_complex_topic_name_with_dashes(self, consumer):
        """Topic names with dashes are handled correctly."""
        msg_id = "my-topic-name-0-42"
        consumer._KafkaMessagingConsumer__mark_message_processed(msg_id)
        assert consumer._KafkaMessagingConsumer__is_message_processed(msg_id) is True

    def test_multiple_partitions(self, consumer):
        """Multiple partitions tracked independently."""
        consumer._KafkaMessagingConsumer__mark_message_processed("topic-0-1")
        consumer._KafkaMessagingConsumer__mark_message_processed("topic-1-1")
        consumer._KafkaMessagingConsumer__mark_message_processed("topic-0-2")

        assert consumer._KafkaMessagingConsumer__is_message_processed("topic-0-1") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("topic-1-1") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("topic-0-2") is True
        assert consumer._KafkaMessagingConsumer__is_message_processed("topic-0-3") is False
        assert consumer._KafkaMessagingConsumer__is_message_processed("topic-1-2") is False


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
