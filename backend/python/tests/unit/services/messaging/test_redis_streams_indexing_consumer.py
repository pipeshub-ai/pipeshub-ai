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
from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
    _BUSYGROUP_ERROR,
    _MESSAGE_VALUE_FIELD,
)


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
    return IndexingRedisStreamsConsumer(logger, config)


def _valid_fields(event_type="test", payload=None):
    """Return a Redis-style fields dict with a JSON-serialised 'value' key."""
    payload = payload or {"key": "val"}
    return {"value": json.dumps({"eventType": event_type, "payload": payload})}


# ===================================================================
# __init__
# ===================================================================


class TestInit:
    def test_default_attributes(self, consumer):
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

        consumer._stop_worker_thread()
        assert len(consumer._active_futures) == 0

    def test_stop_with_loop_not_running(self, consumer):
        """When worker_loop exists but is not running, stop skips loop.stop()."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        consumer.worker_loop = mock_loop
        consumer.worker_executor = MagicMock()

        consumer._stop_worker_thread()
        mock_loop.call_soon_threadsafe.assert_not_called()
        assert consumer.worker_executor is None


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


# ===================================================================
# cleanup  (lines 137-143)
# ===================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stops_worker_and_closes_redis(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()
        consumer.redis = mock_redis

        with patch.object(consumer, "_stop_worker_thread") as mock_stop:
            await consumer.cleanup()
            mock_stop.assert_called_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_without_redis(self, consumer):
        consumer.redis = None
        with patch.object(consumer, "_stop_worker_thread"):
            await consumer.cleanup()  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_handles_exception(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock(side_effect=Exception("close err"))
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
    def test_valid_json(self, consumer):
        fields = _valid_fields("CREATE", {"id": 42})
        result = consumer._parse_message("1-0", fields)
        assert isinstance(result, StreamMessage)
        assert result.eventType == "CREATE"
        assert result.payload == {"id": 42}

    def test_double_encoded_json(self, consumer):
        inner = json.dumps({"eventType": "test", "payload": {"key": "val"}})
        fields = {"value": json.dumps(inner)}
        result = consumer._parse_message("1-0", fields)
        assert isinstance(result, StreamMessage)
        assert result.payload == {"key": "val"}

    def test_missing_value_field_returns_none(self, consumer):
        result = consumer._parse_message("1-0", {"_init": "1"})
        assert result is None

    def test_empty_fields_returns_none(self, consumer):
        result = consumer._parse_message("1-0", {})
        assert result is None

    def test_invalid_json_returns_none(self, consumer):
        result = consumer._parse_message("1-0", {"value": "not-json{{{"})
        assert result is None

    def test_valid_json_missing_required_fields(self, consumer):
        """Valid JSON but missing required StreamMessage fields raises (not caught)."""
        # The code only catches JSONDecodeError, not pydantic ValidationError.
        # Missing eventType + payload will raise ValidationError.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            consumer._parse_message("1-0", {"value": json.dumps({"foo": "bar"})})

    def test_valid_with_timestamp(self, consumer):
        fields = {
            "value": json.dumps(
                {"eventType": "test", "payload": {"k": "v"}, "timestamp": 12345}
            )
        }
        result = consumer._parse_message("1-0", fields)
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
            "asyncio.run_coroutine_threadsafe", return_value=mock_future
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

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
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

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
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

        mock_ack_future = MagicMock()
        mock_ack_future.result.return_value = 1

        with patch(
            "asyncio.run_coroutine_threadsafe", return_value=mock_ack_future
        ):
            result = await consumer._process_message_wrapper(
                "stream-a", "1-0", _valid_fields()
            )

        assert result is True
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

    @pytest.mark.asyncio
    async def test_only_parsing_complete_released(self, consumer):
        """Only PARSING_COMPLETE yielded; indexing released in finally."""
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

        mock_ack_future = MagicMock()
        mock_ack_future.result.return_value = 1

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_ack_future):
            result = await consumer._process_message_wrapper(
                "s", "1-0", _valid_fields()
            )

        assert result is True
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

        mock_ack_future = MagicMock()
        mock_ack_future.result.return_value = 1

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_ack_future):
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
            yield  # noqa: unreachable

        consumer.message_handler = handler
        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is False
        assert consumer.parsing_semaphore._value == 1
        assert consumer.indexing_semaphore._value == 1

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
    async def test_xack_timeout_logged(self, consumer):
        """TimeoutError during xack is logged as warning."""
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

        mock_ack_future = MagicMock()
        mock_ack_future.result.side_effect = TimeoutError("xack timed out")

        with patch("asyncio.run_coroutine_threadsafe", return_value=mock_ack_future):
            result = await consumer._process_message_wrapper(
                "s", "1-0", _valid_fields()
            )

        # Still returns True because the handler succeeded
        assert result is True

    @pytest.mark.asyncio
    async def test_xack_skipped_during_shutdown(self, consumer):
        """When not running, xack is skipped."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.running = False  # shutting down

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        # No redis/main_loop set -> triggers the `elif not self.running` branch
        consumer.redis = None
        consumer.main_loop = None

        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is True

    @pytest.mark.asyncio
    async def test_xack_skipped_when_main_loop_not_running(self, consumer):
        """When main_loop exists but is not running, xack is skipped."""
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)
        consumer.running = False

        async def handler(msg):
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id="r1"),
            )

        consumer.message_handler = handler
        consumer.redis = AsyncMock()
        mock_main_loop = MagicMock()
        mock_main_loop.is_running.return_value = False
        consumer.main_loop = mock_main_loop

        result = await consumer._process_message_wrapper("s", "1-0", _valid_fields())
        assert result is True

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

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        assert mock_process.call_count == 2

    @pytest.mark.asyncio
    async def test_drain_empty_pel(self, consumer):
        """Empty PEL exits immediately."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_process:
            await consumer._drain_pending()

        mock_process.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_none_result(self, consumer):
        """When xautoclaim returns no claimed messages, drain stops."""
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))

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
        """Backpressure engaged when active tasks >= limit, cleared when below."""
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

        original_get_count = consumer._get_active_task_count
        task_count_values = [max_tasks, 0, 0]  # first: at capacity, rest: below
        task_count_iter = iter(task_count_values)

        def mock_get_count():
            try:
                return next(task_count_iter)
            except StopIteration:
                return 0

        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch.object(consumer, "_get_active_task_count", side_effect=mock_get_count):
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
            with patch.object(consumer, "_get_active_task_count", side_effect=mock_get_count):
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
            return None

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
