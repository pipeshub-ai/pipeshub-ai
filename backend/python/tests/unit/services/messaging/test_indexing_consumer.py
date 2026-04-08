"""Additional tests for IndexingKafkaConsumer targeting remaining uncovered lines.

Covers:
- __stop_worker_thread (with and without running loop)
- _wait_for_active_futures (timeout, error scenarios)
- __apply_backpressure (engage and clear)
- __consume_loop (message processing, error handling)
- __start_processing_task (future tracking and callback)
- __process_message_wrapper (partial event yields)
- cleanup (with worker thread)
- stop (full lifecycle)
"""

import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

import ssl

from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData, StreamMessage, messaging_env
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    FUTURE_CLEANUP_INTERVAL,
    IndexingKafkaConsumer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def logger():
    return logging.getLogger("test_indexing_cov")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def consumer(logger, plain_config):
    return IndexingKafkaConsumer(logger, plain_config)


def _make_message(topic="test-topic", partition=0, offset=0, value=None):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


# ===================================================================
# __stop_worker_thread
# ===================================================================

class TestStopWorkerThread:

    def test_stop_without_started_thread(self, consumer):
        """Stopping when thread was never started should not raise."""
        consumer._IndexingKafkaConsumer__stop_worker_thread()
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None

    def test_stop_after_start(self, consumer):
        """Stop a running worker thread cleanly."""
        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)
        assert consumer.worker_loop is not None
        assert consumer.worker_loop.is_running()

        consumer._IndexingKafkaConsumer__stop_worker_thread()
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None

    def test_stop_clears_active_futures(self, consumer):
        """Active futures set is cleared on stop."""
        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)

        # Add a completed future
        f = Future()
        f.set_result(None)
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        consumer._IndexingKafkaConsumer__stop_worker_thread()
        assert len(consumer._active_futures) == 0


# ===================================================================
# _wait_for_active_futures - timeout and error
# ===================================================================

class TestWaitForActiveFuturesExtended:

    def test_timeout_futures_cancelled(self, consumer):
        """Futures that timeout are cancelled."""
        f = Future()
        # Don't set result - will timeout
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        # Patch shutdown_task_timeout to very small value
        with patch.object(
            type(messaging_env), "shutdown_task_timeout", new_callable=PropertyMock, return_value=0.01,
        ):
            consumer._wait_for_active_futures()
        # Should not raise

    def test_mixed_futures(self, consumer):
        """Mix of completed and errored futures."""
        f1 = Future()
        f1.set_result("ok")
        f2 = Future()
        f2.set_exception(ValueError("bad"))
        f3 = Future()
        f3.set_result(None)

        with consumer._futures_lock:
            consumer._active_futures.update({f1, f2, f3})

        consumer._wait_for_active_futures()
        # Should not raise


# ===================================================================
# __apply_backpressure
# ===================================================================

class TestApplyBackpressure:

    def test_engages_when_at_capacity(self, consumer):
        """Pauses partitions when at capacity."""
        consumer.consumer = MagicMock()
        assigned = {MagicMock(), MagicMock()}
        consumer.consumer.assignment.return_value = assigned
        consumer.consumer.paused.return_value = set()

        # Add futures to reach capacity
        with consumer._futures_lock:
            for _ in range(messaging_env.max_pending_indexing_tasks):
                f = Future()
                consumer._active_futures.add(f)

        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_called_once()
        assert consumer._backpressure_logged is True

    def test_clears_when_below_capacity(self, consumer):
        """Resumes partitions when below capacity."""
        consumer.consumer = MagicMock()
        paused = {MagicMock()}
        consumer.consumer.paused.return_value = paused
        consumer._backpressure_logged = True

        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.resume.assert_called_once()
        assert consumer._backpressure_logged is False

    def test_no_action_when_no_paused_and_below_capacity(self, consumer):
        """No resume needed when nothing is paused."""
        consumer.consumer = MagicMock()
        consumer.consumer.paused.return_value = set()
        consumer._backpressure_logged = False

        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.resume.assert_not_called()

    def test_already_paused_not_paused_again(self, consumer):
        """Already paused partitions aren't paused again."""
        consumer.consumer = MagicMock()
        tp = MagicMock()
        consumer.consumer.assignment.return_value = {tp}
        consumer.consumer.paused.return_value = {tp}  # Already paused
        consumer._backpressure_logged = True

        with consumer._futures_lock:
            for _ in range(messaging_env.max_pending_indexing_tasks):
                f = Future()
                consumer._active_futures.add(f)

        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_not_called()


