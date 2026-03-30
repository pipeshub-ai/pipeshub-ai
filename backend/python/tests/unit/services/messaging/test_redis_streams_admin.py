"""
Tests for RedisStreamsAdmin:
  - ensure_topics_exist (creates streams, skips existing, handles errors)
  - list_topics (scans keys, filters by stream type)
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.services.messaging.config import REQUIRED_TOPICS, RedisStreamsConfig
from app.services.messaging.redis_streams.admin import RedisStreamsAdmin


@pytest.fixture
def logger():
    return logging.getLogger("test_redis_streams_admin")


@pytest.fixture
def config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        password=None,
        db=0,
    )


@pytest.fixture
def admin(logger, config):
    return RedisStreamsAdmin(logger, config)


class TestEnsureTopicsExist:
    @pytest.mark.asyncio
    async def test_creates_missing_streams(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_destroy = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist(["stream-a", "stream-b"])

        assert mock_redis.xgroup_create.call_count == 2
        assert mock_redis.xgroup_destroy.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_existing_streams(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist(["existing-stream"])

        mock_redis.xgroup_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_per_topic_error_gracefully(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist(["bad-stream"])

    @pytest.mark.asyncio
    async def test_uses_default_topics_when_none_provided(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.xgroup_create = AsyncMock()
        mock_redis.xgroup_destroy = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist()

        assert mock_redis.exists.call_count == len(REQUIRED_TOPICS)

    @pytest.mark.asyncio
    async def test_closes_redis_even_on_per_topic_error(self, logger, config):
        """Per-topic errors are caught and logged, not re-raised.
        Redis is still closed in the finally block."""
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(side_effect=Exception("Redis down"))
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist(["stream-a"])

        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_closes_redis_on_success(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.exists = AsyncMock(return_value=1)
        mock_redis.close = AsyncMock()

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.ensure_topics_exist(["s"])

        mock_redis.close.assert_awaited_once()


class TestListTopics:
    @pytest.mark.asyncio
    async def test_returns_only_stream_keys(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()

        async def mock_scan_iter():
            for key in ["stream-1", "hash-key", "stream-2"]:
                yield key

        mock_redis.scan_iter = mock_scan_iter

        type_results = {"stream-1": "stream", "hash-key": "hash", "stream-2": "stream"}
        mock_redis.type = AsyncMock(side_effect=lambda k: type_results.get(k, "string"))

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            result = await admin.list_topics()

        assert result == ["stream-1", "stream-2"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_streams(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()

        async def mock_scan_iter():
            return
            yield

        mock_redis.scan_iter = mock_scan_iter

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            result = await admin.list_topics()

        assert result == []

    @pytest.mark.asyncio
    async def test_closes_redis_after_listing(self, logger, config):
        admin = RedisStreamsAdmin(logger, config)
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock()

        async def mock_scan_iter():
            return
            yield

        mock_redis.scan_iter = mock_scan_iter

        with patch(
            "app.services.messaging.redis_streams.admin.Redis",
            return_value=mock_redis,
        ):
            await admin.list_topics()

        mock_redis.close.assert_awaited_once()
