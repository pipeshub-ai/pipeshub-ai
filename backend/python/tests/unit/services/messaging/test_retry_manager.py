"""Unit tests for RetryManager."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.retry_manager import RetryManager
from app.services.redis.standalone_provider import StandaloneRedisProvider


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


def _make_pipeline(execute_results: list) -> MagicMock:
    """A pipeline mock matching redis-py: queueing calls (``delete``/``get``)
    are synchronous, ``execute()`` is the only awaited call, and the object
    is used as an async context manager."""
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=execute_results)
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=None)
    return pipeline


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
    # pipeline() itself is synchronous on a real Redis client; only
    # execute() (and __aenter__/__aexit__) are awaited (R5).
    redis.pipeline = MagicMock(return_value=_make_pipeline([]))
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
        """Test clearing multiple keys via a pipelined per-key DELETE (R5)."""
        pipeline = _make_pipeline([1, 1, 1])
        mock_redis.pipeline = MagicMock(return_value=pipeline)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        deleted = await manager.clear_batch(["msg-1", "msg-2", "msg-3"])

        assert deleted == 3
        assert pipeline.delete.call_count == 3
        pipeline.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_batch_empty_list(self, mock_logger, mock_redis):
        """Test clearing empty list returns 0."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        deleted = await manager.clear_batch([])

        assert deleted == 0
        mock_redis.pipeline.assert_not_called()


class TestRetryManagerHasPendingRetries:
    """Tests for has_pending_retries method."""

    @pytest.mark.asyncio
    async def test_has_pending_retries_true(self, mock_logger, mock_redis):
        """Test when some messages have pending retries (pipelined GET, R5)."""
        pipeline = _make_pipeline([None, "2", None])
        mock_redis.pipeline = MagicMock(return_value=pipeline)
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        has_pending = await manager.has_pending_retries(["msg-1", "msg-2", "msg-3"])

        assert has_pending is True
        assert pipeline.get.call_count == 3

    @pytest.mark.asyncio
    async def test_has_pending_retries_false(self, mock_logger, mock_redis):
        """Test when no messages have pending retries."""
        pipeline = _make_pipeline([None, None, None])
        mock_redis.pipeline = MagicMock(return_value=pipeline)
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
        """Test that initialize builds a per-loop client registry and pings it."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()

        with patch.object(
            StandaloneRedisProvider, "create_client", lambda self, *a, **k: mock_client
        ):
            await manager.initialize()

            mock_client.ping.assert_called_once()
            assert manager._client() is mock_client

    @pytest.mark.asyncio
    async def test_initialize_picks_up_redis_key_namespace(
        self, mock_logger, mock_redis_config, monkeypatch
    ):
        """REDIS_KEY_NAMESPACE (R9) is read from the provider once
        initialized and applied by `_build_key` -- never as a client-level
        prefix."""
        monkeypatch.setenv("REDIS_KEY_NAMESPACE", "tenant-a")
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()

        with patch.object(
            StandaloneRedisProvider, "create_client", lambda self, *a, **k: mock_client
        ):
            await manager.initialize()

        assert manager._build_key("msg-1") == "tenant-a:messaging:retry:msg-1"

    @pytest.mark.asyncio
    async def test_initialize_with_existing_client_noop(self, mock_logger, mock_redis):
        """Test that initialize with existing client is a no-op."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        await manager.initialize()

        # Should not call ping again
        mock_redis.ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_closes_owned_connection(self, mock_logger, mock_redis_config):
        """Test that cleanup closes every per-loop client we own."""
        manager = RetryManager(mock_logger, redis_config=mock_redis_config)
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.aclose = AsyncMock()

        with patch.object(
            StandaloneRedisProvider, "create_client", lambda self, *a, **k: mock_client
        ):
            await manager.initialize()
            await manager.cleanup()

            mock_client.aclose.assert_called_once()
            assert manager._registry is None

    @pytest.mark.asyncio
    async def test_cleanup_does_not_close_provided_connection(self, mock_logger, mock_redis):
        """Test that cleanup doesn't close provided connection."""
        manager = RetryManager(mock_logger, redis_client=mock_redis)

        await manager.cleanup()

        mock_redis.aclose.assert_not_called()


class TestCrossSlotSafety:
    """Regression tests for R5: message ids in the same batch routinely land
    in different Redis Cluster hash slots. `has_pending_retries`/`clear_batch`
    must survive that; a naive `MGET`/`DEL k1 k2 ...` would raise CROSSSLOT
    against a real cluster or MemoryDB. `FakeClusterRedis` enforces the same
    slot rule `fakeredis` does not, so this fails loudly if either method
    regresses back to a multi-key command.
    """

    @staticmethod
    def _ids_spanning_multiple_slots() -> list[str]:
        """Message ids picked to hash to at least two distinct slots -- the
        exact slot values do not matter, only that they differ."""
        from redis.crc import key_slot

        from app.services.messaging.retry_manager import RetryManager

        candidates = [f"msg-{i}" for i in range(50)]
        keys = [f"{RetryManager.KEY_PREFIX}:{mid}" for mid in candidates]
        slots = {key_slot(k.encode()) for k in keys}
        assert len(slots) > 1, "test fixture must exercise more than one hash slot"
        return candidates

    @pytest.mark.asyncio
    async def test_has_pending_retries_survives_cross_slot_ids(self, mock_logger):
        from tests.support.fake_cluster_redis import FakeClusterRedis

        fake = FakeClusterRedis()
        message_ids = self._ids_spanning_multiple_slots()
        manager = RetryManager(mock_logger, redis_client=fake)
        await manager.increment_and_check(message_ids[0], max_attempts=5)

        # Must not raise ClusterCrossSlotError even though the ids span
        # multiple slots.
        has_pending = await manager.has_pending_retries(message_ids)
        assert has_pending is True

    @pytest.mark.asyncio
    async def test_clear_batch_survives_cross_slot_ids(self, mock_logger):
        from tests.support.fake_cluster_redis import FakeClusterRedis

        fake = FakeClusterRedis()
        message_ids = self._ids_spanning_multiple_slots()
        manager = RetryManager(mock_logger, redis_client=fake)
        for mid in message_ids:
            await manager.increment_and_check(mid, max_attempts=5)

        # Must not raise ClusterCrossSlotError even though the ids span
        # multiple slots.
        deleted = await manager.clear_batch(message_ids)
        assert deleted == len(message_ids)

    @pytest.mark.asyncio
    async def test_fake_cluster_redis_actually_enforces_crossslot(self):
        """Sanity check on the fake itself: a real multi-key MGET across
        slots must still raise, or the two tests above would be vacuous."""
        from redis.exceptions import ClusterCrossSlotError

        from tests.support.fake_cluster_redis import FakeClusterRedis

        fake = FakeClusterRedis()
        message_ids = self._ids_spanning_multiple_slots()
        keys = [f"{RetryManager.KEY_PREFIX}:{mid}" for mid in message_ids]

        with pytest.raises(ClusterCrossSlotError):
            await fake.mget(keys)
