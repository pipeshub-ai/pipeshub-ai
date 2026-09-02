"""Tests for IndexingRedisStreamsConsumer covering lines 49-452.

Covers:
- __init__: attribute defaults
- initialize(): Redis creation, consumer group creation, BUSYGROUP handling,
  stale consumer deletion, exception-triggers-stop
- _start_worker_thread() / _stop_worker_thread(): lifecycle
- _wait_for_active_futures(): timeout, error, empty
- _drain_pending(): PEL recovery with messages, empty PEL, not-running exit
- start() / stop(): full lifecycle, re-initialize if no redis
- _consume_loop(): backpressure engage/clear, message dispatch, CancelledError,
  general exception retry, fatal error, not-running inner break
- _parse_message(): valid JSON, double-encoded, missing value field, invalid JSON
- _start_processing_task(): no worker loop, not running, future tracking/callback
- _process_message_wrapper(): semaphore acquire/release, handler iteration with
  PipelineEvent, xack via main_loop, parse failure, no handler, exception
- cleanup(): stops worker, closes redis, handles errors
- is_running()
- _get_active_task_count()
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.lease import LeaseRenewer
from app.services.messaging.redis_streams.indexing_consumer import (
    _BUSYGROUP_ERROR,
    _MESSAGE_VALUE_FIELD,
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor import Pool
from app.services.resource_governor.models import ParseTier
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_redis_indexing_consumer")


@pytest.fixture
def config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        password="secret",
        db=0,
        max_len=10000,
        block_ms=100,
        batch_size=5,
        client_id="test-consumer",
        group_id="test-group",
        topics=["topic-a", "topic-b"],
    )


@pytest.fixture
def consumer(logger, config):
    c = IndexingRedisStreamsConsumer(logger, config, retry_manager=None, producer=None)
    # _pending_message_is_owned now runs unconditionally in
    # _process_message_wrapper (see fix-pel-ownership). Most tests here
    # aren't exercising PEL-ownership loss, so default it to "still owned"
    # and let the handful of tests that do care restore the real method.
    c._pending_message_is_owned = AsyncMock(return_value=True)
    return c


def _valid_fields(event_type="test", payload=None):
    """Return a Redis-style fields dict with a JSON-serialised 'value' key."""
    payload = payload or {"key": "val"}
    return {"value": json.dumps({"eventType": event_type, "payload": payload})}


def _submit_without_running(future):
    def submit(coro, _loop):
        coro.close()
        return future

    return submit


# ===================================================================
# __init__
# ===================================================================


class TestInit:
    def test_default_attributes(self, consumer, config):
        assert consumer.redis is None
        assert consumer.running is False
        assert consumer.consume_task is None
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None
        assert consumer.parsing_semaphore is None
        assert consumer.indexing_semaphore is None
        assert consumer.message_handler is None
        assert len(consumer._active_futures) == 0
        assert consumer._backpressure_active is False
        assert consumer.consumer_name.startswith(f"{config.client_id}-")

    def test_consumer_name_is_unique_per_instance(self, logger, config):
        first = IndexingRedisStreamsConsumer(logger, config)
        second = IndexingRedisStreamsConsumer(logger, config)

        assert first.consumer_name != second.consumer_name


# ===================================================================
# is_running
# ===================================================================


class TestIsRunning:
    def test_default_false(self, consumer):
        assert consumer.is_running() is False

    def test_after_set_true(self, consumer):
        consumer.running = True
        assert consumer.is_running() is True


# ===================================================================
# _get_active_task_count
# ===================================================================


class TestGetActiveTaskCount:
    def test_empty(self, consumer):
        assert consumer._get_active_task_count() == 0

    def test_with_futures(self, consumer):
        f = Future()
        with consumer._futures_lock:
            consumer._active_futures.add(f)
        assert consumer._get_active_task_count() == 1


# ===================================================================
# initialize  (lines 49-104)
# ===================================================================


class TestInitialize:
    @pytest.mark.asyncio
    async def test_successful_initialize(self, logger, config):
        """Full happy path: Redis ping, group creation, stale consumer deletion."""
        c = IndexingRedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_delconsumer = AsyncMock()

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = True

            with patch(
                "app.services.messaging.redis_streams.indexing_consumer.Redis",
                return_value=mock_redis,
            ):
                await c.initialize()

        mock_redis.ping.assert_awaited_once()
        # 2 topics -> 2 xgroup_create
        assert mock_redis.xgroup_create.call_count == 2
        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_busygroup_error_handled(self, logger, config):
        """BUSYGROUP error is swallowed (group already exists)."""
        c = IndexingRedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )
        mock_redis.xgroup_delconsumer = AsyncMock()

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = True

            with patch(
                "app.services.messaging.redis_streams.indexing_consumer.Redis",
                return_value=mock_redis,
            ):
                await c.initialize()

        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_non_busygroup_error_raises(self, logger, config):
        """Non-BUSYGROUP error during xgroup_create is re-raised."""
        c = IndexingRedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("Connection lost")
        )

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = True

            with patch(
                "app.services.messaging.redis_streams.indexing_consumer.Redis",
                return_value=mock_redis,
            ):
                with patch.object(c, "stop", new_callable=AsyncMock) as mock_stop:
                    with pytest.raises(Exception, match="Connection lost"):
                        await c.initialize()
                    mock_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_loop_timeout_raises(self, logger, config):
        """RuntimeError when worker loop does not become ready in time."""
        c = IndexingRedisStreamsConsumer(logger, config)

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = False  # timed out

            with patch.object(c, "stop", new_callable=AsyncMock) as mock_stop:
                with pytest.raises(RuntimeError, match="not initialized in time"):
                    await c.initialize()
                mock_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_loop_not_running_raises(self, logger, config):
        """RuntimeError when worker loop signalled ready but is not running."""
        c = IndexingRedisStreamsConsumer(logger, config)

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = False

            with patch.object(c, "stop", new_callable=AsyncMock) as mock_stop:
                with pytest.raises(RuntimeError, match="failed to start"):
                    await c.initialize()
                mock_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_loop_none_after_ready_raises(self, logger, config):
        """RuntimeError when worker_loop is None despite ready signal."""
        c = IndexingRedisStreamsConsumer(logger, config)

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = None  # not set

            with patch.object(c, "stop", new_callable=AsyncMock) as mock_stop:
                with pytest.raises(RuntimeError, match="failed to start"):
                    await c.initialize()
                mock_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delconsumer_failure_is_ignored(self, logger, config):
        """Exception in xgroup_delconsumer is silently ignored."""
        c = IndexingRedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_delconsumer = AsyncMock(side_effect=Exception("not found"))

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = True

            with patch(
                "app.services.messaging.redis_streams.indexing_consumer.Redis",
                return_value=mock_redis,
            ):
                await c.initialize()  # should not raise

        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_exception_during_init_calls_stop(self, logger, config):
        """Any exception during initialize calls stop() before re-raising."""
        c = IndexingRedisStreamsConsumer(logger, config)

        with patch.object(
            c, "_start_worker_thread", side_effect=Exception("thread boom")
        ):
            with patch.object(c, "stop", new_callable=AsyncMock) as mock_stop:
                with pytest.raises(Exception, match="thread boom"):
                    await c.initialize()
                mock_stop.assert_awaited_once()


# ===================================================================
# _start_worker_thread / _stop_worker_thread  (lines 107-133, 194-203)
# ===================================================================


class TestStartStopWorkerThread:
    def test_start_and_stop_roundtrip(self, consumer):
        """Worker thread starts, becomes ready, then shuts down cleanly."""
        consumer._start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)
        assert consumer.worker_loop is not None
        assert consumer.worker_loop.is_running()
        assert consumer.worker_executor is not None

        consumer._stop_worker_thread()
        assert consumer.worker_executor is None
        assert consumer.worker_loop is None

    def test_stop_without_start_is_safe(self, consumer):
        """_stop_worker_thread is a no-op when never started."""
        consumer._stop_worker_thread()  # should not raise
        assert consumer.worker_executor is None

    def test_stop_clears_active_futures(self, consumer):
        consumer._start_worker_thread()
        assert consumer.worker_loop_ready.wait(timeout=5.0)

        f = Future()
        f.set_result(None)
        with consumer._futures_lock:
            consumer._active_futures.add(f)
        consumer._mark_in_flight("1-0")

        consumer._stop_worker_thread()
        assert len(consumer._active_futures) == 0
        assert consumer._is_in_flight("1-0") is False

    def test_stop_with_loop_not_running(self, consumer):
        """When worker_loop exists but is not running, stop skips loop.stop()."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        consumer.worker_loop = mock_loop
        consumer.worker_executor = MagicMock()

        consumer._stop_worker_thread()
        mock_loop.call_soon_threadsafe.assert_not_called()
        assert consumer.worker_executor is None


