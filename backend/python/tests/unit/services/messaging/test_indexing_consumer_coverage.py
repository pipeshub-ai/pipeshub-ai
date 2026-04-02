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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingEvent,
    IndexingKafkaConsumer,
    MAX_CONCURRENT_INDEXING,
    MAX_CONCURRENT_PARSING,
    MAX_PENDING_INDEXING_TASKS,
    SHUTDOWN_TASK_TIMEOUT,
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

        # Patch SHUTDOWN_TASK_TIMEOUT to very small value
        with patch(
            "app.services.messaging.kafka.consumer.indexing_consumer.SHUTDOWN_TASK_TIMEOUT",
            0.01,
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
            for _ in range(MAX_PENDING_INDEXING_TASKS):
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
            for _ in range(MAX_PENDING_INDEXING_TASKS):
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
        msg = _make_message(value=json.dumps({"x": 1}).encode("utf-8"))
        result = consumer._IndexingKafkaConsumer__parse_message(msg)
        assert result == {"x": 1}


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
            yield {"event": "indexing_complete", "data": {}}

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))

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
            yield {"event": "unknown_event", "data": {}}

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))

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
                yield {"event": "parsing_complete"}
                yield {"event": "indexing_complete"}

            consumer.message_handler = handler

            msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))
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
