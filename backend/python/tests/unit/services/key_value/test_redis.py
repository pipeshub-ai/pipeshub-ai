"""Tests for app.services.key_value.redis.redis.RedisService"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.key_value.redis.redis import RedisService


@pytest.fixture
def logger():
    return logging.getLogger("test-redis")


@pytest.fixture
def mock_redis_client():
    """Fully mocked async Redis client."""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_config():
    return MagicMock()


@pytest.fixture
def service(logger, mock_redis_client, mock_config):
    return RedisService(logger, mock_redis_client, mock_config)


# ---------------------------------------------------------------------------
# set()
# ---------------------------------------------------------------------------


class TestSet:
    """Tests for RedisService.set()."""

    @pytest.mark.asyncio
    async def test_set_string_value(self, service, mock_redis_client):
        """String values are stored as-is."""
        result = await service.set("mykey", "myvalue")
        assert result is True
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:mykey", "myvalue", ex=86400
        )

    @pytest.mark.asyncio
    async def test_set_dict_value_json_serialized(self, service, mock_redis_client):
        """Dict values are JSON-serialized before storing."""
        data = {"name": "test", "count": 42}
        result = await service.set("dictkey", data)
        assert result is True
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:dictkey", json.dumps(data), ex=86400
        )

    @pytest.mark.asyncio
    async def test_set_list_value_json_serialized(self, service, mock_redis_client):
        """List values are JSON-serialized before storing."""
        data = [1, 2, 3]
        result = await service.set("listkey", data)
        assert result is True
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:listkey", json.dumps(data), ex=86400
        )

    @pytest.mark.asyncio
    async def test_set_custom_expiration(self, service, mock_redis_client):
        """Custom expiration is forwarded to redis."""
        await service.set("k", "v", expire=3600)
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:k", "v", ex=3600
        )

    @pytest.mark.asyncio
    async def test_set_returns_false_on_exception(self, service, mock_redis_client):
        """Returns False when redis raises an exception."""
        mock_redis_client.set.side_effect = Exception("connection lost")
        result = await service.set("k", "v")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_none_client_returns_false(self, logger, mock_config):
        """Returns False when redis_client is None."""
        svc = RedisService(logger, None, mock_config)
        result = await svc.set("k", "v")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_integer_value_not_serialized(self, service, mock_redis_client):
        """Integer values (not dict/list) are stored as-is."""
        await service.set("intkey", 42)
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:intkey", 42, ex=86400
        )


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    """Tests for RedisService.get()."""

    @pytest.mark.asyncio
    async def test_get_json_dict_value(self, service, mock_redis_client):
        """Values starting with '{' are JSON-deserialized."""
        stored = json.dumps({"name": "test"})
        mock_redis_client.get.return_value = stored
        result = await service.get("dictkey")
        assert result == {"name": "test"}
        mock_redis_client.get.assert_awaited_once_with("redis_service:dictkey")

    @pytest.mark.asyncio
    async def test_get_json_list_value(self, service, mock_redis_client):
        """Values starting with '[' are JSON-deserialized."""
        stored = json.dumps([1, 2, 3])
        mock_redis_client.get.return_value = stored
        result = await service.get("listkey")
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_plain_string_value(self, service, mock_redis_client):
        """Plain strings are returned as-is."""
        mock_redis_client.get.return_value = "just a string"
        result = await service.get("strkey")
        assert result == "just a string"

    @pytest.mark.asyncio
    async def test_get_none_value(self, service, mock_redis_client):
        """None return from redis is handled properly."""
        mock_redis_client.get.return_value = None
        result = await service.get("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_on_exception(self, service, mock_redis_client):
        """Returns None when redis raises an exception."""
        mock_redis_client.get.side_effect = Exception("timeout")
        result = await service.get("k")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_none_client_returns_none(self, logger, mock_config):
        """Returns None when redis_client is None."""
        svc = RedisService(logger, None, mock_config)
        result = await svc.get("k")
        assert result is None


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


class TestDelete:
    """Tests for RedisService.delete()."""

    @pytest.mark.asyncio
    async def test_delete_success(self, service, mock_redis_client):
        """Successful delete returns True."""
        mock_redis_client.delete.return_value = 1
        result = await service.delete("mykey")
        assert result is True
        mock_redis_client.delete.assert_awaited_once_with("redis_service:mykey")

    @pytest.mark.asyncio
    async def test_delete_returns_false_on_exception(self, service, mock_redis_client):
        """Returns False when redis raises an exception."""
        mock_redis_client.delete.side_effect = Exception("error")
        result = await service.delete("k")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_none_client_returns_false(self, logger, mock_config):
        """Returns False when redis_client is None."""
        svc = RedisService(logger, None, mock_config)
        result = await svc.delete("k")
        assert result is False


# ---------------------------------------------------------------------------
# Key prefix
# ---------------------------------------------------------------------------


class TestKeyPrefix:
    """Verify 'redis_service:' prefix is prepended to all keys."""

    @pytest.mark.asyncio
    async def test_set_prepends_prefix(self, service, mock_redis_client):
        await service.set("foo", "bar")
        call_args = mock_redis_client.set.call_args
        assert call_args[0][0] == "redis_service:foo"

    @pytest.mark.asyncio
    async def test_get_prepends_prefix(self, service, mock_redis_client):
        mock_redis_client.get.return_value = None
        await service.get("foo")
        mock_redis_client.get.assert_awaited_once_with("redis_service:foo")

    @pytest.mark.asyncio
    async def test_delete_prepends_prefix(self, service, mock_redis_client):
        await service.delete("foo")
        mock_redis_client.delete.assert_awaited_once_with("redis_service:foo")

    def test_prefix_attribute(self, service):
        assert service.prefix == "redis_service:"


# ---------------------------------------------------------------------------
# store_progress / get_progress
# ---------------------------------------------------------------------------


class TestProgress:
    """Tests for store_progress() and get_progress() JSON wrappers."""

    @pytest.mark.asyncio
    async def test_store_progress_serializes_to_json(self, service, mock_redis_client):
        """store_progress() JSON-dumps the progress dict and stores it."""
        progress = {"status": "running", "percent": 50}
        result = await service.store_progress(progress)
        assert result is True
        # store_progress calls set("sync_progress", json.dumps(progress))
        # and set() will see a string (not dict/list), so it stores it as-is
        expected_value = json.dumps(progress)
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:sync_progress", expected_value, ex=86400
        )

    @pytest.mark.asyncio
    async def test_get_progress_double_parse_bug(self, service, mock_redis_client):
        """get_progress() has a double-deserialization issue when get() auto-parses JSON.

        store_progress() calls set() with json.dumps(progress) -- a JSON string.
        get() then auto-parses values starting with '{', returning a dict.
        get_progress() calls json.loads() on that dict, raising TypeError.

        This test documents the existing behavior: when the raw redis value
        starts with '{', get() returns a dict and get_progress() fails.
        """
        progress = {"status": "done", "percent": 100}
        mock_redis_client.get.return_value = json.dumps(progress)
        with pytest.raises(TypeError):
            await service.get_progress()

    @pytest.mark.asyncio
    async def test_store_progress_stores_json_string(self, service, mock_redis_client):
        """store_progress passes json.dumps(progress) to set(), which stores it as a string."""
        progress = {"status": "running", "percent": 75}
        await service.store_progress(progress)
        expected = json.dumps(progress)
        mock_redis_client.set.assert_awaited_once_with(
            "redis_service:sync_progress", expected, ex=86400
        )

    @pytest.mark.asyncio
    async def test_get_progress_returns_none_when_no_data(self, service, mock_redis_client):
        """get_progress returns None when there is no stored progress."""
        mock_redis_client.get.return_value = None
        result = await service.get_progress()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_progress_with_string_value(self, service, mock_redis_client):
        """get_progress correctly parses when get() returns a plain JSON string."""
        # Simulate a scenario where redis returns a value that get() does not auto-parse
        # (e.g., the value doesn't start with '{' because of some other path).
        # We'll patch 'get' directly on the service to control its return.
        progress = {"status": "complete", "percent": 100}
        json_str = json.dumps(progress)

        # Patch service.get to return the JSON string directly
        service.get = AsyncMock(return_value=json_str)
        result = await service.get_progress()
        assert result == progress


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Tests for connect() and disconnect()."""

    @pytest.mark.asyncio
    async def test_connect_success(self, service, mock_redis_client):
        mock_redis_client.ping.return_value = True
        result = await service.connect()
        assert result is True

    @pytest.mark.asyncio
    async def test_connect_failure(self, service, mock_redis_client):
        mock_redis_client.ping.side_effect = Exception("refused")
        result = await service.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_none_client(self, logger, mock_config):
        svc = RedisService(logger, None, mock_config)
        result = await svc.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect_success(self, service, mock_redis_client):
        result = await service.disconnect()
        assert result is True
        mock_redis_client.close.assert_awaited_once()
        assert service.redis_client is None

    @pytest.mark.asyncio
    async def test_disconnect_none_client(self, logger, mock_config):
        svc = RedisService(logger, None, mock_config)
        result = await svc.disconnect()
        assert result is False