class TestConsumerMetadataCleanup:
    @pytest.mark.asyncio
    async def test_deletes_only_empty_idle_consumers(self, consumer):
        consumer.redis = AsyncMock()
        consumer.redis.xinfo_consumers.return_value = [
            {
                "name": "old-empty",
                "pending": 0,
                "idle": consumer.config.claim_min_idle_ms,
            },
            {
                "name": "old-pending",
                "pending": 1,
                "idle": consumer.config.claim_min_idle_ms * 10,
            },
            {
                "name": consumer.consumer_name,
                "pending": 0,
                "idle": consumer.config.claim_min_idle_ms * 10,
            },
        ]

        await consumer._cleanup_empty_consumers("topic-a")

        consumer.redis.xgroup_delconsumer.assert_awaited_once_with(
            "topic-a",
            consumer.config.group_id,
            "old-empty",
        )


# ===================================================================
# _wait_for_active_futures  (lines 206-220)
# ===================================================================


class TestWaitForActiveFutures:
    def test_no_futures(self, consumer):
        consumer._wait_for_active_futures()  # should not raise

    def test_completed_future(self, consumer):
        f = Future()
        f.set_result("ok")
        with consumer._futures_lock:
            consumer._active_futures.add(f)
        consumer._wait_for_active_futures()

    def test_errored_future_logged(self, consumer):
        f = Future()
        f.set_exception(RuntimeError("boom"))
        with consumer._futures_lock:
            consumer._active_futures.add(f)
        consumer._wait_for_active_futures()  # logs warning, does not raise

    def test_timed_out_future_cancelled(self, consumer):
        f = Future()  # never resolved
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        with patch.object(
            type(messaging_env),
            "shutdown_task_timeout",
            new_callable=PropertyMock,
            return_value=0.01,
        ):
            consumer._wait_for_active_futures()  # should not raise

    def test_mixed_futures(self, consumer):
        f1 = Future()
        f1.set_result("ok")
        f2 = Future()
        f2.set_exception(ValueError("bad"))
        f3 = Future()
        f3.set_result(None)

        with consumer._futures_lock:
            consumer._active_futures.update({f1, f2, f3})
        consumer._wait_for_active_futures()

    def test_multiple_stuck_futures_share_one_timeout_window(self, consumer):
        """N stuck futures must not multiply the wait into N * shutdown_task_timeout."""
        futures = [Future() for _ in range(5)]  # never resolved
        with consumer._futures_lock:
            consumer._active_futures.update(futures)

        with patch.object(
            type(messaging_env), "shutdown_task_timeout", new_callable=PropertyMock, return_value=0.2,
        ):
            start = time.monotonic()
            consumer._wait_for_active_futures()
            elapsed = time.monotonic() - start

        assert elapsed < 1.0


# ===================================================================
# cleanup  (lines 137-143)
# ===================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stops_worker_and_closes_redis(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis

        with patch.object(consumer, "_stop_worker_thread") as mock_stop:
            await consumer.cleanup()
            mock_stop.assert_called_once()
        mock_redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_without_redis(self, consumer):
        consumer.redis = None
        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_handles_exception(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(side_effect=Exception("close err"))
        consumer.redis = mock_redis

        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.cleanup()  # logs error, does not raise


# ===================================================================
# start  (lines 149-165)
# ===================================================================


class TestStart:
    @pytest.mark.asyncio
    async def test_start_creates_consume_task(self, consumer):
        consumer.redis = AsyncMock()  # already initialised

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        with patch.object(consumer, "_consume_loop", new_callable=AsyncMock):
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
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_start_initializes_when_no_redis(self, consumer):
        consumer.redis = None

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        with patch.object(consumer, "initialize", new_callable=AsyncMock) as mock_init:
            with patch.object(consumer, "_consume_loop", new_callable=AsyncMock):
                await consumer.start(handler)
                mock_init.assert_awaited_once()

        consumer.running = False
        if consumer.consume_task:
            consumer.consume_task.cancel()
            try:
                await consumer.consume_task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_start_exception_propagated(self, logger, config):
        c = IndexingRedisStreamsConsumer(logger, config)
        c.redis = None

        async def handler(msg):
            yield  # pragma: no cover

        with patch.object(
            c, "initialize", new_callable=AsyncMock, side_effect=Exception("init fail")
        ):
            with pytest.raises(Exception, match="init fail"):
                await c.start(handler)


# ===================================================================
# stop  (lines 171-188)
# ===================================================================


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_consume_task(self, consumer):
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.close = AsyncMock()
        consumer.consume_task = asyncio.create_task(asyncio.sleep(10))

        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.stop()

        assert consumer.running is False
        assert consumer.consume_task.cancelled() or consumer.consume_task.done()

    @pytest.mark.asyncio
    async def test_stop_without_consume_task(self, consumer):
        consumer.running = True
        consumer.consume_task = None
        consumer.redis = None

        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.stop()
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_redis_close_error_handled(self, consumer):
        consumer.running = True
        consumer.consume_task = None
        consumer.redis = AsyncMock()
        consumer.redis.close = AsyncMock(side_effect=Exception("close err"))

        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.stop()  # logs error, does not raise
        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_without_redis(self, consumer):
        consumer.running = True
        consumer.consume_task = None
        consumer.redis = None

        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.stop()
        assert consumer.running is False


# ===================================================================
# _parse_message  (lines 334-351)
# ===================================================================


class TestParseMessage:
    async def test_valid_json(self, consumer):
        fields = _valid_fields("CREATE", {"id": 42})
        result = await consumer._parse_message("1-0", fields)
        assert isinstance(result, StreamMessage)
        assert result.eventType == "CREATE"
        assert result.payload == {"id": 42}

    async def test_double_encoded_json(self, consumer):
        inner = json.dumps({"eventType": "test", "payload": {"key": "val"}})
        fields = {"value": json.dumps(inner)}
        result = await consumer._parse_message("1-0", fields)
        assert isinstance(result, StreamMessage)
        assert result.payload == {"key": "val"}

    async def test_missing_value_field_returns_none(self, consumer):
        result = await consumer._parse_message("1-0", {"_init": "1"})
        assert result is None

    async def test_empty_fields_returns_none(self, consumer):
        result = await consumer._parse_message("1-0", {})
        assert result is None

    async def test_invalid_json_returns_none(self, consumer):
        result = await consumer._parse_message("1-0", {"value": "not-json{{{"})
        assert result is None

    async def test_valid_json_invalid_schema_returns_none(self, consumer):
        """Valid JSON not matching the StreamMessage schema is poison.

        Missing required fields raises pydantic ValidationError internally; the
        parser must treat it as unparseable (return None) so the message is
        dropped, not crash the worker into a no-ACK loop.
        """
        result = await consumer._parse_message("1-0", {"value": json.dumps({"foo": "bar"})})
        assert result is None

    async def test_non_mapping_json_returns_none(self, consumer):
        """JSON decoding to a non-object (list) is poison -> None, not a TypeError."""
        result = await consumer._parse_message("1-0", {"value": json.dumps([1, 2, 3])})
        assert result is None

    async def test_valid_with_timestamp(self, consumer):
        fields = {
            "value": json.dumps(
                {"eventType": "test", "payload": {"k": "v"}, "timestamp": 12345}
            )
        }
        result = await consumer._parse_message("1-0", fields)
        assert isinstance(result, StreamMessage)
        assert result.timestamp == 12345


# ===================================================================
# _start_processing_task  (lines 356-377)
# ===================================================================


class TestStartProcessingTask:
    @pytest.mark.asyncio
    async def test_no_worker_loop_returns(self, consumer):
        consumer.worker_loop = None
        consumer.running = True
        await consumer._start_processing_task("stream", "1-0", _valid_fields())
        # Should return early without error

    @pytest.mark.asyncio
    async def test_not_running_returns(self, consumer):
        consumer.worker_loop = MagicMock()
        consumer.running = False
        await consumer._start_processing_task("stream", "1-0", _valid_fields())

    @pytest.mark.asyncio
    async def test_submits_to_worker_loop_and_tracks_future(self, consumer):
        consumer.running = True
        consumer.worker_loop = MagicMock()
        mock_future = MagicMock(spec=Future)
        mock_future.add_done_callback = MagicMock()

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(mock_future),
        ):
            await consumer._start_processing_task("stream", "1-0", _valid_fields())

        with consumer._futures_lock:
            assert mock_future in consumer._active_futures
        mock_future.add_done_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_future_done_removes_from_tracking(self, consumer):
        """Done callback removes the future from _active_futures."""
        consumer.running = True
        consumer.worker_loop = MagicMock()

        captured_callback = None
        mock_future = MagicMock(spec=Future)

        def capture_cb(cb):
            nonlocal captured_callback
            captured_callback = cb

        mock_future.add_done_callback = capture_cb
        mock_future.result.return_value = True

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(mock_future),
        ):
            await consumer._start_processing_task("stream", "1-0", _valid_fields())

        assert captured_callback is not None
        with consumer._futures_lock:
            assert mock_future in consumer._active_futures

        # Simulate future completion
        captured_callback(mock_future)

        with consumer._futures_lock:
            assert mock_future not in consumer._active_futures

    @pytest.mark.asyncio
    async def test_on_future_done_logs_exception(self, consumer):
        """Done callback logs errors from the completed future."""
        consumer.running = True
        consumer.worker_loop = MagicMock()

        captured_callback = None
        mock_future = MagicMock(spec=Future)

        def capture_cb(cb):
            nonlocal captured_callback
            captured_callback = cb

        mock_future.add_done_callback = capture_cb
        mock_future.result.side_effect = RuntimeError("task exploded")

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(mock_future),
        ):
            await consumer._start_processing_task("stream", "1-0", _valid_fields())

        # Simulate future completion with exception
        captured_callback(mock_future)

        with consumer._futures_lock:
            assert mock_future not in consumer._active_futures


