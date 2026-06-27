"""Unit tests for RetryManager."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.messaging.retry_manager import RetryManager


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.ping = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock(return_value=1)
    redis.mget = AsyncMock(return_value=[])
    redis.aclose = AsyncMock()
    return redis


@pytest.fixture
def mock_redis_config():
    """Create a mock Redis config."""
    config = MagicMock()
    config.host = "localhost"
    config.port = 6379
    config.password = None
    config.db = 0
    return config


class TestRetryManagerInit:
    """Tests for RetryManager initialization."""

    def test_init_with_redis_client(self, mock_logger, mock_redis):
        """Test initialization with existing Redis client."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)
        assert manager._redis is mock_redis
        assert manager._owns_client is False

    def test_init_with_redis_config(self, mock_logger, mock_redis_config):
        """Test initialization with Redis config."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)
        assert manager._redis is None
        assert manager._redis_config is mock_redis_config
        assert manager._owns_client is True

    def test_init_without_redis_raises_error(self, mock_logger):
        """Test that initialization without Redis raises ValueError."""
        with pytest.raises(ValueError, match="Either redis_client or redis_config"):
            RetryManager(mock_logger)

    def test_init_with_custom_ttl(self, mock_logger, mock_redis):
        """Test initialization with custom TTL."""
        manager = RetryManager(mock_logger, redis_client=mock_redis, ttl_seconds=3600)
        assert manager.ttl_seconds == 3600


class TestRetryManagerBuildKey:
    """Tests for key building."""

    def test_build_key(self, mock_logger, mock_redis):
        """Test key format."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)
        key = manager._build_key("topic-0-12345")
        assert key == "messaging:retry:topic-0-12345"

    def test_build_key_with_special_chars(self, mock_logger, mock_redis):
        """Test key with special characters in message ID."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)
        key = manager._build_key("my-topic-0-99999")
        assert key == "messaging:retry:my-topic-0-99999"


class TestRetryManagerIncrementAndCheck:
    """Tests for increment_and_check method."""

    @pytest.mark.asyncio
    async def test_increment_first_attempt(self, mock_logger, mock_redis):
        """Test first attempt increments to 1."""
        mock_redis.incr = AsyncMock(return_value=1)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count, should_dead_letter = await manager.increment_and_check("msg-1", 3)

        assert count == 1
        assert should_dead_letter is False
        mock_redis.incr.assert_called_once_with("messaging:retry:msg-1")
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_increment_second_attempt(self, mock_logger, mock_redis):
        """Test second attempt increments to 2."""
        mock_redis.incr = AsyncMock(return_value=2)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count, should_dead_letter = await manager.increment_and_check("msg-1", 3)

        assert count == 2
        assert should_dead_letter is False

    @pytest.mark.asyncio
    async def test_increment_reaches_max(self, mock_logger, mock_redis):
        """Test when max attempts reached, should_dead_letter is True."""
        mock_redis.incr = AsyncMock(return_value=3)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count, should_dead_letter = await manager.increment_and_check("msg-1", 3)

        assert count == 3
        assert should_dead_letter is True

    @pytest.mark.asyncio
    async def test_increment_exceeds_max(self, mock_logger, mock_redis):
        """Test when count exceeds max attempts."""
        mock_redis.incr = AsyncMock(return_value=5)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count, should_dead_letter = await manager.increment_and_check("msg-1", 3)

        assert count == 5
        assert should_dead_letter is True

    @pytest.mark.asyncio
    async def test_increment_not_initialized_raises(self, mock_logger, mock_redis_config):
        """Test that calling increment without initialization raises."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)

        with pytest.raises(RuntimeError, match="not initialized"):
            await manager.increment_and_check("msg-1", 3)


