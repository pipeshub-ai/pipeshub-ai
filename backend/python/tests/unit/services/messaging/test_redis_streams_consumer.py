"""
Tests for RedisStreamsConsumer:
  - initialize (connects, creates groups, handles BUSYGROUP, stale consumer cleanup)
  - cleanup (closes redis)
  - start / stop lifecycle (including error paths)
  - _process_message (JSON parsing, handler invocation, null payload)
  - _drain_pending (PEL recovery: ack, nack, exceptions, batching)
  - _consume_loop (message processing, ack/nack, cancellation, error handling)
  - is_running
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer


@pytest.fixture
def logger():
    return logging.getLogger("test_redis_streams_consumer")


@pytest.fixture
def config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        password=None,
        db=0,
        max_len=10000,
        block_ms=100,
        client_id="test-consumer",
        group_id="test-group",
        topics=["test-topic"],
    )


@pytest.fixture
def consumer(logger, config):
    return RedisStreamsConsumer(logger, config)


class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_redis_client_and_pings(self, logger, config):
        c = RedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.initialize()

        mock_redis.ping.assert_awaited_once()
        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_creates_consumer_groups_for_each_topic(self, logger):
        cfg = RedisStreamsConfig(
            host="localhost",
            port=6379,
            client_id="c",
            group_id="g",
            topics=["topic-a", "topic-b"],
        )
        c = RedisStreamsConsumer(logger, cfg)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.initialize()

        assert mock_redis.xgroup_create.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_busygroup_error(self, logger, config):
        c = RedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.initialize()

        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_raises_on_non_busygroup_error(self, logger, config):
        c = RedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("Connection lost")
        )

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            with pytest.raises(Exception, match="Connection lost"):
                await c.initialize()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_closes_redis_client(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis

        await consumer.cleanup()

        mock_redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_no_redis(self, consumer):
        assert consumer.redis is None
        await consumer.cleanup()

    @pytest.mark.asyncio
    async def test_handles_close_exception(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(side_effect=Exception("close failed"))
        consumer.redis = mock_redis

        await consumer.cleanup()


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_initializes_if_no_redis(self, logger, config):
        c = RedisStreamsConsumer(logger, config)
        handler = AsyncMock(return_value=True)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.start(handler)

        assert c.running is True
        assert c.consume_task is not None

        # Clean up
        c.running = False
        if c.consume_task:
            c.consume_task.cancel()
            try:
                await c.consume_task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_stop_cancels_consume_task(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock()
        consumer.consume_task = asyncio.create_task(asyncio.sleep(10))

        await consumer.stop()

        assert consumer.running is False
        assert consumer.consume_task.cancelled() or consumer.consume_task.done()

    @pytest.mark.asyncio
    async def test_is_running(self, consumer):
        assert consumer.is_running() is False
        consumer.running = True
        assert consumer.is_running() is True


class TestProcessMessage:
    @pytest.mark.asyncio
    async def test_parses_json_and_calls_handler(self, consumer):
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        result = await consumer._process_message(
            "test-stream",
            "1-0",
            {"value": json.dumps({"eventType": "TEST", "payload": {"id": 1}})},
        )

        assert result is True
        handler.assert_awaited_once()
        called_msg = handler.call_args[0][0]
        assert isinstance(called_msg, StreamMessage)
        assert called_msg.eventType == "TEST"

    @pytest.mark.asyncio
    async def test_handles_double_encoded_json(self, consumer):
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        inner = json.dumps({"eventType": "test", "payload": {"key": "val"}})
        result = await consumer._process_message(
            "s", "1-0", {"value": json.dumps(inner)}
        )

        assert result is True
        called_msg = handler.call_args[0][0]
        assert isinstance(called_msg, StreamMessage)
        assert called_msg.payload == {"key": "val"}

    @pytest.mark.asyncio
    async def test_returns_false_for_invalid_json(self, consumer):
        consumer.message_handler = AsyncMock()
        result = await consumer._process_message(
            "s", "1-0", {"value": "not-json{{{"}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_handler(self, consumer):
        consumer.message_handler = None
        result = await consumer._process_message(
            "s", "1-0", {"value": json.dumps({"eventType": "test", "payload": {"k": "v"}})}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_processes_empty_dict_message_fails(self, consumer):
        """An empty dict {} is no longer valid for StreamMessage (missing required fields)."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        result = await consumer._process_message("s", "1-0", {"value": "{}"})

        # StreamMessage(**{}) will fail validation (missing eventType, payload)
        assert result is False
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_on_handler_exception(self, consumer):
        consumer.message_handler = AsyncMock(side_effect=Exception("handler error"))
        result = await consumer._process_message(
            "s", "1-0", {"value": json.dumps({"eventType": "test", "payload": {"k": "v"}})}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_message_without_value_field(self, consumer):
        """Messages without a 'value' field (e.g. init messages) are skipped."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        result = await consumer._process_message("s", "1-0", {"_init": "1"})

        assert result is True
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_completely_empty_fields(self, consumer):
        """Empty fields dict is treated as an init-like message and skipped."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        result = await consumer._process_message("s", "1-0", {})

        assert result is True
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_when_parsed_value_is_null(self, consumer):
        """When JSON parses to None (literal null), message should be skipped."""
        consumer.message_handler = AsyncMock(return_value=True)

        result = await consumer._process_message("s", "1-0", {"value": "null"})

        assert result is False
        consumer.message_handler.assert_not_awaited()


class TestInitializeStaleConsumerCleanup:
    """Tests for initialize() consumer group creation."""

    @pytest.mark.asyncio
    async def test_initialize_creates_groups_for_each_topic(self, logger):
        """xgroup_create is called for every topic during initialize."""
        cfg = RedisStreamsConfig(
            host="localhost",
            port=6379,
            client_id="c1",
            group_id="g1",
            topics=["topic-a", "topic-b"],
        )
        c = RedisStreamsConsumer(logger, cfg)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.initialize()

        assert mock_redis.xgroup_create.call_count == 2
        assert c.redis is mock_redis

    @pytest.mark.asyncio
    async def test_busygroup_exception_is_suppressed(self, logger, config):
        """If xgroup_create raises BUSYGROUP, the error is silently ignored."""
        c = RedisStreamsConsumer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            # Should not raise
            await c.initialize()

        assert c.redis is mock_redis


class TestStartErrorPath:
    """Tests for start() error handling -- lines 99-106."""

    @pytest.mark.asyncio
    async def test_start_calls_initialize_when_redis_is_none(self, logger, config):
        """When self.redis is None, start() calls initialize() (line 99-100)."""
        c = RedisStreamsConsumer(logger, config)
        handler = AsyncMock(return_value=True)

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_delconsumer = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()

        assert c.redis is None

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            return_value=mock_redis,
        ):
            await c.start(handler)

        assert c.redis is mock_redis
        assert c.running is True
        assert c.consume_task is not None

        # cleanup
        c.running = False
        c.consume_task.cancel()
        try:
            await c.consume_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_start_does_not_initialize_when_redis_already_set(
        self, logger, config
    ):
        """When self.redis is already set, start() skips initialize()."""
        c = RedisStreamsConsumer(logger, config)
        handler = AsyncMock(return_value=True)

        mock_redis = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()
        c.redis = mock_redis

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
        ) as mock_cls:
            await c.start(handler)
            mock_cls.assert_not_called()

        assert c.running is True
        assert c.consume_task is not None

        # cleanup
        c.running = False
        c.consume_task.cancel()
        try:
            await c.consume_task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_start_raises_on_initialize_failure(self, logger, config):
        """If initialize() fails during start(), the error propagates (lines 104-106)."""
        c = RedisStreamsConsumer(logger, config)
        handler = AsyncMock(return_value=True)

        with patch(
            "app.services.messaging.redis_streams.consumer.Redis",
            side_effect=Exception("Connection refused"),
        ):
            with pytest.raises(Exception, match="Connection refused"):
                await c.start(handler)

        assert c.running is True  # set before initialize
        assert c.consume_task is None  # never reached