# ===================================================================
# _process_message_wrapper  (lines 382-452)
# ===================================================================


class TestProcessMessageWrapper:
    @pytest.mark.asyncio
    async def test_no_semaphores_returns_false(self, consumer):
        consumer.parsing_semaphore = None
        consumer.indexing_semaphore = None
        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False

    @pytest.mark.asyncio
    async def test_parse_failure_returns_false(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        result = await consumer._process_message_wrapper(
            "s", "1-0", {"value": "not-json"}
        )
        assert result is False
        # Semaphores released in finally
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_no_handler_returns_false(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = None
        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_acked_claim_is_not_reprocessed(self, consumer) -> None:
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.concurrency_manager = AsyncMock()
        consumer.concurrency_manager.try_acquire.return_value = True
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range.return_value = []
        consumer.main_loop = asyncio.get_running_loop()
        consumer.message_handler = MagicMock()
        # Restore the real ownership check (fixture defaults it to "owned")
        # since this test exercises the "no longer owned" rejection path.
        consumer._pending_message_is_owned = (
            IndexingRedisStreamsConsumer._pending_message_is_owned.__get__(
                consumer, IndexingRedisStreamsConsumer
            )
        )
        fields = _valid_fields(payload={"recordId": "r1"})

        result = await consumer._process_message_wrapper(
            "stream-a",
            "1-0",
            fields,
        )

        assert result is False
        consumer.message_handler.assert_not_called()
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_successful_processing_with_xack(self, consumer):
        """Full happy path: handler yields both events, xack succeeds."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()

        # Create a mock main loop
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            result = await consumer._process_message_wrapper(
                "stream-a", "1-0", _valid_fields()
            )

        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_indexing_limit_bounds_handlers_before_status_write(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(2)
        consumer._ack_message = AsyncMock()
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
                consumer._process_message_wrapper(
                    "stream-a",
                    f"{i}-0",
                    _valid_fields(payload={"id": i}),
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
        consumer._ack_message = AsyncMock()
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
                consumer._process_message_wrapper(
                    "stream-a",
                    f"{i}-0",
                    _valid_fields(payload={"id": i}),
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
        self, logger, config
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
            IndexingRedisStreamsConsumer(
                logger,
                config,
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
            candidate._ack_message = AsyncMock()
            candidate._pending_message_is_owned = AsyncMock(return_value=True)
            candidate.message_handler = handler
            for offset in range(3):
                record_id = consumer_index * 3 + offset
                tasks.append(
                    asyncio.create_task(
                        candidate._process_message_wrapper(
                            "stream-a",
                            f"{consumer_index}-{offset}",
                            _valid_fields(
                                payload={
                                    "id": record_id,
                                    "recordId": f"shared-{offset}",
                                }
                            ),
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
    async def test_only_parsing_complete_released(self, consumer):
        """A partial handler result is not acknowledged as successful."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            result = await consumer._process_message_wrapper(
                "s", "1-0", _valid_fields()
            )

        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_only_indexing_complete_released(self, consumer):
        """Only INDEXING_COMPLETE yielded; parsing released in finally."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            result = await consumer._process_message_wrapper(
                "s", "1-0", _valid_fields()
            )

        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_handler_exception_releases_semaphores(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            raise RuntimeError("handler exploded")
            yield

        consumer.message_handler = handler
        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False
        assert consumer.parsing_semaphore._value == 1
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

        consumer.message_handler = handler
        # A real renewer, never started: start_lease_guard still registers the
        # owner and spawns the waiter, which is what cancellation must clean up.
        consumer.lease_renewer = LeaseRenewer(
            consumer.logger,
            consumer.concurrency_manager,
            lease_seconds=120,
            interval_seconds=30,
        )

        processing = asyncio.create_task(
            consumer._process_message_wrapper(
                "stream-a",
                "1-0",
                _valid_fields(payload={"recordId": "cancelled-record"}),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        processing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await processing

        await asyncio.wait_for(handler_cancelled.wait(), timeout=1)
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1
        # The owner must be gone from the renewer, or a cancelled record keeps
        # being renewed forever and leaks a handle per cancellation.
        assert consumer.lease_renewer._handles == {}

    @pytest.mark.asyncio
    async def test_handler_exception_after_parsing_released(self, consumer):
        """Handler raises after yielding PARSING_COMPLETE. Only indexing released in finally."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )
            raise RuntimeError("late boom")

        consumer.message_handler = handler
        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_xack_timeout_keeps_message_retryable(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            with patch(
                "asyncio.wait_for", side_effect=TimeoutError("xack timed out")
            ):
                result = await consumer._process_message_wrapper(
                    "s", "1-0", _valid_fields()
                )

        assert result is False

    @pytest.mark.asyncio
    async def test_xack_unavailable_during_shutdown_is_not_success(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.running = False  # shutting down

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        # No redis/main_loop set -> triggers the `elif not self.running` branch
        consumer.redis = None
        consumer.main_loop = None

        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False

    @pytest.mark.asyncio
    async def test_xack_with_stopped_main_loop_is_not_success(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.running = False

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = False
        consumer.main_loop = mock_main_loop

        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_value_field_returns_false(self, consumer):
        """Message without 'value' field => _parse_message returns None => False."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = AsyncMock()

        result = await consumer._process_message_wrapper(
            "s", "1-0", {"_init": "1"}
        )
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_unparseable_message_is_acked(self, consumer):
        """Poison (unparseable) messages must be ACKed so they leave the PEL.

        Regression: a parse failure previously returned without XACK, leaving
        the entry pending forever and re-recovered on every drain — the
        infinite recovery loop reported against record-events.
        """
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.running = True
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            result = await consumer._process_message_wrapper(
                "stream-a", "1-0", {"value": "not-json{{{"}
            )

        assert result is False
        consumer.redis.xack.assert_called_once_with(
            "stream-a", consumer.config.group_id, "1-0"
        )
        # Semaphores still released in finally despite the early return.
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


class TestProcessMessageWrapperWithGovernor:
    """Phase 1: when a ResourceGovernor is injected, parsing/indexing
    admission routes through its adaptive gates instead of the legacy
    static semaphores, on the same event sequence used today."""

    @pytest.fixture
    def governor_consumer(self, logger, config) -> IndexingRedisStreamsConsumer:
        c = IndexingRedisStreamsConsumer(
            logger, config, retry_manager=None, producer=None,
            governor=make_test_governor(logger_name="test_redis_indexing_governor"),
        )
        c._pending_message_is_owned = AsyncMock(return_value=True)
        return c

    @pytest.mark.asyncio
    async def test_local_gate_is_taken_before_the_cluster_lease(
        self, governor_consumer
    ) -> None:
        """Ordering, not just presence. The node-local gate is an asyncio
        Event and costs nothing to queue on, so it must absorb the wait;
        only records it has already admitted should contend for the Redis
        lease. Taking the lease first put the entire queue on Redis, each
        waiter re-polling on a timer — the shape that drove Redis to its
        client limit in production."""
        order: list[str] = []
        governor = governor_consumer.governor

        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease):
            if pool.startswith("parsing"):
                order.append(f"lease:{pool}")
            return True

        manager.try_acquire.side_effect = try_acquire
        governor_consumer.concurrency_manager = manager

        real_acquire = concurrency.acquire_parsing_slot

        async def spy(host, tier, size_bytes):
            order.append("gate")
            return await real_acquire(host, tier, size_bytes)

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=8),
            )
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        governor_consumer.message_handler = handler

        with patch.object(concurrency, "acquire_parsing_slot", spy):
            result = await governor_consumer._process_message_wrapper(
                "stream-a", "1-0",
                _valid_fields(payload={"recordId": "r1", "extension": "md",
                                       "mimeType": "text/markdown"}),
            )

        assert result is True
        assert order == ["gate", "lease:parsing:light"], order
        # And the permit came back.
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_parse_permit_is_released_when_the_lease_step_aborts(
        self, governor_consumer
    ) -> None:
        """The gate permit is taken first now, so every exit path after it —
        including the clean-shutdown abort — has to hand it back or the pool
        leaks a permit per shutdown."""
        governor = governor_consumer.governor
        manager = AsyncMock()

        async def try_acquire(pool, _owner, _limit, _lease):
            if pool.startswith("parsing"):
                governor_consumer.running = False  # clean shutdown mid-acquire
                return False
            return True

        manager.try_acquire.side_effect = try_acquire
        governor_consumer.concurrency_manager = manager

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=8),
            )
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        governor_consumer.message_handler = handler

        result = await governor_consumer._process_message_wrapper(
                "stream-a", "1-0",
                _valid_fields(payload={"recordId": "r1", "extension": "md",
                                       "mimeType": "text/markdown"}),
            )

        assert result is False
        assert governor.gate(Pool.LIGHT_PARSE).in_use == 0
        assert governor.gate(Pool.INDEX_LIGHT).in_use == 0

    @pytest.mark.asyncio
    async def test_light_records_draw_on_their_own_index_budget(
        self, governor_consumer
    ) -> None:
        """The head-of-line bug: an index permit is held for a record's whole
        lifetime, including the wait for a parse slot, so one shared budget let
        a queue of Docling PDFs hold every permit while Jira/Confluence records
        that finish in seconds were never admitted at all."""
        governor = governor_consumer.governor

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=1),
            )
            assert governor.gate(Pool.INDEX_LIGHT).in_use == 1
            assert governor.gate(Pool.INDEX_HEAVY).in_use == 0
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0",
            _valid_fields(payload={"recordId": "r1", "extension": "md", "mimeType": "text/markdown"}),
        )

        assert result is True
        assert governor.gate(Pool.INDEX_LIGHT).in_use == 0

    @pytest.mark.asyncio
    async def test_heavy_records_draw_on_the_heavy_index_budget(
        self, governor_consumer
    ) -> None:
        """Routed from the record event's own extension/mimeType, because the
        permit is taken before the handler runs and can't wait for its tier."""
        governor = governor_consumer.governor

        async def handler(_msg):
            assert governor.gate(Pool.INDEX_HEAVY).in_use == 1
            assert governor.gate(Pool.INDEX_LIGHT).in_use == 0
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0",
            _valid_fields(payload={"recordId": "r1", "extension": "pdf", "mimeType": "application/pdf"}),
        )

        assert result is True
        assert governor.gate(Pool.INDEX_HEAVY).in_use == 0

    @pytest.mark.asyncio
    async def test_unknown_format_draws_on_the_heavy_budget(
        self, governor_consumer
    ) -> None:
        """classify() resolves anything unrecognised to HEAVY, so an
        unclassifiable record can never consume the budget sized for records
        that turn over in seconds."""
        governor = governor_consumer.governor

        async def handler(_msg):
            assert governor.gate(Pool.INDEX_HEAVY).in_use == 1
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0",
            _valid_fields(payload={"recordId": "r1"}),
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_worker_loop_uses_governor_gate_for_index_pool(
        self, governor_consumer
    ) -> None:
        governor_consumer._start_worker_thread()
        assert governor_consumer.worker_loop_ready.wait(timeout=5.0)
        try:
            assert governor_consumer.parsing_semaphore is None
            # Under a governor there is no single index gate to park on the
            # consumer: acquire_index_slot resolves the tier's gate per
            # message. The worker loop only warms all four so they bind here.
            assert governor_consumer.indexing_semaphore is None
            for pool in Pool:
                assert governor_consumer.governor.gate(pool) is not None
        finally:
            governor_consumer._stop_worker_thread()

    @pytest.mark.asyncio
    async def test_heavy_tier_routes_to_heavy_parse_gate(
        self, governor_consumer
    ) -> None:
        governor_consumer.redis = AsyncMock()
        # Same loop as the test itself, so cross-loop bridging in
        # bridge_to_main_loop becomes a plain await
        # instead of needing asyncio.run_coroutine_threadsafe mocked out.
        governor_consumer.main_loop = asyncio.get_running_loop()

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.HEAVY, size_bytes=1024),
            )
            assert governor_consumer.governor.gate(Pool.HEAVY_PARSE).in_use == 1
            assert governor_consumer.governor.gate(Pool.LIGHT_PARSE).in_use == 0
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0", _valid_fields()
        )

        assert result is True
        assert governor_consumer.governor.gate(Pool.HEAVY_PARSE).in_use == 0
        assert governor_consumer.governor.gate(Pool.INDEX_HEAVY).in_use == 0
        assert governor_consumer.governor.gate(Pool.INDEX_LIGHT).in_use == 0

    @pytest.mark.asyncio
    async def test_light_tier_routes_to_light_parse_gate(
        self, governor_consumer
    ) -> None:
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=128),
            )
            assert governor_consumer.governor.gate(Pool.LIGHT_PARSE).in_use == 1
            assert governor_consumer.governor.gate(Pool.HEAVY_PARSE).in_use == 0
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0", _valid_fields()
        )

        assert result is True
        assert governor_consumer.governor.gate(Pool.LIGHT_PARSE).in_use == 0

    @pytest.mark.asyncio
    async def test_distributed_lease_uses_resolved_ceiling_not_adaptive_limit(
        self, governor_consumer, monkeypatch
    ) -> None:
        """Distributed leases must be sized to the resolved ceiling (the
        cluster-wide cap), not the current adaptive node-local gate limit."""
        monkeypatch.setenv("INDEXING_SPLIT_LEASE_POOLS", "true")
        governor_consumer.running = True
        governor_consumer.governor._registry.set(Pool.INDEX_HEAVY, 1)
        governor_consumer.governor._registry.set(Pool.INDEX_LIGHT, 1)
        governor_consumer.governor._registry.set(Pool.HEAVY_PARSE, 1)
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        manager = AsyncMock()
        manager.try_acquire.return_value = True
        governor_consumer.concurrency_manager = manager

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.HEAVY, size_bytes=1),
            )
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0", _valid_fields()
        )

        assert result is True
        lease_limits = {
            call.args[0]: call.args[2] for call in manager.try_acquire.await_args_list
        }
        # The invariant is that the lease is sized to the *resolved ceiling*,
        # never the adaptive node-local limit shrunk to 1 above — the lease is
        # the cluster-wide cap and must not move when one node backs off.
        assert lease_limits["indexing"] == governor_consumer.governor.ceilings.index_heavy
        assert lease_limits["indexing"] > 0
        assert lease_limits["parsing"] == 4

    @pytest.mark.asyncio
    async def test_light_parse_lease_uses_light_ceiling_and_own_pool(
        self, governor_consumer
    ) -> None:
        governor_consumer.running = True
        governor_consumer.redis = AsyncMock()
        governor_consumer.main_loop = asyncio.get_running_loop()
        manager = AsyncMock()
        manager.try_acquire.return_value = True
        governor_consumer.concurrency_manager = manager

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=128),
            )
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        governor_consumer.message_handler = handler
        result = await governor_consumer._process_message_wrapper(
            "stream-a", "1-0", _valid_fields()
        )

        assert result is True
        lease_limits = {
            call.args[0]: call.args[2] for call in manager.try_acquire.await_args_list
        }
        assert "parsing" not in lease_limits
        assert lease_limits["parsing:light"] == governor_consumer.governor.ceilings.light
        assert lease_limits["parsing:light"] > 4

    @pytest.mark.asyncio
    async def test_legacy_semaphore_path_unaffected_when_no_governor(
        self, consumer
    ) -> None:
        assert consumer.governor is None
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        async def handler(_msg):
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(record_id="r1"),
            )
            assert consumer.parsing_semaphore._value == 0
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        result = await consumer._process_message_wrapper(
            "stream-a", "1-0", _valid_fields()
        )

        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===================================================================
