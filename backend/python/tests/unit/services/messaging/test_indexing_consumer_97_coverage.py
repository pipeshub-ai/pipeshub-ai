"""Additional tests for IndexingKafkaConsumer targeting >97% coverage.

Targets specific uncovered lines:
- Line 120: pending tasks cancellation in run_worker_loop finally block
- Line 124: gathering pending tasks
- Lines 156-170: initialize with SSL and auto-commit logging
- Lines 217-219: _wait_for_active_futures timeout branch
- Lines 269-271: start exception handling
- Lines 361-362: consume loop inner not-running break
- Lines 367-369: consume loop per-message exception
- Lines 374-380: consume loop general and fatal exceptions
- Lines 465-466: on_future_done exception logging
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
    MAX_PENDING_INDEXING_TASKS,
    SHUTDOWN_TASK_TIMEOUT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def logger():
    return logging.getLogger("test_indexing_97")


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
def auto_commit_config():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )


@pytest.fixture
def ssl_config_with_sasl():
    return KafkaConsumerConfig(
        topics=["idx-topic"],
        client_id="idx-consumer",
        group_id="idx-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=True,
        sasl={"username": "admin", "password": "secret", "mechanism": "SCRAM-SHA-512"},
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
# Initialize — successful flow with auto-commit enabled
# ===================================================================

class TestInitializeAutoCommit:

    @pytest.mark.asyncio
    async def test_auto_commit_enabled_logging(self, logger, auto_commit_config):
        """When auto_commit is enabled, it should log accordingly."""
        consumer = IndexingKafkaConsumer(logger, auto_commit_config)

        mock_kafka_consumer = AsyncMock()
        mock_kafka_consumer.start = AsyncMock()

        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread'):
            consumer.worker_loop_ready = MagicMock()
            consumer.worker_loop_ready.wait.return_value = True
            consumer.worker_loop = MagicMock()
            consumer.worker_loop.is_running.return_value = True

            with patch("app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
                        return_value=mock_kafka_consumer):
                await consumer.initialize()
                assert consumer.consumer is mock_kafka_consumer
                mock_kafka_consumer.start.assert_awaited_once()


# ===================================================================
# Initialize — worker loop not running after ready signal
# ===================================================================

class TestInitializeWorkerLoopNotRunning:

    @pytest.mark.asyncio
    async def test_worker_loop_not_running(self, logger, plain_config):
        """When worker_loop_ready signals but loop isn't running."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread'):
            consumer.worker_loop_ready = MagicMock()
            consumer.worker_loop_ready.wait.return_value = True
            consumer.worker_loop = MagicMock()
            consumer.worker_loop.is_running.return_value = False

            with pytest.raises(RuntimeError, match="failed to start"):
                await consumer.initialize()

    @pytest.mark.asyncio
    async def test_worker_loop_is_none(self, logger, plain_config):
        """When worker_loop_ready signals but loop is None."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread'):
            consumer.worker_loop_ready = MagicMock()
            consumer.worker_loop_ready.wait.return_value = True
            consumer.worker_loop = None

            with pytest.raises(RuntimeError, match="failed to start"):
                await consumer.initialize()


# ===================================================================
# Initialize — exception triggers stop
# ===================================================================

class TestInitializeExceptionTriggersStop:

    @pytest.mark.asyncio
    async def test_exception_during_init_calls_stop(self, logger, plain_config):
        """Any exception during init should call stop."""
        consumer = IndexingKafkaConsumer(logger, plain_config)

        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread',
                          side_effect=Exception("start error")):
            with patch.object(consumer, 'stop', new_callable=AsyncMock) as mock_stop:
                with pytest.raises(Exception, match="start error"):
                    await consumer.initialize()
                mock_stop.assert_awaited_once()


# ===================================================================
# Initialize with SSL config
# ===================================================================

class TestInitializeSSL:

    @pytest.mark.asyncio
    async def test_ssl_config_init(self, logger, ssl_config_with_sasl):
        """Initialize with SSL/SASL configuration."""
        consumer = IndexingKafkaConsumer(logger, ssl_config_with_sasl)

        mock_kafka_consumer = AsyncMock()
        mock_kafka_consumer.start = AsyncMock()

        with patch.object(consumer, '_IndexingKafkaConsumer__start_worker_thread'):
            consumer.worker_loop_ready = MagicMock()
            consumer.worker_loop_ready.wait.return_value = True
            consumer.worker_loop = MagicMock()
            consumer.worker_loop.is_running.return_value = True

            with patch("app.services.messaging.kafka.consumer.indexing_consumer.AIOKafkaConsumer",
                        return_value=mock_kafka_consumer):
                await consumer.initialize()
                assert consumer.consumer is mock_kafka_consumer


# ===================================================================
# Start — exception handling
# ===================================================================

class TestStartExceptionHandling:

    @pytest.mark.asyncio
    async def test_start_initialize_failure(self, logger, plain_config):
        """When initialize fails during start, exception is raised."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.consumer = None

        async def handler(msg):
            yield {"event": "done"}

        with patch.object(consumer, 'initialize', new_callable=AsyncMock,
                          side_effect=Exception("init failed")):
            with pytest.raises(Exception, match="init failed"):
                await consumer.start(handler)

    @pytest.mark.asyncio
    async def test_start_exception_raised(self, logger, plain_config):
        """Exception during start is propagated."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.consumer = MagicMock()
        consumer.running = False

        async def handler(msg):
            yield {"event": "done"}

        with patch("asyncio.create_task", side_effect=Exception("task error")):
            with pytest.raises(Exception, match="task error"):
                await consumer.start(handler)


# ===================================================================
# Consume loop — inner not-running break
# ===================================================================

class TestConsumeLoopNotRunningInner:

    @pytest.mark.asyncio
    async def test_stops_processing_batch_when_not_running(self, consumer):
        """When running becomes False mid-batch, remaining messages are skipped."""
        consumer.running = True
        mock_consumer = MagicMock()

        msg1 = _make_message(value=b'{"key": "val"}')
        msg2 = _make_message(value=b'{"key": "val2"}')

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"tp": [msg1, msg2]}
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        process_call_count = 0

        async def mock_process(msg):
            nonlocal process_call_count
            process_call_count += 1
            # Stop running after first message
            consumer.running = False

        with patch.object(consumer, '_IndexingKafkaConsumer__start_processing_task',
                          side_effect=mock_process):
            await consumer._IndexingKafkaConsumer__consume_loop()

        # Only first message should be processed
        assert process_call_count == 1


# ===================================================================
# Consume loop — per-message exception handling
# ===================================================================

class TestConsumeLoopMessageException:

    @pytest.mark.asyncio
    async def test_continues_after_message_error(self, consumer):
        """Error processing one message doesn't stop other messages."""
        consumer.running = True
        mock_consumer = MagicMock()

        msg1 = _make_message(value=b'{"key": "val1"}')
        msg2 = _make_message(value=b'{"key": "val2"}')

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"tp": [msg1, msg2]}
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        process_call_count = 0

        async def mock_process(msg):
            nonlocal process_call_count
            process_call_count += 1
            if process_call_count == 1:
                raise Exception("processing error")

        with patch.object(consumer, '_IndexingKafkaConsumer__start_processing_task',
                          side_effect=mock_process):
            await consumer._IndexingKafkaConsumer__consume_loop()

        # Both messages should be attempted
        assert process_call_count == 2