class TestRetryManagerGetCount:
    """Tests for get_count method."""

    @pytest.mark.asyncio
    async def test_get_count_existing(self, mock_logger, mock_redis):
        """Test getting count for existing message."""
        mock_redis.get = AsyncMock(return_value="2")
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count = await manager.get_count("msg-1")

        assert count == 2
        mock_redis.get.assert_called_once_with("messaging:retry:msg-1")

    @pytest.mark.asyncio
    async def test_get_count_not_found(self, mock_logger, mock_redis):
        """Test getting count for non-existent message returns 0."""
        mock_redis.get = AsyncMock(return_value=None)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        count = await manager.get_count("msg-1")

        assert count == 0


class TestRetryManagerClear:
    """Tests for clear method."""

    @pytest.mark.asyncio
    async def test_clear_existing(self, mock_logger, mock_redis):
        """Test clearing existing key."""
        mock_redis.delete = AsyncMock(return_value=1)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        await manager.clear("msg-1")

        mock_redis.delete.assert_called_once_with("messaging:retry:msg-1")

    @pytest.mark.asyncio
    async def test_clear_non_existent(self, mock_logger, mock_redis):
        """Test clearing non-existent key doesn't raise."""
        mock_redis.delete = AsyncMock(return_value=0)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        # Should not raise
        await manager.clear("msg-1")


class TestRetryManagerClearBatch:
    """Tests for clear_batch method."""

    @pytest.mark.asyncio
    async def test_clear_batch(self, mock_logger, mock_redis):
        """Test clearing multiple keys."""
        mock_redis.delete = AsyncMock(return_value=3)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        deleted = await manager.clear_batch(["msg-1", "msg-2", "msg-3"])

        assert deleted == 3
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_batch_empty_list(self, mock_logger, mock_redis):
        """Test clearing empty list returns 0."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        deleted = await manager.clear_batch([])

        assert deleted == 0
        mock_redis.delete.assert_not_called()


class TestRetryManagerHasPendingRetries:
    """Tests for has_pending_retries method."""

    @pytest.mark.asyncio
    async def test_has_pending_retries_true(self, mock_logger, mock_redis):
        """Test when some messages have pending retries."""
        mock_redis.mget = AsyncMock(return_value=[None, "2", None])
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        has_pending = await manager.has_pending_retries(["msg-1", "msg-2", "msg-3"])

        assert has_pending is True

    @pytest.mark.asyncio
    async def test_has_pending_retries_false(self, mock_logger, mock_redis):
        """Test when no messages have pending retries."""
        mock_redis.mget = AsyncMock(return_value=[None, None, None])
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        has_pending = await manager.has_pending_retries(["msg-1", "msg-2", "msg-3"])

        assert has_pending is False

    @pytest.mark.asyncio
    async def test_has_pending_retries_empty_list(self, mock_logger, mock_redis):
        """Test with empty list returns False."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        has_pending = await manager.has_pending_retries([])

        assert has_pending is False


class TestRetryManagerInitializeCleanup:
    """Tests for initialize and cleanup methods."""

    @pytest.mark.asyncio
    async def test_initialize_creates_connection(self, mock_logger, mock_redis_config):
        """Test that initialize creates Redis connection."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)

        with patch("app.services.messaging.retry_manager.Redis") as MockRedis:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            MockRedis.return_value = mock_client

            await manager.initialize()

            MockRedis.assert_called_once()
            mock_client.ping.assert_called_once()
            assert manager._redis is mock_client

    @pytest.mark.asyncio
    async def test_initialize_with_existing_client_noop(self, mock_logger, mock_redis):
        """Test that initialize with existing client is a no-op."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        await manager.initialize()

        # Should not call ping again
        mock_redis.ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_closes_owned_connection(self, mock_logger, mock_redis_config):
        """Test that cleanup closes connection when we own it."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)

        with patch("app.services.messaging.retry_manager.Redis") as MockRedis:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.aclose = AsyncMock()
            MockRedis.return_value = mock_client

            await manager.initialize()
            await manager.cleanup()

            mock_client.aclose.assert_called_once()
            assert manager._redis is None

    @pytest.mark.asyncio
    async def test_cleanup_does_not_close_provided_connection(self, mock_logger, mock_redis):
        """Test that cleanup doesn't close provided connection."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        await manager.cleanup()

        mock_redis.aclose.assert_not_called()