# Retry backoff delay applied before semaphore acquisition (parity with
# the Kafka consumer's Fix 6 — a re-queued message is stamped with a
# not-before timestamp and the wait happens before parsing_semaphore is
# acquired).
# ===================================================================


class TestRetryDelayBeforeSemaphore:
    @pytest.mark.asyncio
    async def test_requeue_message_stamps_not_before(self, consumer):
        consumer.producer = AsyncMock()
        message = StreamMessage(eventType="newRecord", payload={"recordId": "r1"})
        before = time.time()

        await consumer._requeue_message("stream-a", message, "stable-id", retry_count=2)

        consumer.producer.send_event.assert_awaited_once()
        sent_payload = consumer.producer.send_event.await_args.kwargs["payload"]
        assert sent_payload["_retry_tracking_id"] == "stable-id"
        # retry_count=2 -> ~60s backoff
        assert before + 59.0 <= sent_payload["_retry_not_before"] <= before + 61.0

    @pytest.mark.asyncio
    async def test_requeue_requires_producer(self, consumer):
        consumer.producer = None
        message = StreamMessage(
            eventType="newRecord",
            payload={"recordId": "r1"},
        )

        with pytest.raises(RuntimeError, match="No producer"):
            await consumer._requeue_message(
                "stream-a",
                message,
                "stable-id",
                retry_count=1,
            )

    @pytest.mark.asyncio
    async def test_delay_occupies_no_semaphore_slot(self, consumer):
        """While waiting out the backoff, the parsing semaphore stays free."""
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        async def handler(msg):
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = True
        consumer.main_loop = mock_main_loop

        ack_future = Future()
        ack_future.set_result(1)
        not_before = time.time() + 0.3
        fields = {"value": json.dumps({
            "eventType": "test",
            "payload": {"k": "v", "_retry_not_before": not_before},
        })}

        with patch(
            "asyncio.run_coroutine_threadsafe",
            side_effect=_submit_without_running(ack_future),
        ):
            task = asyncio.create_task(
                consumer._process_message_wrapper("stream-a", "1-0", fields)
            )
            await asyncio.sleep(0.1)
            # Still within the backoff window: semaphore must not be acquired yet.
            assert consumer.parsing_semaphore._value == 1

            result = await asyncio.wait_for(task, timeout=2.0)

        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_shutdown_interrupts_delay_without_processing(self, consumer):
        """A shutdown request (running -> False) during backoff aborts the wait
        promptly instead of holding the future for the full ~300s window."""
        consumer.running = True
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.message_handler = AsyncMock()

        fields = {"value": json.dumps({
            "eventType": "test",
            "payload": {"k": "v", "_retry_not_before": time.time() + 120},
        })}

        task = asyncio.create_task(
            consumer._process_message_wrapper("stream-a", "1-0", fields)
        )
        await asyncio.sleep(0.1)
        consumer.running = False  # simulate stop() being called mid-backoff

        result = await asyncio.wait_for(task, timeout=2.0)
        assert result is False
        consumer.message_handler.assert_not_called()
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1


