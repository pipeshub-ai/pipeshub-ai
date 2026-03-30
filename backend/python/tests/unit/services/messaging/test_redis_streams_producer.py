"""
Tests for RedisStreamsProducer:
  - initialize (connect, ping, double-checked locking)
  - cleanup (close, noop)
  - send_message (xadd, auto-initialize, failure)
  - send_event (wraps message, failure)
  - start / stop lifecycle
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.producer import RedisStreamsProducer


@pytest.fixture
def logger():
    return logging.getLogger("test_redis_streams_producer")


@pytest.fixture
def config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        password=None,
        db=0,
        max_len=5000,
        client_id="test-producer",
        group_id="test-group",
        topics=["test-topic"],
    )


@pytest.fixture
def producer(logger, config):
    return RedisStreamsProducer(logger, config)


class TestInitialize:
    @pytest.mark.asyncio
    async def test_creates_redis_client_and_pings(self, logger, config):
        p = RedisStreamsProducer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.producer.Redis",
            return_value=mock_redis,
        ):
            await p.initialize()

        mock_redis.ping.assert_awaited_once()
        assert p.redis is mock_redis

    @pytest.mark.asyncio
    async def test_skips_if_already_initialized(self, producer):
        producer.redis = MagicMock()

        with patch("app.services.messaging.redis_streams.producer.Redis") as MockRedis:
            await producer.initialize()
            MockRedis.assert_not_called()

    @pytest.mark.asyncio
    async def test_double_check_after_lock(self, logger, config):
        p = RedisStreamsProducer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        call_count = 0

        def redis_factory(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_redis

        with patch(
            "app.services.messaging.redis_streams.producer.Redis",
            side_effect=redis_factory,
        ):
            await asyncio.gather(p.initialize(), p.initialize())

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_clears_redis_on_failure(self, logger, config):
        p = RedisStreamsProducer(logger, config)
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("unreachable"))

        with patch(
            "app.services.messaging.redis_streams.producer.Redis",
            return_value=mock_redis,
        ):
            with pytest.raises(Exception, match="unreachable"):
                await p.initialize()

        assert p.redis is None


class TestCleanup:
    @pytest.mark.asyncio
    async def test_closes_redis_client(self, producer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()
        producer.redis = mock_redis

        await producer.cleanup()

        mock_redis.close.assert_awaited_once()
        assert producer.redis is None

    @pytest.mark.asyncio
    async def test_noop_when_no_redis(self, producer):
        assert producer.redis is None
        await producer.cleanup()

    @pytest.mark.asyncio
    async def test_handles_close_exception(self, producer):
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock(side_effect=Exception("close failed"))
        producer.redis = mock_redis

        await producer.cleanup()


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_success(self, producer):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        producer.redis = mock_redis

        result = await producer.send_message("test-topic", {"key": "val"}, key="k1")

        assert result is True
        mock_redis.xadd.assert_awaited_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == "test-topic"
        fields = call_args[0][1]
        assert json.loads(fields["value"]) == {"key": "val"}
        assert fields["key"] == "k1"

    @pytest.mark.asyncio
    async def test_without_key(self, producer):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        producer.redis = mock_redis

        result = await producer.send_message("t", {"data": 1})

        assert result is True
        fields = mock_redis.xadd.call_args[0][1]
        assert "key" not in fields

    @pytest.mark.asyncio
    async def test_uses_maxlen_from_config(self, producer):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")
        producer.redis = mock_redis

        await producer.send_message("t", {"d": 1})

        kwargs = mock_redis.xadd.call_args[1]
        assert kwargs["maxlen"] == 5000
        assert kwargs["approximate"] is True

    @pytest.mark.asyncio
    async def test_auto_initializes(self, logger, config):
        p = RedisStreamsProducer(logger, config)
        assert p.redis is None

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="1-0")

        with patch(
            "app.services.messaging.redis_streams.producer.Redis",
            return_value=mock_redis,
        ):
            result = await p.send_message("t", {"d": 1})

        assert result is True
        mock_redis.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, producer):
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(side_effect=Exception("xadd failed"))
        producer.redis = mock_redis

        result = await producer.send_message("t", {"d": 1})
        assert result is False


class TestSendEvent:
    @pytest.mark.asyncio
    async def test_wraps_message(self, producer):
        producer.send_message = AsyncMock(return_value=True)

        result = await producer.send_event(
            topic="test-topic",
            event_type="USER_CREATED",
            payload={"userId": "123"},
            key="key1",
        )

        assert result is True
        producer.send_message.assert_awaited_once()
        call_kwargs = producer.send_message.call_args
        sent_msg = call_kwargs.kwargs.get("message") or call_kwargs[0][1]
        assert sent_msg["eventType"] == "USER_CREATED"
        assert sent_msg["payload"] == {"userId": "123"}
        assert "timestamp" in sent_msg

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, producer):
        producer.send_message = AsyncMock(side_effect=Exception("boom"))

        result = await producer.send_event(
            topic="t", event_type="EVT", payload={}
        )
        assert result is False


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_calls_initialize_when_no_redis(self, logger, config):
        p = RedisStreamsProducer(logger, config)
        assert p.redis is None

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.producer.Redis",
            return_value=mock_redis,
        ):
            await p.start()

        assert p.redis is mock_redis

    @pytest.mark.asyncio
    async def test_start_skips_when_redis_exists(self, producer):
        producer.redis = MagicMock()

        with patch("app.services.messaging.redis_streams.producer.Redis") as MockRedis:
            await producer.start()
            MockRedis.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_calls_cleanup(self, producer):
        producer.cleanup = AsyncMock()
        await producer.stop()
        producer.cleanup.assert_awaited_once()