class TestStop:
    """Tests for stop() -- lines 108-123."""

    @pytest.mark.asyncio
    async def test_stop_cancels_task_and_closes_redis(self, consumer):
        """stop() cancels the consume_task and closes Redis."""
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.consume_task = asyncio.create_task(asyncio.sleep(100))

        await consumer.stop()

        assert consumer.running is False
        assert consumer.consume_task.cancelled() or consumer.consume_task.done()
        mock_redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_consume_task(self, consumer):
        """stop() works when no consume_task was created."""
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.consume_task = None

        await consumer.stop()

        assert consumer.running is False
        mock_redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_redis(self, consumer):
        """stop() works when redis is None (line 121->exit path)."""
        consumer.running = True
        consumer.redis = None
        consumer.consume_task = None

        await consumer.stop()

        assert consumer.running is False


class TestDrainPending:
    """Tests for _drain_pending() — Phase 1 (XAUTOCLAIM) + Phase 2 (XREADGROUP id="0")."""

    @pytest.mark.asyncio
    async def test_drain_pending_no_pending_messages(self, consumer):
        """When PEL is empty, _drain_pending returns immediately."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        mock_redis.xautoclaim.assert_awaited_once()
        mock_redis.xreadgroup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drain_pending_none_result(self, consumer):
        """When xautoclaim returns no claimed messages, _drain_pending stops."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        mock_redis.xautoclaim.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drain_pending_processes_and_acks_messages(self, consumer):
        """Pending messages claimed via XAUTOCLAIM are processed and acknowledged."""
        mock_redis = AsyncMock()
        pending_msg = {
            "value": json.dumps({"eventType": "RECOVER", "payload": {"id": 42}})
        }
        mock_redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("1-0", pending_msg)], [])
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_awaited_once_with(
            "test-topic", consumer.config.group_id, "1-0"
        )

    @pytest.mark.asyncio
    async def test_drain_pending_does_not_ack_failed_messages(self, consumer):
        """If handler returns False, message is NOT acknowledged."""
        mock_redis = AsyncMock()
        pending_msg = {
            "value": json.dumps({"eventType": "FAIL", "payload": {"id": 1}})
        }
        mock_redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("2-0", pending_msg)], [])
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=False)

        await consumer._drain_pending()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_pending_handles_xack_exception(self, consumer):
        """If xack raises during pending recovery, the error is logged but loop continues."""
        mock_redis = AsyncMock()
        msg1 = {
            "value": json.dumps({"eventType": "OK1", "payload": {"id": 1}})
        }
        msg2 = {
            "value": json.dumps({"eventType": "OK2", "payload": {"id": 2}})
        }
        mock_redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("1-0", msg1), ("2-0", msg2)], [])
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        # xack raises on first call, succeeds on second
        mock_redis.xack = AsyncMock(
            side_effect=[Exception("xack failed"), None]
        )
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        assert consumer.message_handler.call_count == 2
        # Both messages were processed, xack called for both (first raised, second ok)
        assert mock_redis.xack.call_count == 2

    @pytest.mark.asyncio
    async def test_drain_pending_multiple_batches(self, consumer):
        """_drain_pending loops until PEL is fully drained via XAUTOCLAIM."""
        mock_redis = AsyncMock()
        msg1 = {"value": json.dumps({"eventType": "E1", "payload": {"a": 1}})}
        msg2 = {"value": json.dumps({"eventType": "E2", "payload": {"b": 2}})}

        # First call: one claimed msg, next_id indicates more
        # Second call: another claimed msg, next_id "0-0" indicates done
        mock_redis.xautoclaim = AsyncMock(
            side_effect=[
                ("5-0", [("1-0", msg1)], []),
                ("0-0", [("2-0", msg2)], []),
            ]
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        assert consumer.message_handler.call_count == 2
        assert mock_redis.xautoclaim.call_count == 2
        assert mock_redis.xack.call_count == 2

    @pytest.mark.asyncio
    async def test_drain_pending_stops_when_not_running(self, consumer):
        """If self.running becomes False, _drain_pending exits early."""
        mock_redis = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = False
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        mock_redis.xautoclaim.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_pending_streams_with_empty_messages(self, consumer):
        """When xautoclaim returns no claimed messages, drain stops."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        mock_redis.xautoclaim.assert_awaited_once()
        consumer.message_handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_pending_phase2_recovers_own_pel(self, consumer):
        """Phase 2: XREADGROUP id="0" recovers messages already owned by this consumer.

        This is the same-client_id "restart" scenario — XAUTOCLAIM cannot help
        because there is no other consumer to steal from.
        """
        mock_redis = AsyncMock()
        own_msg = {
            "value": json.dumps({"eventType": "OWN_PEL", "payload": {"id": 7}})
        }
        # Phase 1: nothing to claim from other consumers
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        # Phase 2: own PEL has one message, then drained
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                [("test-topic", [("9-0", own_msg)])],
                None,
            ]
        )
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._drain_pending()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_awaited_once_with(
            "test-topic", consumer.config.group_id, "9-0"
        )
        # Phase 2 must use id "0" to read from own PEL (not ">")
        first_phase2_call = mock_redis.xreadgroup.call_args_list[0]
        assert first_phase2_call.kwargs["streams"] == {"test-topic": "0"}
        assert first_phase2_call.kwargs["consumername"] == consumer.config.client_id

    @pytest.mark.asyncio
    async def test_drain_pending_phase2_does_not_ack_failed_messages(self, consumer):
        """Phase 2: handler returning False keeps the message in own PEL."""
        mock_redis = AsyncMock()
        own_msg = {
            "value": json.dumps({"eventType": "OWN_FAIL", "payload": {"id": 8}})
        }
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                [("test-topic", [("10-0", own_msg)])],
                None,
            ]
        )
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=False)

        await consumer._drain_pending()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_not_awaited()


class TestExceedsMaxRetries:
    """Tests for _exceeds_max_retries() — dead-letter logic for poison messages."""

    @pytest.mark.asyncio
    async def test_under_limit_returns_false(self, consumer):
        """Message below the delivery threshold should NOT be dead-lettered."""
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 2}]
        )
        consumer.redis = mock_redis

        with patch("app.services.messaging.redis_streams.consumer.messaging_env") as mock_env:
            mock_env.max_delivery_attempts = 10
            result = await consumer._exceeds_max_retries("test-topic", "1-0")

        assert result is False
        mock_redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_at_limit_dead_letters(self, consumer):
        """Message at the delivery threshold should be ACK-ed (dead-lettered)."""
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 10}]
        )
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis

        with patch("app.services.messaging.redis_streams.consumer.messaging_env") as mock_env:
            mock_env.max_delivery_attempts = 10
            result = await consumer._exceeds_max_retries("test-topic", "1-0")

        assert result is True
        mock_redis.xack.assert_awaited_once_with("test-topic", consumer.config.group_id, "1-0")

    @pytest.mark.asyncio
    async def test_above_limit_dead_letters(self, consumer):
        """Message above the delivery threshold should be ACK-ed (dead-lettered)."""
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(
            return_value=[{"times_delivered": 15}]
        )
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis

        with patch("app.services.messaging.redis_streams.consumer.messaging_env") as mock_env:
            mock_env.max_delivery_attempts = 10
            result = await consumer._exceeds_max_retries("test-topic", "1-0")

        assert result is True
        mock_redis.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_xpending_returns_false(self, consumer):
        """When XPENDING returns no details, message is not dead-lettered."""
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(return_value=[])
        consumer.redis = mock_redis

        with patch("app.services.messaging.redis_streams.consumer.messaging_env") as mock_env:
            mock_env.max_delivery_attempts = 10
            result = await consumer._exceeds_max_retries("test-topic", "1-0")

        assert result is False

    @pytest.mark.asyncio
    async def test_xpending_error_returns_false(self, consumer):
        """XPENDING errors should not dead-letter — return False and log."""
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(side_effect=Exception("redis down"))
        consumer.redis = mock_redis

        with patch("app.services.messaging.redis_streams.consumer.messaging_env") as mock_env:
            mock_env.max_delivery_attempts = 10
            result = await consumer._exceeds_max_retries("test-topic", "1-0")

        assert result is False

    @pytest.mark.asyncio
    async def test_drain_phase1_skips_poison_message(self, consumer):
        """Phase 1 should skip processing when _exceeds_max_retries returns True."""
        mock_redis = AsyncMock()
        pending_msg = {
            "value": json.dumps({"eventType": "POISON", "payload": {"id": 1}})
        }
        mock_redis.xautoclaim = AsyncMock(
            return_value=("0-0", [("1-0", pending_msg)], [])
        )
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        with patch.object(consumer, "_exceeds_max_retries", new_callable=AsyncMock, return_value=True):
            await consumer._drain_pending()

        # Handler should never be called — message was skipped
        consumer.message_handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_phase2_skips_poison_message(self, consumer):
        """Phase 2 should skip processing when _exceeds_max_retries returns True."""
        mock_redis = AsyncMock()
        own_msg = {
            "value": json.dumps({"eventType": "OWN_POISON", "payload": {"id": 2}})
        }
        # Phase 1: nothing to claim
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        # Phase 2: own PEL has one poison message, then drained
        mock_redis.xreadgroup = AsyncMock(
            side_effect=[
                [("test-topic", [("5-0", own_msg)])],
                None,
            ]
        )
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        with patch.object(consumer, "_exceeds_max_retries", new_callable=AsyncMock, return_value=True):
            await consumer._drain_pending()

        consumer.message_handler.assert_not_awaited()


class TestConsumeLoop:
    """Tests for _consume_loop()."""

    @pytest.mark.asyncio
    async def test_consume_loop_processes_new_messages(self, consumer):
        """_consume_loop processes messages and acks successful ones."""
        mock_redis = AsyncMock()
        msg = {"value": json.dumps({"eventType": "NEW", "payload": {"x": 1}})}
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("test-topic", [("10-0", msg)])]
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._consume_loop()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_awaited_once_with(
            "test-topic", consumer.config.group_id, "10-0"
        )

    @pytest.mark.asyncio
    async def test_consume_loop_nack_path(self, consumer):
        """When handler returns False, message is not acked (nack path)."""
        mock_redis = AsyncMock()
        msg = {"value": json.dumps({"eventType": "FAIL", "payload": {"x": 1}})}
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("test-topic", [("11-0", msg)])]
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=False)

        await consumer._consume_loop()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consume_loop_handles_xack_exception(self, consumer):
        """Exception during xack is caught per-message, loop continues."""
        mock_redis = AsyncMock()
        msg = {"value": json.dumps({"eventType": "OK", "payload": {"x": 1}})}
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("test-topic", [("12-0", msg)])]
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xack = AsyncMock(side_effect=Exception("xack exploded"))
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        # Should not raise -- the per-message except catches the xack error
        await consumer._consume_loop()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_awaited_once()
        mock_redis.aclose.assert_awaited()  # cleanup called in finally

    @pytest.mark.asyncio
    async def test_consume_loop_nack_path_logs_warning(self, consumer):
        """When handler returns False, the nack/warning path is taken."""
        mock_redis = AsyncMock()
        msg = {"value": json.dumps({"eventType": "NACK", "payload": {"x": 1}})}
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [("test-topic", [("13-0", msg)])]
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        # Handler returns False (processing failed)
        consumer.message_handler = AsyncMock(return_value=False)

        await consumer._consume_loop()

        consumer.message_handler.assert_awaited_once()
        mock_redis.xack.assert_not_awaited()  # no ack on failure

    @pytest.mark.asyncio
    async def test_consume_loop_handles_xreadgroup_exception(self, consumer):
        """If xreadgroup raises in the inner loop, error is logged and loop retries."""
        mock_redis = AsyncMock()
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Redis connection lost")
            # Stop loop on next iteration
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        # Skip _drain_pending so its Phase 2 xreadgroup call doesn't consume
        # the side_effect intended for the inner consume loop.
        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await consumer._consume_loop()
                mock_sleep.assert_awaited_with(1)

        mock_redis.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_consume_loop_cancellation(self, consumer):
        """CancelledError in the inner loop breaks cleanly."""
        mock_redis = AsyncMock()
        mock_redis.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        # Skip _drain_pending so its Phase 2 xreadgroup call doesn't trigger
        # the CancelledError before the inner consume loop's handler can catch it.
        with patch.object(consumer, "_drain_pending", new_callable=AsyncMock):
            await consumer._consume_loop()

        mock_redis.aclose.assert_awaited()

    @pytest.mark.asyncio
    async def test_consume_loop_skips_when_no_results(self, consumer):
        """When xreadgroup returns None, loop continues without processing."""
        mock_redis = AsyncMock()
        call_count = 0

        async def xreadgroup_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return None
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        await consumer._consume_loop()

        consumer.message_handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consume_loop_fatal_error_in_drain_pending(self, consumer):
        """Fatal error in _drain_pending is caught by outer try/except."""
        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock(return_value=True)

        with patch.object(
            consumer, "_drain_pending", new_callable=AsyncMock,
            side_effect=Exception("Fatal redis error"),
        ):
            # Should not raise; the outer except catches it
            await consumer._consume_loop()

        mock_redis.aclose.assert_awaited()  # cleanup still runs in finally

    @pytest.mark.asyncio
    async def test_consume_loop_calls_cleanup_in_finally(self, consumer):
        """The finally block always invokes cleanup()."""
        mock_redis = AsyncMock()
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.aclose = AsyncMock()
        consumer.redis = mock_redis
        consumer.running = True
        consumer.message_handler = AsyncMock()

        async def xreadgroup_side_effect(**kwargs):
            consumer.running = False
            return None

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)

        await consumer._consume_loop()

        mock_redis.aclose.assert_awaited_once()