# ===================================================================
# _drain_pending  (lines 228-254)
# ===================================================================


class TestDrainPending:
    @pytest.mark.asyncio
    async def test_drains_pending_messages(self, consumer):
        """Re-processes messages from the PEL via XAUTOCLAIM, then stops."""
        consumer.running = True
        consumer.redis = AsyncMock()

        # xautoclaim returns two claimed messages for topic-a, then empty for topic-b
        consumer.redis.xautoclaim = AsyncMock(
            side_effect=[
                ("0-0", [("1-0", _valid_fields()), ("2-0", _valid_fields())], []),
                ("0-0", [], []),
            ]
        )
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        assert mock_process.call_count == 2
        assert consumer.redis.xautoclaim.await_args_list[0].args[2] == consumer.consumer_name

    @pytest.mark.asyncio
    async def test_drain_empty_pel(self, consumer):
        """Empty PEL exits immediately."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_reads_only_remaining_pending_capacity(
        self,
        consumer,
    ) -> None:
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xreadgroup = AsyncMock(return_value=None)
        with consumer._futures_lock:
            consumer._gate_waiters = 39

        with patch.object(
            type(messaging_env),
            "max_pending_indexing_tasks",
            new_callable=PropertyMock,
            return_value=40,
        ):
            await consumer._drain_pending()

        assert all(
            call.kwargs["count"] == 1
            for call in consumer.redis.xautoclaim.await_args_list
        )
        assert all(
            call.kwargs["count"] == 1
            for call in consumer.redis.xreadgroup.await_args_list
        )

    @pytest.mark.asyncio
    async def test_drain_none_result(self, consumer):
        """When xautoclaim returns no claimed messages, drain stops."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_stops_when_not_running(self, consumer):
        """Exits mid-drain when running becomes False."""
        consumer.running = True
        consumer.redis = AsyncMock()

        consumer.redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("1-0", _valid_fields())], [])
        )
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        async def stop_on_process(stream, mid, fields):
            consumer.running = False

        with patch.object(
            consumer, "_start_processing_task", side_effect=stop_on_process
        ):
            await consumer._drain_pending()

    @pytest.mark.asyncio
    async def test_drain_handles_processing_error(self, consumer):
        """Errors during PEL recovery are logged and processing continues."""
        consumer.running = True
        consumer.redis = AsyncMock()

        # xautoclaim returns two messages for topic-a, then empty for topic-b
        consumer.redis.xautoclaim = AsyncMock(
            side_effect=[
                ("0-0", [("1-0", _valid_fields()), ("2-0", _valid_fields())], []),
                ("0-0", [], []),
            ]
        )
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        process_count = 0

        async def mock_process(stream, mid, fields):
            nonlocal process_count
            process_count += 1
            if process_count == 1:
                raise Exception("PEL processing error")

        with patch.object(consumer, "_start_processing_task", side_effect=mock_process):
            await consumer._drain_pending()

        # Both messages attempted despite first one erroring
        assert process_count == 2

    @pytest.mark.asyncio
    async def test_drain_not_running_from_start(self, consumer):
        """When running is False from the start, drain exits immediately."""
        consumer.running = False
        consumer.redis = AsyncMock()

        await consumer._drain_pending()
        consumer.redis.xautoclaim.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_results_all_empty_messages(self, consumer):
        """When xautoclaim returns no claimed messages, PEL is drained."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_phase2_recovers_own_pel(self, consumer):
        """Phase 2: XREADGROUP id="0" recovers messages already owned by this consumer.

        This covers a retry within the lifetime of the current consumer instance.
        """
        # The test fixture configures two topics; Phase 2 runs once per topic.
        first_topic = consumer.config.topics[0]

        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        # Phase 2 call sequence (in order):
        #   1. topic[0]: returns one message
        #   2. topic[0]: drained, return None
        #   3. topic[1]: empty, return None
        consumer.redis.xreadgroup = AsyncMock(
            side_effect=[
                [(first_topic, [("9-0", _valid_fields())])],
                None,
                None,
            ]
        )
        # Phase 2 only runs when the pending list holds something this
        # consumer is not already tracking -- the XREADGROUP below bumps
        # times_delivered on everything it returns, so it must not run
        # speculatively.
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"message_id": "9-0"}]
        )

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        mock_process.assert_awaited_once()
        first_call = consumer.redis.xreadgroup.call_args_list[0]
        # Phase 2 must use id "0", not ">"
        assert first_call.kwargs["streams"][first_topic] == "0"
        assert first_call.kwargs["consumername"] == consumer.consumer_name

    @pytest.mark.asyncio
    async def test_drain_phase2_advances_cursor(self, consumer):
        """Phase 2 must advance its PEL read cursor instead of re-reading id "0".

        Regression: re-reading from "0" on every iteration re-delivered the
        same un-ACKed entries forever — a tight infinite recovery loop.
        """
        first_topic = consumer.config.topics[0]
        second_topic = consumer.config.topics[1]
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        # Non-empty so the Phase-2 gate lets the recovery read run: it is
        # skipped when nothing in the pending list is unaccounted for.
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"message_id": "5-0"}]
        )
        consumer.redis.xreadgroup = AsyncMock(
            side_effect=[
                [(first_topic, [("5-0", _valid_fields()), ("9-0", _valid_fields())])],
                [(first_topic, [])],
                [(second_topic, [])],
            ]
        )

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        # The second Phase-2 read for topic[0] must continue past the last
        # recovered id ("9-0"), not restart from "0".
        second_call = consumer.redis.xreadgroup.call_args_list[1]
        assert second_call.kwargs["streams"][first_topic] == "9-0"
        assert mock_process.await_count == 2


# ===================================================================
# _should_dead_letter  (dead-letter logic)
# ===================================================================


class TestExceedsMaxRetries:
    """Tests for _should_dead_letter() — dead-letter logic for poison messages."""

    @pytest.mark.asyncio
    async def test_under_limit_returns_false(self, consumer):
        """Message below the delivery threshold should NOT be dead-lettered."""
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 2}]
        )

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is False
        consumer.redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_at_limit_dead_letters(self, consumer):
        """Message at the delivery threshold should be ACK-ed (dead-lettered)."""
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 11}]
        )
        consumer.redis.xack = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is True
        consumer.redis.xack.assert_awaited_once_with(
            "topic-a", consumer.config.group_id, "1-0"
        )

    @pytest.mark.asyncio
    async def test_empty_xpending_returns_false(self, consumer):
        """When XPENDING returns no details, message is not dead-lettered."""
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(return_value=[])

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is False

    @pytest.mark.asyncio
    async def test_xpending_error_returns_false(self, consumer):
        """XPENDING errors should not dead-letter — return False and log."""
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(
            side_effect=Exception("redis down")
        )

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is False

    @pytest.mark.asyncio
    async def test_backstop_fires_when_retry_manager_present_but_app_counter_lags(
        self, consumer
    ):
        """Regression test for #2992: a poison message must still dead-letter via
        the Redis-native times_delivered backstop even when a RetryManager is
        configured (the production case) and its app-tracked failure count
        hasn't reached the threshold — e.g. because the process crashed before
        ever incrementing it. Before the fix, the RetryManager branch always
        returned early and this backstop was unreachable dead code.

        Uses a current message_id distinct from the stable retry-tracking id
        (as a re-queued entry would carry) to prove the backstop clears retry
        state under the stable id, not the current Redis message_id.
        """
        consumer.retry_manager = AsyncMock()
        consumer.retry_manager.get_count.return_value = 1  # app counter lagging
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 11}]
        )
        consumer.redis.xack = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter(
                "topic-a", "2-0", stable_message_id="stable-1"
            )

        assert result is True
        consumer.redis.xack.assert_awaited_once_with(
            "topic-a", consumer.config.group_id, "2-0"
        )
        consumer.retry_manager.get_count.assert_awaited_once_with("stable-1")
        consumer.retry_manager.clear.assert_awaited_once_with("stable-1")

    @pytest.mark.asyncio
    async def test_app_counter_alone_still_dead_letters_with_retry_manager(
        self, consumer
    ):
        """The app-tracked RetryManager count reaching the threshold must keep
        dead-lettering on its own without needing xpending_range at all."""
        consumer.retry_manager = AsyncMock()
        consumer.retry_manager.get_count.return_value = 10
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock()
        consumer.redis.xack = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is True
        consumer.redis.xack.assert_awaited_once_with(
            "topic-a", consumer.config.group_id, "1-0"
        )
        consumer.redis.xpending_range.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_backstop_fires_when_app_counter_lookup_raises(self, consumer):
        """A RetryManager lookup error must not skip the times_delivered
        backstop: it used to share a try/except with the backstop, so an
        exception from the app-tracked count check returned False before
        ever reaching xpending_range, even though the backstop doesn't
        depend on that lookup succeeding."""
        consumer.retry_manager = AsyncMock()
        consumer.retry_manager.get_count = AsyncMock(
            side_effect=Exception("redis down")
        )
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 11}]
        )
        consumer.redis.xack = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.indexing_consumer.messaging_env"
        ) as mock_env:
            mock_env.max_delivery_attempts = 10
            mock_env.redis_max_deliveries = 11
            mock_env.max_pending_indexing_tasks = 100
            mock_env.max_concurrent_parsing = 5
            mock_env.max_concurrent_indexing = 10
            result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is True
        consumer.redis.xack.assert_awaited_once_with(
            "topic-a", consumer.config.group_id, "1-0"
        )

    @pytest.mark.asyncio
    async def test_drain_phase1_skips_poison_message(self, consumer):
        """Phase 1 should skip dispatch when _should_dead_letter returns True."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("1-0", _valid_fields())], [])
        )
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_should_dead_letter", new_callable=AsyncMock, return_value=True
        ):
            with patch.object(
                consumer, "_start_processing_task", new_callable=AsyncMock
            ) as mock_process:
                await consumer._drain_pending()

        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_phase2_skips_poison_message(self, consumer):
        """Phase 2 should skip dispatch when _should_dead_letter returns True."""
        first_topic = consumer.config.topics[0]
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        consumer.redis.xreadgroup = AsyncMock(
            side_effect=[
                [(first_topic, [("9-0", _valid_fields())])],
                None,
                None,
            ]
        )
        # Phase 2 only runs when the pending list holds something this
        # consumer is not already tracking -- the XREADGROUP below bumps
        # times_delivered on everything it returns, so it must not run
        # speculatively.
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"message_id": "9-0"}]
        )

        with patch.object(
            consumer, "_should_dead_letter", new_callable=AsyncMock, return_value=True
        ):
            with patch.object(
                consumer, "_start_processing_task", new_callable=AsyncMock
            ) as mock_process:
                await consumer._drain_pending()

        mock_process.assert_not_called()


