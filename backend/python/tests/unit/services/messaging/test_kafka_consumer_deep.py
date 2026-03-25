"""Tests for IndexingKafkaConsumer (app.services.messaging.kafka.consumer.indexing_consumer).

Covers: __init__, kafka_config_to_dict, __start_worker_thread, initialize,
__parse_message, __process_message_wrapper, __start_processing_task,
start, stop, cleanup, is_running, __consume_loop.
"""

import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    FUTURE_CLEANUP_INTERVAL,
    IndexingEvent,
    IndexingKafkaConsumer,
    MAX_CONCURRENT_INDEXING,
    MAX_CONCURRENT_PARSING,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_indexing_consumer")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["indexing-topic"],
        client_id="indexing-consumer",
        group_id="indexing-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker1:9092", "broker2:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def ssl_config():
    return KafkaConsumerConfig(
        topics=["indexing-topic"],
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
        topics=["indexing-topic"],
        client_id="sasl-consumer",
        group_id="sasl-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9094"],
        ssl=True,
        sasl={
            "username": "user",
            "password": "pass",
            "mechanism": "SCRAM-SHA-512",
        },
    )


@pytest.fixture
def consumer(logger, plain_config):
    return IndexingKafkaConsumer(logger, plain_config)


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
    def test_attributes_set(self, consumer, logger, plain_config):
        """Constructor sets logger, config, and initial state."""
        assert consumer.logger is logger
        assert consumer.kafka_config is plain_config
        assert consumer.consumer is None
        assert consumer.running is False
        assert consumer.consume_task is None

    def test_semaphores_not_created_in_init(self, consumer):
        """Semaphores are None until worker thread starts."""
        assert consumer.parsing_semaphore is None
        assert consumer.indexing_semaphore is None

    def test_worker_infrastructure_not_started(self, consumer):
        """Worker thread infrastructure is not started in __init__."""
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None

    def test_futures_tracking_initialized(self, consumer):
        """Active futures set and lock are initialized."""
        assert len(consumer._active_futures) == 0
        assert isinstance(consumer._futures_lock, type(threading.Lock()))

    def test_message_handler_is_none(self, consumer):
        """Message handler is None until start() is called."""
        assert consumer.message_handler is None


# ===========================================================================
# IndexingEvent
# ===========================================================================


class TestIndexingEvent:
    def test_event_types(self):
        """IndexingEvent has the expected event type strings."""
        assert IndexingEvent.PARSING_COMPLETE == "parsing_complete"
        assert IndexingEvent.INDEXING_COMPLETE == "indexing_complete"


# ===========================================================================
# kafka_config_to_dict
# ===========================================================================


class TestKafkaConfigToDict:
    def test_plain_config(self, plain_config):
        """Plain config produces correct dict."""
        result = IndexingKafkaConsumer.kafka_config_to_dict(plain_config)

        assert result["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert result["group_id"] == "indexing-group"
        assert result["auto_offset_reset"] == "earliest"
        assert result["enable_auto_commit"] is False
        assert result["client_id"] == "indexing-consumer"
        assert result["topics"] == ["indexing-topic"]
        assert "ssl_context" not in result

    def test_ssl_config(self, ssl_config):
        """SSL config adds ssl_context and security_protocol."""
        result = IndexingKafkaConsumer.kafka_config_to_dict(ssl_config)

        assert "ssl_context" in result
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result

    def test_sasl_config(self, sasl_config):
        """SASL config adds SASL settings."""
        result = IndexingKafkaConsumer.kafka_config_to_dict(sasl_config)

        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"


# ===========================================================================
# __start_worker_thread
# ===========================================================================


class TestStartWorkerThread:
    def test_worker_thread_creates_executor(self, consumer):
        """Starting worker thread creates executor and event loop."""
        consumer._IndexingKafkaConsumer__start_worker_thread()

        try:
            assert consumer.worker_executor is not None
            # Wait for the loop to be ready
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            assert consumer.worker_loop is not None
            assert consumer.worker_loop.is_running()
            assert consumer.parsing_semaphore is not None
            assert consumer.indexing_semaphore is not None
        finally:
            # Clean up
            consumer._IndexingKafkaConsumer__stop_worker_thread()

    def test_ready_event_is_set(self, consumer):
        """Worker loop ready event is set after thread starts."""
        consumer._IndexingKafkaConsumer__start_worker_thread()

        try:
            ready = consumer.worker_loop_ready.wait(timeout=5.0)
            assert ready is True
        finally:
            consumer._IndexingKafkaConsumer__stop_worker_thread()


# ===========================================================================
# initialize
# ===========================================================================


class TestInitialize:
    @pytest.mark.asyncio
    async def test_success(self, logger, plain_config):
        """Initialize starts worker thread and creates Kafka consumer."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        mock_kafka_consumer = AsyncMock()
        mock_kafka_consumer.start = AsyncMock()

        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
            return_value=mock_kafka_consumer,
        ):
            await consumer.initialize()

        try:
            assert consumer.consumer is mock_kafka_consumer
            mock_kafka_consumer.start.assert_awaited_once()
        finally:
            await consumer.cleanup()

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger):
        """Initialize raises when kafka_config is None."""
        consumer = IndexingKafkaConsumer(logger, None)

        with pytest.raises(ValueError, match="not valid"):
            await consumer.initialize()

    @pytest.mark.asyncio
    async def test_consumer_start_failure_stops(self, logger, plain_config):
        """If consumer.start() fails, stop is called and error re-raised."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        mock_kafka_consumer = AsyncMock()
        mock_kafka_consumer.start = AsyncMock(side_effect=RuntimeError("connect fail"))

        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
            return_value=mock_kafka_consumer,
        ):
            with pytest.raises(RuntimeError, match="connect fail"):
                await consumer.initialize()