# ---------------------------------------------------------------------------
# create() factory method
# ---------------------------------------------------------------------------


class TestCreateFactory:
    """Tests for RedisService.create() class method."""

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_success(self, mock_build_url, mock_aioredis, logger):
        """create() builds URL, creates client, pings, and returns service."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "host": "localhost",
            "port": 6379,
            "password": "secret",
        }
        mock_build_url.return_value = "redis://:secret@localhost:6379/0"

        mock_client = AsyncMock()
        mock_client.ping.return_value = True
        # from_url is called with await, so it must be an AsyncMock
        mock_aioredis.from_url = AsyncMock(return_value=mock_client)

        svc = await RedisService.create(logger, mock_config_service)

        assert isinstance(svc, RedisService)
        assert svc.redis_client is mock_client
        mock_build_url.assert_called_once_with({"host": "localhost", "port": 6379, "password": "secret"})
        mock_aioredis.from_url.assert_awaited_once()
        mock_client.ping.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_config_none_raises(self, mock_build_url, mock_aioredis, logger):
        """create() raises when config returns None."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = None

        with pytest.raises(Exception, match="Redis configuration not found"):
            await RedisService.create(logger, mock_config_service)

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_config_not_dict_raises(self, mock_build_url, mock_aioredis, logger):
        """create() raises when config returns a non-dict value."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = "not-a-dict"

        with pytest.raises(Exception, match="Redis configuration not found"):
            await RedisService.create(logger, mock_config_service)

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_empty_dict_config_raises(self, mock_build_url, mock_aioredis, logger):
        """create() raises when config returns an empty dict (falsy)."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {}

        with pytest.raises(Exception, match="Redis configuration not found"):
            await RedisService.create(logger, mock_config_service)

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_ping_failure_raises(self, mock_build_url, mock_aioredis, logger):
        """create() raises when Redis ping fails (connect returns False)."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "host": "localhost",
            "port": 6379,
        }
        mock_build_url.return_value = "redis://localhost:6379/0"

        mock_client = AsyncMock()
        mock_client.ping.side_effect = Exception("connection refused")
        mock_aioredis.from_url = AsyncMock(return_value=mock_client)

        with pytest.raises(Exception, match="Failed to connect to Redis"):
            await RedisService.create(logger, mock_config_service)

    @pytest.mark.asyncio
    @patch("app.services.key_value.redis.redis.aioredis")
    @patch("app.services.key_value.redis.redis.build_redis_url")
    async def test_create_from_url_raises(self, mock_build_url, mock_aioredis, logger):
        """create() raises when aioredis.from_url fails."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config.return_value = {
            "host": "localhost",
            "port": 6379,
        }
        mock_build_url.return_value = "redis://localhost:6379/0"
        mock_aioredis.from_url = AsyncMock(side_effect=Exception("could not connect"))

        with pytest.raises(Exception):
            await RedisService.create(logger, mock_config_service)