# ===================================================================
# Consume loop — general exception and retry
# ===================================================================

class TestConsumeLoopGeneralException:

    @pytest.mark.asyncio
    async def test_general_exception_retries(self, consumer):
        """General exception in loop retries after sleep."""
        consumer.running = True
        mock_consumer = MagicMock()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("kafka error")
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await consumer._IndexingKafkaConsumer__consume_loop()
            mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_general_exception_while_not_running(self, consumer):
        """General exception when not running doesn't retry."""
        consumer.running = False
        mock_consumer = MagicMock()

        async def mock_getmany(**kwargs):
            raise RuntimeError("kafka error")

        mock_consumer.getmany = mock_getmany
        consumer.consumer = mock_consumer

        # Should exit without sleeping since running is False from the start
        await consumer._IndexingKafkaConsumer__consume_loop()


# ===================================================================
# Consume loop — fatal error
# ===================================================================

class TestConsumeLoopFatalError:

    @pytest.mark.asyncio
    async def test_fatal_error_logged(self, consumer):
        """Fatal error in consume_messages is caught at outer level."""
        consumer.running = True

        # Patch logger.info to raise on the first call ("Starting Kafka consumer loop")
        # which happens BEFORE the inner while loop, triggering the outer except
        original_info = consumer.logger.info
        call_count = [0]
        def patched_info(msg, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("fatal error")
            return original_info(msg, *args, **kwargs)

        consumer.logger.info = patched_info
        consumer.consumer = MagicMock()

        await consumer._IndexingKafkaConsumer__consume_loop()
        # Should exit gracefully after fatal error


# ===================================================================
# on_future_done callback — exception logging
# ===================================================================

class TestOnFutureDoneException:

    @pytest.mark.asyncio
    async def test_future_with_exception_logs_error(self, logger, plain_config):
        """When future completes with exception, it's logged."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer._IndexingKafkaConsumer__start_worker_thread()

        try:
            assert consumer.worker_loop_ready.wait(timeout=5.0)
            consumer.running = True

            async def failing_handler(msg):
                raise RuntimeError("handler exploded")
                yield  # noqa: unreachable

            consumer.message_handler = failing_handler

            # Set up semaphores in the worker loop
            async def setup_semaphores():
                consumer.parsing_semaphore = asyncio.Semaphore(1)
                consumer.indexing_semaphore = asyncio.Semaphore(1)

            asyncio.run_coroutine_threadsafe(setup_semaphores(), consumer.worker_loop).result(timeout=5)

            msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

            # Wait for task completion
            import time
            time.sleep(2)

            # Future should be cleaned up via callback even after exception
            with consumer._futures_lock:
                remaining = len(consumer._active_futures)
            assert remaining == 0
        finally:
            consumer.running = False
            consumer._IndexingKafkaConsumer__stop_worker_thread()


# ===================================================================
# Worker thread — pending tasks cancellation in finally
# ===================================================================

class TestWorkerThreadPendingTasks:

    def test_worker_thread_cleans_pending_tasks(self, logger, plain_config):
        """Worker thread cancels pending tasks when stopped."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer._IndexingKafkaConsumer__start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)

        # Submit a long-running task to the worker loop
        async def long_running():
            await asyncio.sleep(100)

        future = asyncio.run_coroutine_threadsafe(long_running(), consumer.worker_loop)

        # Stop should clean up
        consumer._IndexingKafkaConsumer__stop_worker_thread()
        assert consumer.worker_executor is None


# ===================================================================
# __stop_worker_thread — loop not running
# ===================================================================

class TestStopWorkerThreadNotRunning:

    def test_stop_when_loop_exists_but_not_running(self, consumer):
        """Stop when worker_loop exists but is not running - skips loop.stop()."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        consumer.worker_loop = mock_loop
        mock_executor = MagicMock()
        consumer.worker_executor = mock_executor

        consumer._IndexingKafkaConsumer__stop_worker_thread()
        # Loop stop was NOT called since it wasn't running
        mock_loop.call_soon_threadsafe.assert_not_called()
        mock_executor.shutdown.assert_called_once_with(wait=True)


# ===================================================================
# Process message wrapper — edge cases
# ===================================================================

class TestProcessMessageWrapperEdgeCases:

    @pytest.mark.asyncio
    async def test_only_parsing_complete_event(self, logger, plain_config):
        """When only parsing_complete is yielded, indexing semaphore released in finally."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield {"event": IndexingEvent.PARSING_COMPLETE}

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is True

        # Both semaphores should be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_neither_semaphore_released_by_handler(self, logger, plain_config):
        """When handler yields no release events, both released in finally."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield {"event": "other_event"}

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is True

        # Both should be released in finally
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_exception_with_parsing_released(self, logger, plain_config):
        """When handler raises after parsing_complete, only indexing released in finally."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield {"event": IndexingEvent.PARSING_COMPLETE}
            raise RuntimeError("boom")

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"k": "v"}).encode("utf-8"))

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False

        # Both should still be released
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===================================================================
# Consume loop — empty batch
# ===================================================================

