"""
Tests for RedisStreamsConsumer:
  - initialize (connects, creates groups, handles BUSYGROUP)
  - cleanup (closes redis)
  - start / stop lifecycle
  - _process_message (JSON parsing, handler invocation)
  - is_running
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig
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
        mock_redis.close = AsyncMock()
        consumer.redis = mock_redis

        await consumer.cleanup()

        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_no_redis(self, consumer):
        assert consumer.redis is None
        await consumer.cleanup()

    @pytest.mark.asyncio
    async def test_handles_close_exception(self, consumer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock(side_effect=Exception("close failed"))
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
        assert called_msg["eventType"] == "TEST"

    @pytest.mark.asyncio
    async def test_handles_double_encoded_json(self, consumer):
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        inner = json.dumps({"key": "val"})
        result = await consumer._process_message(
            "s", "1-0", {"value": json.dumps(inner)}
        )

        assert result is True
        called_msg = handler.call_args[0][0]
        assert called_msg["key"] == "val"

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
            "s", "1-0", {"value": json.dumps({"k": "v"})}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_processes_empty_dict_message(self, consumer):
        """An empty dict {} is a valid parsed message and should be passed to the handler."""
        handler = AsyncMock(return_value=True)
        consumer.message_handler = handler

        result = await consumer._process_message("s", "1-0", {"value": "{}"})

        assert result is True
        handler.assert_awaited_once_with({})

    @pytest.mark.asyncio
    async def test_returns_false_on_handler_exception(self, consumer):
        consumer.message_handler = AsyncMock(side_effect=Exception("handler error"))
        result = await consumer._process_message(
            "s", "1-0", {"value": json.dumps({"k": "v"})}
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