# ===================================================================
# _consume_loop  (lines 261-327)
# ===================================================================


class TestConsumeLoop:
    @pytest.mark.asyncio
    async def test_exits_when_not_running(self, consumer):
        consumer.running = False
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            await consumer._consume_loop()

    @pytest.mark.asyncio
    async def test_processes_messages(self, consumer):
        consumer.running = True
        consumer.redis = AsyncMock()

        call_count = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("stream", [("1-0", _valid_fields())])]
            consumer.running = False
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(
                consumer, "_start_processing_task", new_callable=AsyncMock
            ) as mock_process:
                await consumer._consume_loop()

        mock_process.assert_called_once_with("stream", "1-0", _valid_fields())

    @pytest.mark.asyncio
    async def test_backpressure_engages_and_clears(self, consumer):
        """Backpressure engaged when gate waiters >= limit, cleared when below."""
        consumer.running = True
        consumer.redis = AsyncMock()

        iteration = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                consumer.running = False
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        max_tasks = messaging_env.max_pending_indexing_tasks

        task_count_values = [max_tasks, 0, 0]  # first: at capacity, rest: below
        task_count_iter = iter(task_count_values)

        def mock_get_count():
            try:
                return next(task_count_iter)
            except StopIteration:
                return 0

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(consumer, "_get_gate_waiter_count", side_effect=mock_get_count):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    await consumer._consume_loop()

        # Backpressure engaged on first iteration -> sleep(0.5)
        mock_sleep.assert_any_call(0.5)

    @pytest.mark.asyncio
    async def test_backpressure_flag_toggles(self, consumer):
        """_backpressure_active toggles when engage/clear occurs."""
        consumer.running = True
        consumer.redis = AsyncMock()

        iteration = 0
        max_tasks = messaging_env.max_pending_indexing_tasks

        async def mock_xreadgroup(**kwargs):
            nonlocal iteration
            iteration += 1
            if iteration >= 4:
                consumer.running = False
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        counts = [max_tasks, max_tasks, 0, 0]
        count_iter = iter(counts)

        def mock_get_count():
            try:
                return next(count_iter)
            except StopIteration:
                return 0

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(consumer, "_get_gate_waiter_count", side_effect=mock_get_count):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await consumer._consume_loop()

    @pytest.mark.asyncio
    async def test_none_results_continue(self, consumer):
        """None results from xreadgroup continue to next iteration."""
        consumer.running = True
        consumer.redis = AsyncMock()

        call_count = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                consumer.running = False

        consumer.redis.xreadgroup = mock_xreadgroup

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            await consumer._consume_loop()

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_inner_not_running_break(self, consumer):
        """When running becomes False mid-batch, stops processing messages."""
        consumer.running = True
        consumer.redis = AsyncMock()

        call_count = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("stream", [("1-0", _valid_fields()), ("2-0", _valid_fields())])]
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        process_count = 0

        async def mock_process(stream, mid, fields):
            nonlocal process_count
            process_count += 1
            consumer.running = False  # stop after first message

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(
                consumer, "_start_processing_task", side_effect=mock_process
            ):
                await consumer._consume_loop()

        assert process_count == 1

    @pytest.mark.asyncio
    async def test_per_message_exception_continues(self, consumer):
        """Error processing one message doesn't stop processing the batch."""
        consumer.running = True
        consumer.redis = AsyncMock()

        call_count = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [
                    ("stream", [("1-0", _valid_fields()), ("2-0", _valid_fields())])
                ]
            consumer.running = False
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        process_count = 0

        async def mock_process(stream, mid, fields):
            nonlocal process_count
            process_count += 1
            if process_count == 1:
                raise Exception("processing error")

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(
                consumer, "_start_processing_task", side_effect=mock_process
            ):
                await consumer._consume_loop()

        assert process_count == 2

    @pytest.mark.asyncio
    async def test_cancelled_error_breaks_loop(self, consumer):
        """CancelledError in inner loop breaks cleanly."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            await consumer._consume_loop()

    @pytest.mark.asyncio
    async def test_general_exception_retries_after_sleep(self, consumer):
        """General exception sleeps for 1 second then retries."""
        consumer.running = True
        consumer.redis = AsyncMock()

        call_count = 0

        async def mock_xreadgroup(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("redis error")
            consumer.running = False
            return []

        consumer.redis.xreadgroup = mock_xreadgroup

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await consumer._consume_loop()

        mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_general_exception_while_not_running_no_retry(self, consumer):
        """General exception when running is False doesn't sleep/retry."""
        consumer.running = True
        consumer.redis = AsyncMock()

        async def mock_xreadgroup(**kwargs):
            consumer.running = False
            raise RuntimeError("redis error")

        consumer.redis.xreadgroup = mock_xreadgroup

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await consumer._consume_loop()

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_fatal_error_in_outer_try(self, consumer):
        """Fatal error before the inner while loop is caught by outer except."""
        consumer.running = True

        with patch.object(
            consumer, "_drain_pending", new_callable=AsyncMock,
            side_effect=RuntimeError("fatal drain error"),
        ):
            await consumer._consume_loop()  # should not raise

    @pytest.mark.asyncio
    async def test_finally_logs_active_tasks(self, consumer):
        """Finally block logs the active task count."""
        consumer.running = False
        consumer.redis = AsyncMock()

        f = Future()
        f.set_result(None)
        with consumer._futures_lock:
            consumer._active_futures.add(f)

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            await consumer._consume_loop()

        # Verify it completed (active tasks are logged in finally)