# ===================================================================
# __parse_message - additional
# ===================================================================

class TestParseMessageAdditional:

    def test_bytes_value_isinstance_check(self, consumer):
        """Ensure isinstance check works for bytes -> str conversion."""
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"x": 1}}).encode("utf-8"))
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert isinstance(result, StreamMessage)
        assert result.eventType == "test"
        assert result.payload == {"x": 1}


# ===================================================================
# __process_message_wrapper - partial event yields
# ===================================================================

class TestProcessMessageWrapperExtended:

    @pytest.mark.asyncio
    async def test_only_indexing_complete_released(self, logger, plain_config):
        """When only indexing_complete is yielded, parsing released in finally."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"))

        await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        # Both should be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_unknown_event_type_ignored(self, logger, plain_config):
        """Unknown event types are silently ignored."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"))

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is True

        # Semaphores released in finally
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===================================================================
# cleanup with worker thread
# ===================================================================

class TestCleanupWithWorkerThread:

    @pytest.mark.asyncio
    async def test_cleanup_stops_worker_and_consumer(self, logger, plain_config):
        """Cleanup stops both worker thread and consumer."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)

        mock_kafka = AsyncMock()
        consumer.consumer = mock_kafka

        await consumer.cleanup()
        mock_kafka.stop.assert_awaited_once()
        assert consumer.worker_executor is None


# ===================================================================
# stop with full lifecycle
# ===================================================================

class TestStopFullLifecycle:

    @pytest.mark.asyncio
    async def test_stop_with_consume_task(self, logger, plain_config):
        """Stop cancels consume task and cleans up."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.running = True
        consumer.consumer = AsyncMock()

        async def dummy_loop():
            while True:
                await asyncio.sleep(0.1)

        consumer.consume_task = asyncio.create_task(dummy_loop())

        await consumer.stop()
        assert consumer.running is False
        assert consumer.consume_task.cancelled() or consumer.consume_task.done()

    @pytest.mark.asyncio
    async def test_stop_consumer_error_handled(self, logger, plain_config):
        """Stop handles consumer.stop() error gracefully."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.running = True
        mock_kafka = AsyncMock()
        mock_kafka.stop = AsyncMock(side_effect=Exception("stop error"))
        consumer.consumer = mock_kafka

        await consumer.stop()
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_without_consume_task(self, logger, plain_config):
        """Stop works when no consume task exists."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.running = True
        consumer.consumer = AsyncMock()
        consumer.consume_task = None

        await consumer.stop()
        assert consumer.running is False


# ===================================================================
# __start_processing_task - future callback
# ===================================================================