class TestConsumeLoopEmptyBatch:

    @pytest.mark.asyncio
    async def test_empty_batch_continues(self, consumer):
        """Empty batch from getmany continues to next iteration."""
        consumer.running = True
        mock_consumer = MagicMock()

        call_count = 0

        async def mock_getmany(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {}  # Empty batch
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        await consumer._IndexingKafkaConsumer__consume_loop()
        assert call_count == 3


# ===================================================================
# SASL config without mechanism defaults
# ===================================================================

class TestKafkaConfigSASLDefaults:

    def test_ssl_with_sasl_no_mechanism(self):
        """SASL without mechanism defaults to SCRAM-SHA-512."""
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
        result = IndexingKafkaConsumer.kafka_config_to_dict(config)
        assert result["sasl_mechanism"] == "SCRAM-SHA-512"

    def test_ssl_with_empty_sasl_username(self):
        """SSL with empty SASL username defaults to SSL only."""
        config = KafkaConsumerConfig(
            topics=["t"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
            ssl=True,
            sasl={"username": "", "password": "p"},
        )
        result = IndexingKafkaConsumer.kafka_config_to_dict(config)
        assert result["security_protocol"] == "SSL"
        assert "sasl_mechanism" not in result


# ===================================================================
# Backpressure — already at capacity, no new partitions to pause
# ===================================================================

class TestBackpressureAlreadyLogged:

    def test_backpressure_already_engaged_doesnt_relog(self, consumer):
        """When backpressure is already logged, doesn't log again."""
        consumer.consumer = MagicMock()
        tp = MagicMock()
        assigned = {tp}
        consumer.consumer.assignment.return_value = assigned
        consumer.consumer.paused.return_value = assigned  # Same set: all paused
        consumer._backpressure_logged = True

        # Add futures to reach capacity
        with consumer._futures_lock:
            for _ in range(MAX_PENDING_INDEXING_TASKS + 1):
                f = Future()
                consumer._active_futures.add(f)

        consumer._IndexingKafkaConsumer__apply_backpressure()
        # Pause not called because assigned - paused = empty set
        consumer.consumer.pause.assert_not_called()
        # Still logged
        assert consumer._backpressure_logged is True

    def test_clear_when_nothing_paused_doesnt_relog(self, consumer):
        """When nothing is paused and backpressure wasn't logged, no logging."""
        consumer.consumer = MagicMock()
        consumer.consumer.paused.return_value = set()
        consumer._backpressure_logged = False

        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.resume.assert_not_called()
        assert consumer._backpressure_logged is False