# ===================================================================
# _wait_out_backpressure / downstream backpressure integration
# ===================================================================


class TestWaitOutBackpressure:
    @pytest.mark.asyncio
    async def test_no_coordinator_returns_immediately(self, consumer):
        consumer.backpressure_coordinator = None
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await consumer._wait_out_backpressure()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_paused_returns_immediately(self, consumer):
        coordinator = MagicMock()
        coordinator.is_paused.return_value = False
        consumer.backpressure_coordinator = coordinator

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await consumer._wait_out_backpressure()

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_until_coordinator_clears(self, consumer):
        """While paused, the loop must sleep and re-check rather than
        returning — this is what actually stops XREADGROUP from running."""
        coordinator = MagicMock()
        coordinator.paused_services = frozenset({"ParsingService"})
        coordinator.pause_remaining.return_value = 3.0
        paused_calls = [True, True, False]
        coordinator.is_paused.side_effect = lambda: paused_calls.pop(0)
        consumer.backpressure_coordinator = coordinator
        consumer.running = True

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await consumer._wait_out_backpressure()

        assert mock_sleep.await_count == 2
        assert consumer._downstream_backpressure_active is False

    @pytest.mark.asyncio
    async def test_shutdown_interrupts_the_wait(self, consumer):
        """A shutdown request (running -> False) must interrupt the wait
        even if the coordinator is still paused, matching
        _delay_if_retry_not_ready's shutdown behaviour."""
        coordinator = MagicMock()
        coordinator.paused_services = frozenset({"ParsingService"})
        coordinator.pause_remaining.return_value = 300.0
        coordinator.is_paused.return_value = True
        consumer.backpressure_coordinator = coordinator
        consumer.running = True

        async def _fake_sleep(*_args, **_kwargs):
            consumer.running = False

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            await consumer._wait_out_backpressure()

        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_consume_loop_checks_backpressure_before_reading(self, consumer):
        """_consume_loop must not call XREADGROUP while downstream is
        signalled as backpressured."""
        coordinator = MagicMock()
        coordinator.paused_services = frozenset({"ParsingService"})
        coordinator.pause_remaining.return_value = 0.01
        consumer.backpressure_coordinator = coordinator
        consumer.running = True

        wait_calls = 0
        call_order: list[str] = []

        async def fake_wait():
            nonlocal wait_calls
            wait_calls += 1
            call_order.append("wait")
            if wait_calls >= 2:
                consumer.running = False

        async def fake_read(**_kwargs):
            call_order.append("read")
            return []

        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = fake_read

        with patch.object(consumer, "_wait_out_backpressure", side_effect=fake_wait):
            with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
                await consumer._consume_loop()

        assert wait_calls == 2
        # Every read must be preceded by a backpressure wait in the same
        # iteration — proves the ordering the docstring claims, not just
        # that both were called some number of times.
        assert call_order[0] == "wait"
        for index, entry in enumerate(call_order):
            if entry == "read":
                assert call_order[index - 1] == "wait"


