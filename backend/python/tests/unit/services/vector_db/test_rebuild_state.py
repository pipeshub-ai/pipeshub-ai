"""Unit tests for app.services.vector_db.rebuild_state.

Covers the connection-provider seam (R14/R21): `_redis_from_config` must go
through `get_redis_provider().create_client(...)`, never
`redis.asyncio.Redis(...)` directly, so a MemoryDB/cluster provider can be
swapped in via `REDIS_MODE` with zero changes to this module.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisConfig
from app.services.vector_db.rebuild_state import (
    RebuildJobLock,
    _redis_from_config,
    get_cleanup_phase,
    mark_cleanup_phase,
    redis_from_config_service,
    set_cleanup_phase,
)


class TestRedisFromConfig:
    def test_uses_connection_provider_not_direct_client(self):
        """`_redis_from_config` must delegate to the provider (never
        `redis.asyncio.Redis(...)`), passing the RedisConfig fields through."""
        mock_client = MagicMock()
        mock_provider = MagicMock()
        mock_provider.create_client = MagicMock(return_value=mock_client)

        with patch(
            "app.services.redis.connection_provider_factory.get_redis_provider",
            return_value=mock_provider,
        ) as mock_get_provider:
            result = _redis_from_config(
                RedisConfig(host="redis.local", port=6380, password="secret", db=2)
            )

        assert result is mock_client
        mock_provider.create_client.assert_called_once()
        config_arg = mock_get_provider.call_args[0][0]
        assert config_arg.host == "redis.local"
        assert config_arg.port == 6380
        assert config_arg.password == "secret"
        assert config_arg.db == 2


class TestRedisFromConfigService:
    @pytest.mark.asyncio
    async def test_builds_client_from_config_service(self):
        config_service = AsyncMock()
        config_service.get_redis_config = AsyncMock(
            return_value=RedisConfig(host="localhost", port=6379)
        )
        mock_client = MagicMock()

        with patch(
            "app.services.vector_db.rebuild_state._redis_from_config",
            return_value=mock_client,
        ):
            result = await redis_from_config_service(config_service)

        assert result is mock_client


class TestRebuildJobLock:
    @pytest.mark.asyncio
    async def test_try_acquire_sets_nx(self):
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        lock = RebuildJobLock(redis, ttl_seconds=60, token="tok-1")

        assert await lock.try_acquire() is True
        redis.set.assert_awaited_once_with(
            "vector_store_rebuild:job", "tok-1", nx=True, ex=60
        )

    @pytest.mark.asyncio
    async def test_refresh_uses_compare_and_expire_lua(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=1)
        lock = RebuildJobLock(redis, ttl_seconds=60, token="tok-1")

        assert await lock.refresh() is True
        redis.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_uses_compare_and_delete_lua(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=1)
        lock = RebuildJobLock(redis, ttl_seconds=60, token="tok-1")

        await lock.release()
        redis.eval.assert_awaited_once()


class TestCleanupPhase:
    @pytest.mark.asyncio
    async def test_get_cleanup_phase_returns_none_when_missing(self):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        assert await get_cleanup_phase(redis) is None

    @pytest.mark.asyncio
    async def test_get_cleanup_phase_returns_value(self):
        redis = AsyncMock()
        redis.get = AsyncMock(return_value="ready")
        assert await get_cleanup_phase(redis) == "ready"

    @pytest.mark.asyncio
    async def test_set_cleanup_phase_sets_with_ttl(self):
        redis = AsyncMock()
        await set_cleanup_phase(redis, "dropping")
        redis.set.assert_awaited_once()


class TestMarkCleanupPhase:
    @pytest.mark.asyncio
    async def test_uses_provided_client_and_does_not_close_it(self):
        redis = AsyncMock()
        await mark_cleanup_phase(config_service=AsyncMock(), phase="ready", redis=redis)
        redis.set.assert_awaited_once()
        redis.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_and_closes_owned_client_when_none_provided(self):
        owned_client = AsyncMock()
        config_service = AsyncMock()
        config_service.get_redis_config = AsyncMock(
            return_value=RedisConfig(host="localhost", port=6379)
        )

        with patch(
            "app.services.vector_db.rebuild_state._redis_from_config",
            return_value=owned_client,
        ):
            await mark_cleanup_phase(config_service=config_service, phase="ready")

        owned_client.set.assert_awaited_once()
        owned_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_and_logs_without_raising_on_persistent_failure(self):
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
        logger = MagicMock()

        with patch("app.services.vector_db.rebuild_state.asyncio.sleep", AsyncMock()):
            # Should not raise, even though every attempt fails.
            await mark_cleanup_phase(
                config_service=AsyncMock(), phase="failed", redis=redis, logger=logger
            )

        assert redis.set.await_count == 3
        logger.error.assert_called_once()
