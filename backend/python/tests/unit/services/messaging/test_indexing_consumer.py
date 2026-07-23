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
import time
from collections.abc import AsyncGenerator
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

import ssl
from aiokafka import TopicPartition

from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData, StreamMessage, messaging_env
from app.services.messaging.distributed_concurrency import DistributedLeaseSet
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    FUTURE_CLEANUP_INTERVAL,
    IndexingKafkaConsumer,
    _compute_retry_backoff_seconds,
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
    return IndexingKafkaConsumer(logger, plain_config, retry_manager=None, producer=None)


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

    def test_multiple_stuck_futures_share_one_timeout_window(self, consumer):
        """N stuck futures must not multiply the wait into N * shutdown_task_timeout.

        Regression guard for the sequential-timeout-loop bug: waiting on 5
        never-resolving futures used to take up to 5 * shutdown_task_timeout
        (loop with future.result(timeout=...) per future); it must now take
        roughly one shutdown_task_timeout window total, since retry-backoff
        delays (Fix 6) can leave several futures simultaneously "stuck" mid
        sleep during a downstream outage.
        """
        futures = [Future() for _ in range(5)]  # never resolved
        with consumer._futures_lock:
            consumer._active_futures.update(futures)

        with patch.object(
            type(messaging_env), "shutdown_task_timeout", new_callable=PropertyMock, return_value=0.2,
        ):
            start = time.monotonic()
            consumer._wait_for_active_futures()
            elapsed = time.monotonic() - start

        # Bounded by ~one timeout window, not 5x.
        assert elapsed < 1.0


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
        """Unknown event types are silently ignored without marking success."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"k": "v"}}).encode("utf-8"))

        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False

        # Semaphores released in finally
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===================================================================
# Fix 6: retry backoff delay applied before semaphore acquisition
# ===================================================================

class TestRetryDelayBeforeSemaphore:
    """A re-queued message is stamped with a not-before timestamp, and the
    consumer waits it out before acquiring the parsing semaphore — so the
    wait ties up only a pending-task slot, never a parsing/indexing slot."""

    def test_backoff_schedule_grows_and_caps(self):
        assert _compute_retry_backoff_seconds(1) == pytest.approx(15.0)
        assert _compute_retry_backoff_seconds(2) == pytest.approx(60.0)
        assert _compute_retry_backoff_seconds(3) == pytest.approx(240.0)
        assert _compute_retry_backoff_seconds(10) == pytest.approx(300.0)

    @pytest.mark.asyncio
    async def test_requeue_message_stamps_not_before(self, logger, plain_config):
        """_requeue_message stamps an absolute _retry_not_before sized by retry_count."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.producer = AsyncMock()

        message = StreamMessage(eventType="newRecord", payload={"recordId": "r1"})
        before = time.time()

        await consumer._requeue_message("record-events", message, "stable-id", retry_count=2)

        consumer.producer.send_event.assert_awaited_once()
        sent_payload = consumer.producer.send_event.await_args.kwargs["payload"]
        assert sent_payload["_retry_tracking_id"] == "stable-id"
        # retry_count=2 -> ~60s backoff
        assert before + 59.0 <= sent_payload["_retry_not_before"] <= before + 61.0

    @pytest.mark.asyncio
    async def test_requeue_failure_does_not_commit_original(
        self, logger, plain_config
    ):
        retry_manager = AsyncMock()
        retry_manager.increment_and_check.return_value = (1, False)
        producer = AsyncMock()
        producer.send_event.side_effect = RuntimeError("broker unavailable")
        consumer = IndexingKafkaConsumer(
            logger,
            plain_config,
            retry_manager=retry_manager,
            producer=producer,
        )
        consumer.consumer = AsyncMock()
        message = _make_message(
            value=json.dumps(
                {
                    "eventType": "newRecord",
                    "payload": {"recordId": "r1"},
                }
            ).encode()
        )
        parsed = StreamMessage(
            eventType="newRecord",
            payload={"recordId": "r1"},
        )

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await consumer._IndexingKafkaConsumer__commit_if_appropriate(
                message,
                parsed,
                success=False,
            )

        consumer.consumer.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delay_occupies_no_semaphore_slot(self, logger, plain_config):
        """While waiting out the backoff, the parsing semaphore stays free."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.running = True  # delay loop bails early if not running (shutdown path)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        not_before = time.time() + 0.3
        msg = _make_message(value=json.dumps({
            "eventType": "test",
            "payload": {"k": "v", "_retry_not_before": not_before},
        }).encode("utf-8"))

        task = asyncio.create_task(
            consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        )
        await asyncio.sleep(0.1)
        # Still within the backoff window: semaphore must not be acquired yet.
        assert consumer.parsing_semaphore._value == 1

        result = await asyncio.wait_for(task, timeout=2.0)
        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_no_delay_when_not_before_already_elapsed(self, logger, plain_config):
        """A _retry_not_before timestamp already in the past causes no wait."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({
            "eventType": "test",
            "payload": {"k": "v", "_retry_not_before": time.time() - 5},
        }).encode("utf-8"))

        start = time.monotonic()
        result = await asyncio.wait_for(
            consumer._IndexingKafkaConsumer__process_message_wrapper(msg), timeout=1.0
        )
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_shutdown_interrupts_delay_without_committing(self, logger, plain_config):
        """A shutdown request (running -> False) during backoff aborts the wait
        promptly instead of holding the future for the full ~300s window, and
        the message is left uncommitted (no offset commit, no handler call)."""
        consumer = IndexingKafkaConsumer(logger, plain_config)
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = AsyncMock()

        msg = _make_message(value=json.dumps({
            "eventType": "test",
            "payload": {"k": "v", "_retry_not_before": time.time() + 120},
        }).encode("utf-8"))

        task = asyncio.create_task(
            consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        )
        await asyncio.sleep(0.1)
        consumer.running = False  # simulate stop() being called mid-backoff

        result = await asyncio.wait_for(task, timeout=2.0)
        assert result is False
        consumer.message_handler.assert_not_called()
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
        # Raises (rather than silently returning) so the caller's
        # except-block runs __finish_partition and undoes the pause from
        # __reserve_partition — see fix-consumer-lifecycle.
        consumer.worker_loop = None
        msg = _make_message()
        with pytest.raises(RuntimeError, match="Worker loop not initialized"):
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

    @pytest.mark.asyncio
    async def test_not_running(self, consumer):
        consumer.worker_loop = MagicMock()
        consumer.running = False
        msg = _make_message()
        with pytest.raises(RuntimeError, match="Consumer is stopping"):
            await consumer._IndexingKafkaConsumer__start_processing_task(msg)

    @pytest.mark.asyncio
    async def test_submits_to_worker(self, consumer):
        consumer.running = True
        consumer.worker_loop = MagicMock()
        mock_future = MagicMock(spec=Future)
        mock_future.add_done_callback = MagicMock()

        def submit_without_running(coro, _loop):
            coro.close()
            return mock_future

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=submit_without_running,
        ):
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
    async def test_indexing_limit_bounds_handlers_before_status_write(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(2)
        release = asyncio.Event()
        two_handlers_started = asyncio.Event()
        started: list[int] = []

        async def handler(parsed):
            started.append(int(parsed.payload["id"]))
            if len(started) == 2:
                two_handlers_started.set()
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id=str(parsed.payload["id"])),
            )
            await release.wait()
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=str(parsed.payload["id"])),
            )

        consumer.message_handler = handler
        tasks = [
            asyncio.create_task(
                consumer._IndexingKafkaConsumer__process_message_wrapper(
                    _make_message(
                        offset=i,
                        value=json.dumps(
                            {"eventType": "test", "payload": {"id": i}}
                        ).encode("utf-8"),
                    )
                )
            )
            for i in range(4)
        ]

        await asyncio.wait_for(two_handlers_started.wait(), timeout=1)
        await asyncio.sleep(0)

        assert len(started) == 2
        assert consumer.indexing_semaphore._value == 0
        assert consumer.parsing_semaphore._value == 1

        release.set()
        assert all(await asyncio.gather(*tasks))
        assert len(started) == 4
        assert consumer.indexing_semaphore._value == 2
        assert consumer.parsing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_parsing_limit_is_nested_inside_indexing_slots(
        self,
        consumer,
    ) -> None:
        """Up to MAX_CONCURRENT_INDEXING handlers can be active while only
        MAX_CONCURRENT_PARSING hold a parse slot (post-parse extraction)."""
        consumer.parsing_semaphore = asyncio.Semaphore(2)
        consumer.indexing_semaphore = asyncio.Semaphore(4)
        parsing_gate = asyncio.Event()
        indexing_gate = asyncio.Event()
        four_started = asyncio.Event()
        two_parsing = asyncio.Event()
        started: list[int] = []
        parsing: list[int] = []

        async def handler(parsed):
            record_id = int(parsed.payload["id"])
            started.append(record_id)
            if len(started) == 4:
                four_started.set()
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id=str(record_id)),
            )
            parsing.append(record_id)
            if len(parsing) == 2:
                two_parsing.set()
            await parsing_gate.wait()
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id=str(record_id)),
            )
            await indexing_gate.wait()
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=str(record_id)),
            )

        consumer.message_handler = handler
        tasks = [
            asyncio.create_task(
                consumer._IndexingKafkaConsumer__process_message_wrapper(
                    _make_message(
                        offset=i,
                        value=json.dumps(
                            {"eventType": "test", "payload": {"id": i}}
                        ).encode("utf-8"),
                    )
                )
            )
            for i in range(4)
        ]

        await asyncio.wait_for(four_started.wait(), timeout=1)
        await asyncio.wait_for(two_parsing.wait(), timeout=1)
        await asyncio.sleep(0.05)

        assert len(started) == 4
        assert len(parsing) == 2
        assert consumer.indexing_semaphore._value == 0
        assert consumer.parsing_semaphore._value == 0

        parsing_gate.set()
        await asyncio.sleep(0.05)
        assert consumer.parsing_semaphore._value == 2
        assert consumer.indexing_semaphore._value == 0

        indexing_gate.set()
        assert all(await asyncio.gather(*tasks))
        assert consumer.indexing_semaphore._value == 4
        assert consumer.parsing_semaphore._value == 2

    @pytest.mark.asyncio
    async def test_indexing_limit_is_shared_across_consumer_instances(
        self, logger, plain_config
    ):
        class SharedLeaseManager:
            def __init__(self):
                self.owners: dict[str, set[str]] = {}
                self.max_active: dict[str, int] = {}
                self.lock = asyncio.Lock()

            async def try_acquire(self, pool, owner, limit, _lease_seconds):
                async with self.lock:
                    owners = self.owners.setdefault(pool, set())
                    if len(owners) >= limit:
                        return False
                    owners.add(owner)
                    self.max_active[pool] = max(
                        self.max_active.get(pool, 0), len(owners)
                    )
                    return True

            async def renew(self, pool, owner, _lease_seconds):
                return owner in self.owners.get(pool, set())

            async def release(self, pool, owner):
                async with self.lock:
                    self.owners.setdefault(pool, set()).discard(owner)

        manager = SharedLeaseManager()
        consumers = [
            IndexingKafkaConsumer(
                logger,
                plain_config,
                concurrency_manager=manager,
            )
            for _ in range(2)
        ]
        release = asyncio.Event()
        two_started = asyncio.Event()
        started: list[int] = []

        async def handler(parsed):
            started.append(int(parsed.payload["id"]))
            if len(started) == 2:
                two_started.set()
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id=str(parsed.payload["id"])),
            )
            await release.wait()
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id=str(parsed.payload["id"])),
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=str(parsed.payload["id"])),
            )

        tasks = []
        for consumer_index, candidate in enumerate(consumers):
            candidate.running = True
            candidate.parsing_semaphore = asyncio.Semaphore(10)
            candidate.indexing_semaphore = asyncio.Semaphore(10)
            candidate.message_handler = handler
            for offset in range(3):
                record_id = consumer_index * 3 + offset
                tasks.append(
                    asyncio.create_task(
                        candidate._IndexingKafkaConsumer__process_message_wrapper(
                            _make_message(
                                partition=consumer_index,
                                offset=offset,
                                value=json.dumps(
                                    {
                                        "eventType": "test",
                                        "payload": {
                                            "id": record_id,
                                            "recordId": f"shared-{offset}",
                                        },
                                    }
                                ).encode("utf-8"),
                            )
                        )
                    )
                )

        with (
            patch.object(
                type(messaging_env),
                "max_concurrent_indexing",
                new_callable=PropertyMock,
                return_value=2,
            ),
            patch.object(
                type(messaging_env),
                "max_concurrent_parsing",
                new_callable=PropertyMock,
                return_value=2,
            ),
            patch.object(
                type(messaging_env),
                "concurrency_acquire_poll_seconds",
                new_callable=PropertyMock,
                return_value=0.01,
            ),
        ):
            await asyncio.wait_for(two_started.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert len(started) == 2
            assert manager.max_active["indexing"] == 2
            assert manager.max_active["parsing"] == 2
            assert sum(
                len(owners)
                for pool, owners in manager.owners.items()
                if pool.startswith("record:")
            ) == 2

            release.set()
            assert all(await asyncio.gather(*tasks))
            assert all(
                manager.max_active[f"record:shared-{offset}"] == 1
                for offset in range(3)
            )

    @pytest.mark.asyncio
    async def test_record_lease_contention_releases_indexing_slot(
        self, consumer
    ) -> None:
        """A duplicate delivery that loses the per-record lease race must
        give back the outer indexing semaphore/lease it already holds
        instead of leaking it — otherwise a handful of duplicate deliveries
        for one record can exhaust MAX_CONCURRENT_INDEXING for everyone."""
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease_seconds):
            return not pool.startswith("record:")

        manager.try_acquire.side_effect = try_acquire
        consumer.concurrency_manager = manager

        msg = _make_message(
            value=json.dumps(
                {"eventType": "test", "payload": {"recordId": "dup-1"}}
            ).encode("utf-8")
        )

        with (
            patch.object(
                type(messaging_env),
                "record_lease_wait_seconds",
                new_callable=PropertyMock,
                return_value=0.05,
            ),
            patch.object(
                type(messaging_env),
                "concurrency_acquire_poll_seconds",
                new_callable=PropertyMock,
                return_value=0.01,
            ),
        ):
            result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        assert result is False
        # Outer indexing lease/semaphore acquired before the record-lease
        # contention must be released, not leaked.
        assert consumer.indexing_semaphore._value == 1
        released_pools = {
            call.args[0] for call in manager.release.await_args_list
        }
        assert "indexing" in released_pools

    @pytest.mark.asyncio
    async def test_handler_exception_releases_semaphores(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(parsed):
            raise RuntimeError("handler error")
            yield

        consumer.message_handler = handler
        msg = _make_message(value=json.dumps({"eventType": "test", "payload": {"key": "val"}}).encode("utf-8"))
        result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_shutdown_during_parsing_slot_wait_does_not_increment_retry(
        self, consumer
    ) -> None:
        """If the consumer starts shutting down while a handler is parked
        waiting on the (undeadlined) global parsing lease, the message must
        be abandoned without bumping its retry count — otherwise a rolling
        deploy can dead-letter records that were never actually broken."""
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.retry_manager = AsyncMock()
        consumer.retry_manager.get_count.return_value = 0

        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease_seconds):
            # "indexing"/"record:*" succeed immediately; "parsing" never
            # does, forcing the handler to park in the poll loop below
            # until self.running flips (simulating a shutdown mid-wait).
            return pool != "parsing"

        manager.try_acquire.side_effect = try_acquire
        consumer.concurrency_manager = manager

        async def handler(_parsed) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        msg = _make_message(
            value=json.dumps(
                {"eventType": "test", "payload": {"recordId": "r1"}}
            ).encode("utf-8")
        )

        async def flip_running_off_shortly() -> None:
            await asyncio.sleep(0.03)
            consumer.running = False

        with patch.object(
            type(messaging_env),
            "concurrency_acquire_poll_seconds",
            new_callable=PropertyMock,
            return_value=0.01,
        ):
            flipper = asyncio.create_task(flip_running_off_shortly())
            result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
            await flipper

        assert result is False
        consumer.retry_manager.increment_and_check.assert_not_awaited()
        assert consumer.indexing_semaphore._value == 1
        assert consumer.parsing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_timeout_parked_on_parsing_semaphore_closes_handler(
        self, consumer
    ) -> None:
        """A record_processing_timeout while the handler is parked at
        ``await parsing_semaphore.acquire()`` (post ``yield START_PARSING``)
        must close the handler generator so its own finally/except cleanup
        (which reverts IN_PROGRESS) runs — otherwise the cancellation is
        only observed by the consumer loop and the record is stuck."""
        consumer.running = True
        # Exhausted: acquire() never resolves, forcing the timeout below to
        # fire while the handler is suspended there.
        consumer.parsing_semaphore = asyncio.Semaphore(0)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        handler_cleanup_ran = asyncio.Event()

        async def handler(_parsed) -> AsyncGenerator[PipelineEvent, None]:
            try:
                yield PipelineEvent(
                    event=IndexingEvent.START_PARSING,
                    data=PipelineEventData(record_id="r1"),
                )
            finally:
                handler_cleanup_ran.set()

        consumer.message_handler = handler
        msg = _make_message(
            value=json.dumps(
                {"eventType": "test", "payload": {"recordId": "r1"}}
            ).encode("utf-8")
        )

        with patch.object(
            type(messaging_env),
            "record_processing_timeout",
            new_callable=PropertyMock,
            return_value=0.05,
        ):
            result = await consumer._IndexingKafkaConsumer__process_message_wrapper(msg)

        assert result is False
        assert handler_cleanup_ran.is_set()
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_cancellation_does_not_orphan_handler_task(
        self,
        consumer,
    ) -> None:
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.concurrency_manager = AsyncMock()
        consumer.concurrency_manager.try_acquire.return_value = True
        entered = asyncio.Event()
        handler_cancelled = asyncio.Event()
        never_complete = asyncio.Event()

        async def handler(_parsed) -> AsyncGenerator[None, None]:
            entered.set()
            try:
                await never_complete.wait()
            finally:
                handler_cancelled.set()
            if False:
                yield

        async def renew_forever() -> None:
            await never_complete.wait()

        consumer.message_handler = handler
        renewal_task = asyncio.create_task(renew_forever())
        msg = _make_message(
            value=json.dumps(
                {
                    "eventType": "test",
                    "payload": {"recordId": "cancelled-record"},
                }
            ).encode("utf-8")
        )

        with patch.object(
            consumer,
            "_start_distributed_renewal",
            return_value=renewal_task,
        ):
            processing = asyncio.create_task(
                consumer._IndexingKafkaConsumer__process_message_wrapper(msg)
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            processing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await processing

        await asyncio.wait_for(handler_cancelled.wait(), timeout=1)
        assert renewal_task.done()
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_definitive_lease_loss_aborts_immediately(
        self,
        consumer,
    ) -> None:
        consumer.concurrency_manager = AsyncMock()
        consumer.concurrency_manager.renew.return_value = False
        leases = DistributedLeaseSet()
        leases.add("indexing", "worker-1")

        with (
            patch.object(
                type(messaging_env),
                "concurrency_lease_seconds",
                new_callable=PropertyMock,
                return_value=1,
            ),
            patch.object(
                type(messaging_env),
                "concurrency_renew_interval_seconds",
                new_callable=PropertyMock,
                return_value=0.01,
            ),
        ):
            with pytest.raises(RuntimeError, match="Lost distributed indexing"):
                await asyncio.wait_for(
                    consumer._renew_distributed_slots(leases),
                    timeout=0.2,
                )

        consumer.concurrency_manager.renew.assert_awaited_once()


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
        mock_consumer = AsyncMock()
        consumer.consumer = mock_consumer
        with patch.object(consumer, '_IndexingKafkaConsumer__stop_worker_thread'):
            await consumer.cleanup()
            mock_consumer.stop.assert_awaited_once()

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
    async def test_serializes_messages_within_partition(self, consumer) -> None:
        consumer.running = True
        mock_consumer = MagicMock()
        first = _make_message(partition=2, offset=10)
        second = _make_message(partition=2, offset=11)
        call_count = 0

        async def mock_getmany(**_kwargs) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"tp": [first, second]}
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        mock_consumer.assignment.return_value = {
            TopicPartition(first.topic, first.partition)
        }
        mock_consumer.paused.return_value = set()
        consumer.consumer = mock_consumer

        with patch.object(
            consumer,
            "_IndexingKafkaConsumer__start_processing_task",
            new_callable=AsyncMock,
        ) as start_task:
            await consumer._IndexingKafkaConsumer__consume_loop()

        start_task.assert_awaited_once_with(first)
        topic_partition = TopicPartition(first.topic, first.partition)
        mock_consumer.pause.assert_called_once_with(topic_partition)

        consumer.running = True
        consumer._IndexingKafkaConsumer__finish_partition(
            first,
            False,
        )

        mock_consumer.seek.assert_called_once_with(
            topic_partition,
            second.offset,
        )
        mock_consumer.resume.assert_called_once_with(topic_partition)

    @pytest.mark.asyncio
    async def test_fetch_is_limited_to_remaining_pending_capacity(
        self,
        consumer,
    ) -> None:
        consumer.running = True
        mock_consumer = MagicMock()
        mock_consumer.assignment.return_value = set()
        mock_consumer.paused.return_value = set()
        requested_max_records = None

        async def mock_getmany(**kwargs) -> dict:
            nonlocal requested_max_records
            requested_max_records = kwargs["max_records"]
            consumer.running = False
            return {}

        mock_consumer.getmany = mock_getmany
        consumer.consumer = mock_consumer
        with consumer._futures_lock:
            consumer._active_futures.update(Future() for _ in range(39))

        with (
            patch.object(
                type(messaging_env),
                "max_pending_indexing_tasks",
                new_callable=PropertyMock,
                return_value=40,
            ),
            patch.object(
                type(messaging_env),
                "message_batch_size_indexing",
                new_callable=PropertyMock,
                return_value=10,
            ),
        ):
            await consumer._IndexingKafkaConsumer__consume_loop()

        assert requested_max_records == 1

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