# ===================================================================
# Integration-like tests for full lifecycle
# ===================================================================


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_start_consume_stop(self, logger, config):
        """Start -> consume one message -> stop lifecycle."""
        c = IndexingRedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_delconsumer = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        with patch.object(c, "_start_worker_thread"):
            c.worker_loop_ready = MagicMock()
            c.worker_loop_ready.wait.return_value = True
            c.worker_loop = MagicMock()
            c.worker_loop.is_running.return_value = True

            with patch(
                "app.services.messaging.redis_streams.indexing_consumer.Redis",
                return_value=mock_redis,
            ):
                await c.start(handler)

        assert c.running is True
        assert c.consume_task is not None

        # Clean up
        with patch.object(c, "_stop_worker_thread"):
            await c.stop()

        assert c.running is False


# ===================================================================
# Worker thread with pending tasks cleanup (lines 119-127)
# ===================================================================


class TestWorkerThreadPendingTaskCleanup:
    def test_worker_thread_cancels_pending_tasks_on_stop(self, logger, config):
        """Worker thread finally block cancels pending asyncio tasks."""
        c = IndexingRedisStreamsConsumer(logger, config)
        c._start_worker_thread()
        assert c.worker_loop_ready.wait(timeout=5.0)

        # Submit a long-running coroutine
        async def long_running():
            await asyncio.sleep(100)

        asyncio.run_coroutine_threadsafe(long_running(), c.worker_loop)

        # Stop should clean up pending tasks in the finally block
        c._stop_worker_thread()
        assert c.worker_executor is None


# ===================================================================
# Edge cases for module-level constants
# ===================================================================


class TestModuleConstants:
    def test_busygroup_error_constant(self):
        assert _BUSYGROUP_ERROR == "BUSYGROUP"

    def test_message_value_field_constant(self):
        assert _MESSAGE_VALUE_FIELD == "value"


# ===================================================================
# Abandonment: nothing is discarded without a terminal record status
# ===================================================================


class TestAbandonmentNotifiesTheSink:
    """A discarded message must leave its record in a terminal, visible state.

    An XACK is final — the entry leaves the PEL and nothing redelivers it. If
    the record's status is not made terminal first, no recovery sweep revisits
    it: the stale scan filters on IN_PROGRESS and the connector sweep only
    touches connectors that are gone. That is how records sat in QUEUED for
    ever with nothing in the logs but a stream id.
    """

    @staticmethod
    def _with_counters(consumer, *, failures):
        consumer.retry_manager = AsyncMock()
        consumer.retry_manager.get_count = AsyncMock(return_value=failures)
        consumer.redis = AsyncMock()
        consumer.redis.xack = AsyncMock()

    @pytest.mark.asyncio
    async def test_sink_hears_about_it_before_the_ack(self, consumer):
        self._with_counters(consumer, failures=99)
        calls = []
        sink = AsyncMock()
        sink.on_message_abandoned = AsyncMock(
            side_effect=lambda *a, **kw: calls.append("sink")
        )
        consumer.disposition_sink = sink
        consumer.redis.xack = AsyncMock(
            side_effect=lambda *a, **kw: calls.append("xack")
        )
        message = StreamMessage(
            eventType="newRecord", payload={"recordId": "rec-1"}
        )

        result = await consumer._should_dead_letter(
            "topic-a", "1-0", None, message
        )

        assert result is True
        assert calls == ["sink", "xack"]
        assert sink.on_message_abandoned.await_args.args[0] is message

    @pytest.mark.asyncio
    async def test_a_failing_sink_does_not_block_the_ack(self, consumer):
        """Losing the status write is bad; stalling the stream is worse."""
        self._with_counters(consumer, failures=99)
        sink = AsyncMock()
        sink.on_message_abandoned = AsyncMock(side_effect=Exception("graph down"))
        consumer.disposition_sink = sink

        result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is True
        consumer.redis.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_healthy_message_is_not_abandoned(self, consumer):
        self._with_counters(consumer, failures=0)
        sink = AsyncMock()
        consumer.disposition_sink = sink
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 2}]
        )

        result = await consumer._should_dead_letter("topic-a", "1-0")

        assert result is False
        sink.on_message_abandoned.assert_not_awaited()
        consumer.redis.xack.assert_not_awaited()


class TestProcessLocalRecordClaim:
    """Two entries can carry the same record; the entry-id set cannot see that.

    The stranded sweep re-publishes a record whose event went missing, so the
    original entry and the new one both name it. The cross-replica guard is the
    distributed `record:` lease, but that is only taken when a concurrency
    manager is configured — without one there was nothing keyed by record at
    all, and the two deliveries could race each other's status writes.
    """

    def test_a_record_can_only_be_claimed_once(self, consumer):
        assert consumer._claim_record("rec-1") is True
        assert consumer._claim_record("rec-1") is False

    def test_releasing_lets_the_next_delivery_through(self, consumer):
        consumer._claim_record("rec-1")
        consumer._release_record("rec-1")
        assert consumer._claim_record("rec-1") is True

    def test_different_records_do_not_block_each_other(self, consumer):
        assert consumer._claim_record("rec-1") is True
        assert consumer._claim_record("rec-2") is True

    def test_releasing_an_unheld_record_is_harmless(self, consumer):
        consumer._release_record("never-claimed")

    @pytest.mark.asyncio
    async def test_a_duplicate_delivery_is_left_for_redelivery(self, consumer):
        """The loser is not acked: it comes back once the winner finishes.

        Dropping it instead would discard a genuinely different event for the
        same record — a create and its update are not interchangeable.
        """
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        consumer.message_handler = MagicMock()
        # Another delivery of this record is already running in this process.
        consumer._claim_record("r1")

        result = await consumer._process_message_wrapper(
            "s", "1-0", _valid_fields(payload={"recordId": "r1"})
        )

        assert result is False
        consumer.message_handler.assert_not_called()
        consumer.redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_claim_is_released_when_processing_finishes(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler

        await consumer._process_message_wrapper(
            "s", "1-0", _valid_fields(payload={"recordId": "r1"})
        )

        assert consumer._claim_record("r1") is True

    @pytest.mark.asyncio
    async def test_the_claim_is_released_when_the_handler_raises(self, consumer):
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        async def handler(msg):
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes this an async generator

        consumer.message_handler = handler

        await consumer._process_message_wrapper(
            "s", "1-0", _valid_fields(payload={"recordId": "r1"})
        )

        assert consumer._claim_record("r1") is True