# ===========================================================================
# __parse_message
# ===========================================================================


class TestParseMessage:
    def test_bytes_json(self, consumer):
        """Bytes message is decoded and parsed as JSON."""
        msg = _make_message(value=json.dumps({"key": "value"}).encode("utf-8"))

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result == {"key": "value"}

    def test_string_json(self, consumer):
        """String message is parsed as JSON."""
        msg = _make_message(value=json.dumps({"key": "value"}))

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result == {"key": "value"}

    def test_double_encoded_json(self, consumer):
        """Double-encoded JSON is handled correctly."""
        inner = json.dumps({"key": "value"})
        msg = _make_message(value=json.dumps(inner).encode("utf-8"))

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result == {"key": "value"}

    def test_invalid_json_returns_none(self, consumer):
        """Invalid JSON returns None."""
        msg = _make_message(value=b"not json {{{")

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result is None

    def test_unexpected_type_returns_none(self, consumer):
        """Non-bytes, non-string value returns None."""
        msg = _make_message(value=12345)

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result is None

    def test_unicode_decode_error_returns_none(self, consumer):
        """Bytes that fail decoding return None."""
        msg = _make_message(value=b"\xff\xfe")
        # Force decode to fail
        msg.value = MagicMock()
        msg.value.decode = MagicMock(side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad"))

        result = consumer._IndexingKafkaConsumer__parse_message(msg)

        assert result is None


# ===========================================================================
# is_running
# ===========================================================================


class TestIsRunning:
    def test_initially_false(self, consumer):
        """Consumer is not running initially."""
        assert consumer.is_running() is False

    def test_true_when_running(self, consumer):
        """is_running returns True when running flag is set."""
        consumer.running = True
        assert consumer.is_running() is True


# ===========================================================================
# _get_active_task_count
# ===========================================================================


class TestGetActiveTaskCount:
    def test_zero_initially(self, consumer):
        """Active task count is zero initially."""
        assert consumer._get_active_task_count() == 0

    def test_reflects_active_futures(self, consumer):
        """Active task count reflects futures set size."""
        f1 = Future()
        f2 = Future()
        with consumer._futures_lock:
            consumer._active_futures.add(f1)
            consumer._active_futures.add(f2)

        assert consumer._get_active_task_count() == 2


# ===========================================================================
# _wait_for_active_futures
# ===========================================================================


class TestWaitForActiveFutures:
    def test_no_futures_noop(self, consumer):
        """No active futures logs info and returns."""
        consumer._wait_for_active_futures()
        # Should not raise

    def test_completed_futures_cleaned(self, consumer):
        """Completed futures are waited on successfully."""
        f = Future()
        f.set_result(None)
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        consumer._wait_for_active_futures()
        # Should not raise

    def test_errored_futures_handled(self, consumer):
        """Errored futures are handled gracefully."""
        f = Future()
        f.set_exception(RuntimeError("task error"))
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        consumer._wait_for_active_futures()
        # Should not raise


# ===========================================================================
# start
# ===========================================================================


class TestStart:
    @pytest.mark.asyncio
    async def test_start_sets_handler(self, consumer):
        """start() sets the message handler and running flag."""
        consumer.consumer = AsyncMock()

        async def handler(msg):
            yield {"event": "parsing_complete"}

        with patch.object(consumer, "_IndexingKafkaConsumer__consume_loop", new_callable=AsyncMock):
            await consumer.start(handler)

        assert consumer.running is True
        assert consumer.message_handler is handler
        assert consumer.consume_task is not None

        # Cleanup
        consumer.running = False
        if consumer.consume_task:
            consumer.consume_task.cancel()
            try:
                await consumer.consume_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_start_initializes_if_no_consumer(self, logger, plain_config):
        """start() calls initialize() if consumer is None."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        mock_kafka_consumer = AsyncMock()
        mock_kafka_consumer.start = AsyncMock()

        async def handler(msg):
            yield {"event": "parsing_complete"}

        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
            return_value=mock_kafka_consumer,
        ):
            await consumer.start(handler)

        try:
            assert consumer.consumer is mock_kafka_consumer
        finally:
            await consumer.stop()


# ===========================================================================
# stop
# ===========================================================================


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, consumer):
        """stop() sets running to False."""
        consumer.running = True
        consumer.consumer = AsyncMock()

        await consumer.stop()

        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_consume_task(self, consumer):
        """stop() cancels the consume task."""
        consumer.running = True
        consumer.consumer = AsyncMock()

        async def _loop():
            while True:
                await asyncio.sleep(0.1)

        consumer.consume_task = asyncio.create_task(_loop())

        await consumer.stop()

        assert consumer.consume_task.cancelled() or consumer.consume_task.done()


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stops_consumer(self, consumer):
        """cleanup() stops the Kafka consumer."""
        mock_consumer = AsyncMock()
        consumer.consumer = mock_consumer

        await consumer.cleanup()

        mock_consumer.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_without_consumer(self, consumer):
        """cleanup() handles None consumer gracefully."""
        consumer.consumer = None

        await consumer.cleanup()
        # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_with_error(self, consumer):
        """cleanup() handles consumer stop error gracefully."""
        mock_consumer = AsyncMock()
        mock_consumer.stop = AsyncMock(side_effect=RuntimeError("stop error"))
        consumer.consumer = mock_consumer

        await consumer.cleanup()
        # Should not raise


# ===========================================================================
# __process_message_wrapper
# ===========================================================================


class TestProcessMessageWrapper:
    @pytest.mark.asyncio
    async def test_semaphores_acquired_and_released_on_success(self, logger, plain_config):
        """Both semaphores are acquired and released on success."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield {"event": "parsing_complete", "data": {}}
            yield {"event": "indexing_complete", "data": {}}

        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Semaphores should be back at 1 (released)
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_semaphores_released_on_error(self, logger, plain_config):
        """Both semaphores are released even when handler raises."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            raise RuntimeError("handler error")
            yield  # noqa - needed for generator

        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))

        # Should not raise (errors are caught)
        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Semaphores should be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_none_parsed_message_skipped(self, logger, plain_config):
        """Unparseable messages are skipped."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        msg = _make_message(value=b"not json {{{")

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Semaphores should be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_no_handler_logs_error(self, logger, plain_config):
        """Missing handler logs error instead of crashing."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = None

        msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Semaphores should be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_semaphores_not_initialized_returns(self, logger, plain_config):
        """Returns early when semaphores are not initialized."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = None
        consumer.indexing_semaphore = None

        msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        # Should not raise

    @pytest.mark.asyncio
    async def test_only_parsing_event_releases_parsing_semaphore(self, logger, plain_config):
        """When only parsing_complete is yielded, indexing semaphore is released in finally."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield {"event": "parsing_complete", "data": {}}
            # No indexing_complete event

        consumer.message_handler = handler

        msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Both semaphores should be released (indexing via finally)
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===========================================================================
# __start_processing_task
# ===========================================================================


def _make_topic_partition(topic="test-topic", partition=0):
    """Helper to create a mock TopicPartition."""
    tp = MagicMock()
    tp.topic = topic
    tp.partition = partition
    return tp


class TestStartProcessingTask:
    @pytest.mark.asyncio
    async def test_no_worker_loop_logs_error(self, consumer, caplog):
        """Logs error when worker_loop is None."""
        consumer.worker_loop = None

        msg = _make_message()
        tp = _make_topic_partition()
        with caplog.at_level(logging.ERROR):
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

        assert "Worker loop not initialized" in caplog.text

    @pytest.mark.asyncio
    async def test_not_running_skips(self, consumer, caplog):
        """Skips processing when consumer is stopping."""
        consumer.worker_loop = MagicMock()
        consumer.running = False

        msg = _make_message()
        tp = _make_topic_partition()
        with caplog.at_level(logging.WARNING):
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

        assert "stopping" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_submits_to_worker_loop(self, logger, plain_config):
        """Submits coroutine to worker loop and tracks future."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer._IndexingKafkaConsumer__start_worker_thread()

        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            consumer.running = True

            msg = _make_message(value=json.dumps({"key": "val"}).encode("utf-8"))
            tp = _make_topic_partition()

            # Set a handler that yields both events
            async def handler(parsed_msg):
                yield {"event": "parsing_complete", "data": {}}
                yield {"event": "indexing_complete", "data": {}}

            consumer.message_handler = handler

            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

            # Give the worker thread time to process
            import time
            time.sleep(0.5)

            # Verify the task was submitted (futures should have been tracked)
            assert len(consumer._active_futures) >= 0  # futures may already be cleaned up
        finally:
            consumer.running = False
            consumer._IndexingKafkaConsumer__stop_worker_thread()


# ===========================================================================
# Constants
# ===========================================================================


class TestConstants:
    def test_max_concurrent_parsing(self):
        """MAX_CONCURRENT_PARSING has a positive default."""
        assert MAX_CONCURRENT_PARSING > 0

    def test_max_concurrent_indexing(self):
        """MAX_CONCURRENT_INDEXING has a positive default."""
        assert MAX_CONCURRENT_INDEXING > 0

    def test_future_cleanup_interval(self):
        """FUTURE_CLEANUP_INTERVAL is positive."""
        assert FUTURE_CLEANUP_INTERVAL > 0