class TestStartProcessingTaskCallback:

    @pytest.mark.asyncio
    async def test_future_callback_removes_from_tracking(self, logger, plain_config):
        """Done callback removes future from _active_futures."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer._IndexingKafkaConsumer__start_worker_thread()

        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            consumer.running = True

            async def handler(msg):
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

            consumer.message_handler = handler

            msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"))
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

            # Wait for task completion
            import time
            time.sleep(1)

            # Future should be cleaned up via callback
            with consumer._futures_lock:
                # All futures should have been cleaned up
                remaining = len(consumer._active_futures)
            assert remaining == 0
        finally:
            consumer.running = False
            consumer._IndexingKafkaConsumer__stop_worker_thread()

# =============================================================================
# Merged from test_indexing_consumer_full_coverage.py
# =============================================================================

@pytest.fixture
def logger_fullcov():
    return logging.getLogger("test_indexing_full")


@pytest.fixture
def plain_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def ssl_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=True,
        sasl={"username": "user", "password": "pass", "mechanism": "SCRAM-SHA-512"},
    )


@pytest.fixture
def ssl_no_sasl_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=True,
        sasl=None,
    )


@pytest.fixture
def consumer(logger_fullcov, plain_config):
    return IndexingKafkaConsumer(logger_fullcov, plain_config)


def _make_message(topic="test-topic", partition=0, offset=0, value=None):
    msg = MagicMock()
    msg.topic = topic
    msg.partition = partition
    msg.offset = offset
    msg.value = value
    return msg


class TestKafkaConfigToDict:
    def test_basic_config(self, plain_config):
        result = IndexingKafkaConsumer.kafka_config_to_dict(plain_config)
        assert result["bootstrap_servers"] == "broker:9092"
        assert result["group_id"] == "idx-group"
        assert result["topics"] == ["idx-topic"]
        assert "ssl_context" not in result

    def test_ssl_with_sasl(self, ssl_config):
        result = IndexingKafkaConsumer.kafka_config_to_dict(ssl_config)
        assert result["security_protocol"] == "SASL_SSL"
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"
        assert result["sasl_plain_username"] == "user"
        assert result["sasl_plain_password"] == "pass"
        assert "ssl_context" in result

    def test_ssl_without_sasl(self, ssl_no_sasl_config):
        result = IndexingKafkaConsumer.kafka_config_to_dict(ssl_no_sasl_config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result


class TestIndexingEventConstants:
    def test_parsing_complete(self):
        assert IndexingEvent.PARSING_COMPLETE == "parsing_complete"

    def test_indexing_complete(self):
        assert IndexingEvent.INDEXING_COMPLETE == "indexing_complete"


class TestInit:
    def test_attributes(self, consumer):
        assert consumer.consumer is None
        assert consumer.running is False
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None
        assert consumer.message_handler is None
        assert len(consumer._active_futures) == 0


class TestIsRunning:
    def test_default_false(self, consumer):
        assert consumer.is_running() is False

    def test_after_set_true(self, consumer):
        consumer.running = True
        assert consumer.is_running() is True


class TestGetActiveTaskCount:
    def test_empty(self, consumer):
        assert consumer._get_active_task_count() == 0

    def test_with_futures(self, consumer):
        f = Future()
        consumer._active_futures.add(f)
        assert consumer._get_active_task_count() == 1


class TestParseMessage:
    def test_json_string(self, consumer):
        msg = _make_message(value='{"eventType": "test", "payload": {"key": "value"}}')
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert isinstance(result, StreamMessage)
        assert result.eventType == "test"
        assert result.payload == {"key": "value"}

    def test_bytes_message(self, consumer):
        msg = _make_message(value=b'{"eventType": "test", "payload": {"key": "value"}}')
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert isinstance(result, StreamMessage)
        assert result.payload == {"key": "value"}

    def test_double_encoded_json(self, consumer):
        inner = json.dumps({"eventType": "test", "payload": {"key": "value"}})
        msg = _make_message(value=json.dumps(inner))
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert isinstance(result, StreamMessage)
        assert result.payload == {"key": "value"}

    def test_invalid_json(self, consumer):
        msg = _make_message(value="not json")
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert result is None

    def test_unexpected_type(self, consumer):
        msg = _make_message(value=12345)
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert result is None

    def test_unicode_decode_error(self, consumer):
        msg = _make_message(value=b'\xff\xfe')
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert result is None


class TestStartProcessingTask:
    @pytest.mark.asyncio
    async def test_no_worker_loop(self, consumer):
        consumer.worker_loop = None
        msg = _make_message()
        await consumer._IndexingKafkaConsumer__start_processing_task(msg)

    @pytest.mark.asyncio
    async def test_not_running(self, consumer):
        consumer.worker_loop = MagicMock()
        consumer.running = False
        msg = _make_message()
        await consumer._IndexingKafkaConsumer__start_processing_task(msg)

    @pytest.mark.asyncio
    async def test_submits_to_worker(self, consumer):
        consumer.running = True
        consumer.worker_loop = MagicMock()
        mock_future = MagicMock(spec=Future)
        mock_future.add_done_callback = MagicMock()
        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
            msg = _make_message()
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)
            assert mock_future in consumer._active_futures


class TestProcessMessageWrapper:
    @pytest.mark.asyncio
    async def test_no_semaphores(self, consumer):
        consumer.parsing_semaphore = None
        consumer.indexing_semaphore = None
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_parse_failure(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        msg = _make_message(value="invalid json")
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_handler(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = None
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_processing(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(parsed):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is True

    @pytest.mark.asyncio
    async def test_handler_exception_releases_semaphores(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(parsed):
            raise RuntimeError("handler error")
            yield  # noqa: unreachable

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


class TestWaitForActiveFutures:
    def test_no_futures(self, consumer):
        consumer._wait_for_active_futures()

    def test_completed_future(self, consumer):
        f = Future()
        f.set_result(None)
        consumer._active_futures.add(f)
        consumer._wait_for_active_futures()

    def test_errored_future(self, consumer):
        f = Future()
        f.set_exception(RuntimeError("err"))
        consumer._active_futures.add(f)
        consumer._wait_for_active_futures()


class TestApplyBackpressureFullCoverage:
    def test_engage_backpressure(self, consumer):
        consumer.consumer = MagicMock()
        assigned = {MagicMock(), MagicMock()}
        consumer.consumer.assignment.return_value = assigned
        consumer.consumer.paused.return_value = set()
        for _ in range(messaging_env.max_pending_indexing_tasks + 1):
            f = Future()
            consumer._active_futures.add(f)
        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_called()
        assert consumer._backpressure_logged is True

    def test_clear_backpressure(self, consumer):
        consumer.consumer = MagicMock()
        consumer.consumer.paused.return_value = {MagicMock()}
        consumer._backpressure_logged = True
        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.resume.assert_called()
        assert consumer._backpressure_logged is False


class TestInitialize:
    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger_fullcov):
        consumer = IndexingKafkaConsumer(logger_fullcov, None)
        consumer.kafka_config = None
        with pytest.raises(ValueError, match="not valid"):
            await consumer.initialize()

    @pytest.mark.asyncio
    async def test_worker_loop_timeout(self, logger_fullcov, plain_config):
        consumer = IndexingKafkaConsumer(logger_fullcov, plain_config)
        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread'):
            consumer.worker_loop_ready = MagicMock()
            consumer.worker_loop_ready.wait.return_value = False
            with pytest.raises(RuntimeError, match="not initialized in time"):
                await consumer.initialize()


class TestStart:
    @pytest.mark.asyncio
    async def test_starts_consume_task(self, consumer):
        consumer.consumer = MagicMock()

        async def dummy_handler(msg):
            yield {"event": "done"}

        with patch.object(consumer, '_IndexingKafkaConsumer__consume_loop', new_callable=AsyncMock):
            await consumer.start(dummy_handler)
            assert consumer.running is True
            assert consumer.message_handler is dummy_handler

    @pytest.mark.asyncio
    async def test_start_without_consumer_initializes(self, consumer):
        consumer.consumer = None

        async def dummy_handler(msg):
            yield {"event": "done"}

        with patch.object(consumer, 'initialize', new_callable=AsyncMock):
            with patch.object(consumer, '_IndexingKafkaConsumer__consume_loop', new_callable=AsyncMock):
                await consumer.start(dummy_handler)
                consumer.initialize.assert_awaited_once()


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_full_lifecycle(self, consumer):
        consumer.running = True
        consumer.consume_task = asyncio.create_task(asyncio.sleep(10))
        consumer.consumer = AsyncMock()
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.stop()
            assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_no_consume_task(self, consumer):
        consumer.running = True
        consumer.consume_task = None
        consumer.consumer = None
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.stop()
            assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_consumer_exception(self, consumer):
        consumer.running = True
        consumer.consume_task = None
        consumer.consumer = AsyncMock()
        consumer.consumer.stop = AsyncMock(side_effect=Exception("err"))
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.stop()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_with_consumer(self, consumer):
        consumer.consumer = AsyncMock()
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.cleanup()
            consumer.consumer.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_without_consumer(self, consumer):
        consumer.consumer = None
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_exception(self, consumer):
        consumer.consumer = AsyncMock()
        consumer.consumer.stop = AsyncMock(side_effect=Exception("err"))
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.cleanup()


class TestConsumeLoop:
    @pytest.mark.asyncio
    async def test_stops_when_not_running(self, consumer):
        consumer.running = False
        consumer.consumer = AsyncMock()
        await consumer._IndexingKafkaConsumer__consume_loop()

    @pytest.mark.asyncio
    async def test_processes_messages(self, consumer):
        consumer.running = True
        mock_consumer = MagicMock()
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"tp": [msg]}
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        with patch.object(consumer, '_IndexingKafkaConsumer__start_processing_task', new_callable=AsyncMock):
            await consumer._IndexingKafkaConsumer__consume_loop()

    @pytest.mark.asyncio
    async def test_handles_cancelled_error(self, consumer):
        consumer.running = True
        mock_consumer = MagicMock()
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()

        async def mock_getmany(**kwargs):
            raise asyncio.CancelledError()

        mock_consumer.getmany = mock_getmany
        consumer.consumer = mock_consumer
        await consumer._IndexingKafkaConsumer__consume_loop()
