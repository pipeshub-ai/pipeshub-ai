"""
Tests for RedisStreamsConsumer with RetryManager integration:
  - _should_dead_letter with RetryManager
  - _finalize_message success and error paths
  - RetryManager increment and clear on different outcomes
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig, StreamMessage, messaging_env
from app.services.messaging.error_classifier import MessageErrorType
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.retry_manager import RetryManager


@pytest.fixture
def logger():
    return logging.getLogger("test_redis_retry_manager")


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
def mock_retry_manager():
    """Create a mocked RetryManager"""
    mock_manager = AsyncMock(spec=RetryManager)
    mock_manager.clear = AsyncMock()
    mock_manager.get_count = AsyncMock(return_value=0)
    mock_manager.increment_and_check = AsyncMock(return_value=(1, False))
    return mock_manager


@pytest.fixture
def consumer_with_retry_manager(logger, config, mock_retry_manager):
    """Consumer with RetryManager configured"""
    return RedisStreamsConsumer(logger, config, retry_manager=mock_retry_manager)


class TestRetryManagerHelpers:
    """Tests for RetryManager helper methods"""

    @pytest.mark.asyncio
    async def test_clear_retry_tracking_calls_manager(self, consumer_with_retry_manager, mock_retry_manager):
        await consumer_with_retry_manager._clear_retry_tracking("msg-1")
        mock_retry_manager.clear.assert_awaited_once_with("msg-1")

    @pytest.mark.asyncio
    async def test_clear_retry_tracking_without_manager(self, logger, config):
        consumer = RedisStreamsConsumer(logger, config, retry_manager=None)
        # Should not raise
        await consumer._clear_retry_tracking("msg-1")

    @pytest.mark.asyncio
    async def test_get_retry_count_returns_count(self, consumer_with_retry_manager, mock_retry_manager):
        mock_retry_manager.get_count.return_value = 5
        count = await consumer_with_retry_manager._get_retry_count("msg-1")
        assert count == 5
        mock_retry_manager.get_count.assert_awaited_once_with("msg-1")

    @pytest.mark.asyncio
    async def test_get_retry_count_without_manager_returns_zero(self, logger, config):
        consumer = RedisStreamsConsumer(logger, config, retry_manager=None)
        count = await consumer._get_retry_count("msg-1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_increment_retry_and_check_returns_tuple(self, consumer_with_retry_manager, mock_retry_manager):
        mock_retry_manager.increment_and_check.return_value = (2, False)
        count, should_dl = await consumer_with_retry_manager._increment_retry_and_check("msg-1")
        assert count == 2
        assert should_dl is False
        mock_retry_manager.increment_and_check.assert_awaited_once_with("msg-1", messaging_env.max_delivery_attempts)

    @pytest.mark.asyncio
    async def test_increment_retry_and_check_without_manager(self, logger, config):
        consumer = RedisStreamsConsumer(logger, config, retry_manager=None)
        count, should_dl = await consumer._increment_retry_and_check("msg-1")
        assert count == 0
        assert should_dl is False


class TestShouldDeadLetterWithRetryManager:
    """Tests for _should_dead_letter with RetryManager"""

    @pytest.mark.asyncio
    async def test_uses_retry_manager_when_available(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        mock_retry_manager.get_count.return_value = messaging_env.max_delivery_attempts

        result = await consumer_with_retry_manager._should_dead_letter("test-topic", "msg-1")

        assert result is True
        mock_retry_manager.get_count.assert_awaited_once_with("msg-1")
        mock_retry_manager.clear.assert_awaited_once_with("msg-1")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "msg-1")
        # xpending_range should NOT be called when RetryManager is used
        assert not hasattr(mock_redis, 'xpending_range') or mock_redis.xpending_range.await_count == 0

    @pytest.mark.asyncio
    async def test_does_not_dead_letter_below_threshold(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        mock_retry_manager.get_count.return_value = 1

        result = await consumer_with_retry_manager._should_dead_letter("test-topic", "msg-1")

        assert result is False
        mock_retry_manager.get_count.assert_awaited_once_with("msg-1")
        mock_retry_manager.clear.assert_not_awaited()
        mock_redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_times_delivered_without_manager(self, logger, config):
        consumer = RedisStreamsConsumer(logger, config, retry_manager=None)
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(return_value=[{"times_delivered": messaging_env.max_delivery_attempts}])
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis

        result = await consumer._should_dead_letter("test-topic", "msg-1")

        assert result is True
        mock_redis.xpending_range.assert_awaited_once()
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "msg-1")


class TestFinalizeMessage:
    """Tests for _finalize_message"""

    @pytest.mark.asyncio
    async def test_success_clears_and_acks(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        await consumer_with_retry_manager._finalize_message("test-topic", "msg-1", success=True, is_terminal=False)

        mock_retry_manager.clear.assert_awaited_once_with("msg-1")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "msg-1")

    @pytest.mark.asyncio
    async def test_terminal_error_clears_and_acks(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        await consumer_with_retry_manager._finalize_message("test-topic", "msg-1", success=False, is_terminal=True)

        mock_retry_manager.clear.assert_awaited_once_with("msg-1")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "msg-1")

    @pytest.mark.asyncio
    async def test_transient_error_increments_retry(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        mock_retry_manager.increment_and_check.return_value = (1, False)

        await consumer_with_retry_manager._finalize_message("test-topic", "msg-1", success=False, is_terminal=False)

        mock_retry_manager.increment_and_check.assert_awaited_once_with("msg-1", messaging_env.max_delivery_attempts)
        mock_retry_manager.clear.assert_not_awaited()
        mock_redis.xack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transient_error_dead_letters_after_max_attempts(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis

        mock_retry_manager.increment_and_check.return_value = (messaging_env.max_delivery_attempts, True)

        await consumer_with_retry_manager._finalize_message("test-topic", "msg-1", success=False, is_terminal=False)

        mock_retry_manager.increment_and_check.assert_awaited_once_with("msg-1", messaging_env.max_delivery_attempts)
        mock_retry_manager.clear.assert_awaited_once_with("msg-1")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "msg-1")

    @pytest.mark.asyncio
    async def test_without_retry_manager_leaves_unacked_on_transient(self, logger, config):
        consumer = RedisStreamsConsumer(logger, config, retry_manager=None)
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock()
        consumer.redis = mock_redis

        await consumer._finalize_message("test-topic", "msg-1", success=False, is_terminal=False)

        mock_redis.xack.assert_not_awaited()


class TestProcessMessageWithClassificationRetryManager:
    """Tests for _process_message_with_classification with RetryManager"""

    @pytest.mark.asyncio
    async def test_classifies_terminal_vs_transient_errors(self, consumer_with_retry_manager):
        mock_handler = AsyncMock(side_effect=ValueError("terminal error"))
        consumer_with_retry_manager.message_handler = mock_handler

        with patch("app.services.messaging.redis_streams.consumer.MessageErrorClassifier.classify_by_exception") as mock_classify:
            mock_classify.return_value = MessageErrorType.TERMINAL

            success, is_terminal = await consumer_with_retry_manager._process_message_with_classification(
                "test-topic",
                "msg-1",
                {"value": json.dumps({"eventType": "TEST", "payload": {}})}
            )

            assert success is False
            assert is_terminal is True
            mock_classify.assert_called_once()


class TestIntegrationWithConsumeLoop:
    """Integration tests for RetryManager in main consume loop"""

    @pytest.mark.asyncio
    async def test_consume_loop_uses_finalize_message(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        msg = {"value": json.dumps({"eventType": "TEST", "payload": {"x": 1}})}

        async def xreadgroup_side_effect(**kwargs):
            consumer_with_retry_manager.running = False
            return [("test-topic", [("1-0", msg)])]

        mock_redis.xreadgroup = AsyncMock(side_effect=xreadgroup_side_effect)
        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [], []))
        mock_redis.xack = AsyncMock()
        mock_redis.aclose = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis
        consumer_with_retry_manager.running = True
        consumer_with_retry_manager.message_handler = AsyncMock(return_value=True)

        await consumer_with_retry_manager._consume_loop()

        # Should clear retry tracking and ACK on success
        mock_retry_manager.clear.assert_awaited_once_with("1-0")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "1-0")

    @pytest.mark.asyncio
    async def test_drain_pending_uses_finalize_message(self, consumer_with_retry_manager, mock_retry_manager):
        mock_redis = AsyncMock()
        pending_msg = {"value": json.dumps({"eventType": "RETRY", "payload": {"id": 1}})}

        mock_redis.xautoclaim = AsyncMock(return_value=("0-0", [("2-0", pending_msg)], []))
        mock_redis.xreadgroup = AsyncMock(return_value=None)
        mock_redis.xpending_range = AsyncMock(return_value=[{"times_delivered": 1}])
        mock_redis.xack = AsyncMock()
        consumer_with_retry_manager.redis = mock_redis
        consumer_with_retry_manager.running = True
        consumer_with_retry_manager.message_handler = AsyncMock(return_value=True)

        mock_retry_manager.get_count.return_value = 1

        await consumer_with_retry_manager._drain_pending()

        # Should clear retry tracking and ACK on success
        mock_retry_manager.clear.assert_awaited_once_with("2-0")
        mock_redis.xack.assert_awaited_once_with("test-topic", "test-group", "2-0")
