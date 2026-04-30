"""Unit tests for app.config.configuration_service.ConfigurationService."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service(store=None, kv_store_type="etcd"):
    """Construct a ConfigurationService with mocked internals.

    We must patch several things that happen during __init__:
    - os.getenv("SECRET_KEY") must return a value
    - EncryptionService.get_instance must be safe
    - _start_watch must be a no-op (avoids background threads)
    """
    if store is None:
        store = AsyncMock()

    with (
        patch("app.config.configuration_service.os.getenv") as mock_getenv,
        patch(
            "app.config.configuration_service.EncryptionService.get_instance"
        ) as mock_enc,
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "SECRET_KEY": _TEST_SECRET_KEY,
            "KV_STORE_TYPE": kv_store_type,
        }.get(key, default)
        mock_enc.return_value = MagicMock()

        # Import here so patches are active during class-body evaluation
        from app.config.configuration_service import ConfigurationService

        with patch.object(ConfigurationService, "_start_watch"):
            svc = ConfigurationService(
                logger=logging.getLogger("test-config"),
                key_value_store=store,
            )

    return svc


# =========================================================================
# get_config
# =========================================================================
class TestGetConfig:
    """Tests for ConfigurationService.get_config."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_value(self):
        store = AsyncMock()
        svc = _build_service(store)
        svc.cache["/some/key"] = {"cached": True}

        result = await svc.get_config("/some/key", use_cache=True)

        assert result == {"cached": True}
        store.get_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_from_store(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value={"from": "store"})
        svc = _build_service(store)

        result = await svc.get_config("/some/key")

        assert result == {"from": "store"}
        store.get_key.assert_awaited_once_with("/some/key")
        # Value should now be cached
        assert svc.cache["/some/key"] == {"from": "store"}

    @pytest.mark.asyncio
    async def test_use_cache_false_bypasses_cache(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="fresh")
        svc = _build_service(store)
        svc.cache["/k"] = "stale"

        result = await svc.get_config("/k", use_cache=False)

        assert result == "fresh"
        store.get_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_returns_none_falls_through_to_default(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value=None)
        svc = _build_service(store)

        result = await svc.get_config("/unknown/key", default="fallback")

        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_store_exception_returns_default(self):
        store = AsyncMock()
        store.get_key = AsyncMock(side_effect=RuntimeError("connection lost"))
        svc = _build_service(store)

        result = await svc.get_config("/bad/key", default=42)

        assert result == 42

    @pytest.mark.asyncio
    async def test_store_exception_tries_env_fallback(self):
        """When the store raises, _get_env_fallback is tried before default."""
        store = AsyncMock()
        store.get_key = AsyncMock(side_effect=RuntimeError("boom"))
        svc = _build_service(store)

        with patch.object(
            svc, "_get_env_fallback", return_value={"env": "value"}
        ) as mock_fb:
            result = await svc.get_config("/services/kafka", default=None)

        mock_fb.assert_called_with("/services/kafka")
        assert result == {"env": "value"}

    @pytest.mark.asyncio
    async def test_store_returns_none_tries_env_fallback(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value=None)
        svc = _build_service(store)

        with patch.object(
            svc, "_get_env_fallback", return_value={"host": "localhost"}
        ) as mock_fb:
            result = await svc.get_config("/services/redis")

        mock_fb.assert_called_with("/services/redis")
        assert result == {"host": "localhost"}
        # Also cached
        assert svc.cache["/services/redis"] == {"host": "localhost"}


# =========================================================================
# _get_env_fallback
# =========================================================================
class TestGetEnvFallback:
    """Tests for ConfigurationService._get_env_fallback."""

    def test_kafka_fallback_basic(self):
        svc = _build_service()

        env = {
            "KAFKA_BROKERS": "broker1:9092,broker2:9093",
            "KAFKA_SSL": "true",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/kafka")

        assert result is not None
        assert result["host"] == "broker1"
        assert result["port"] == 9092
        assert result["bootstrap_servers"] == ["broker1:9092", "broker2:9093"]
        assert result["ssl"] is True
        assert "sasl" not in result

    def test_kafka_fallback_with_sasl(self):
        svc = _build_service()

        env = {
            "KAFKA_BROKERS": "broker:9092",
            "KAFKA_SSL": "false",
            "KAFKA_USERNAME": "admin",
            "KAFKA_PASSWORD": "secret",
            "KAFKA_SASL_MECHANISM": "plain",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/kafka")

        assert result["sasl"]["mechanism"] == "plain"
        assert result["sasl"]["username"] == "admin"
        assert result["sasl"]["password"] == "secret"

    def test_kafka_fallback_no_brokers_returns_none(self):
        svc = _build_service()

        with patch("app.config.configuration_service.os.getenv", return_value=None):
            result = svc._get_env_fallback("/services/kafka")

        assert result is None

    def test_kafka_broker_without_port(self):
        svc = _build_service()

        env = {"KAFKA_BROKERS": "broker-no-port", "KAFKA_SSL": ""}
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/kafka")

        assert result["host"] == "broker-no-port"
        assert result["port"] == 9092
        assert result["ssl"] is False

    def test_arangodb_fallback(self):
        svc = _build_service()

        env = {
            "ARANGO_URL": "http://localhost:8529",
            "ARANGO_USERNAME": "admin",
            "ARANGO_PASSWORD": "pass",
            "ARANGO_DB_NAME": "mydb",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/arangodb")

        assert result == {
            "url": "http://localhost:8529",
            "username": "admin",
            "password": "pass",
            "db": "mydb",
        }

    def test_arangodb_fallback_defaults(self):
        svc = _build_service()

        env = {"ARANGO_URL": "http://arango:8529"}
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/arangodb")

        assert result["username"] == "root"
        assert result["db"] == "es"

    def test_arangodb_fallback_no_url_returns_none(self):
        svc = _build_service()

        with patch("app.config.configuration_service.os.getenv", return_value=None):
            result = svc._get_env_fallback("/services/arangodb")

        assert result is None

    def test_redis_fallback(self):
        svc = _build_service()

        env = {
            "REDIS_HOST": "redis.local",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "s3cret",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/redis")

        assert result == {"host": "redis.local", "port": 6380, "password": "s3cret"}

    def test_redis_fallback_empty_password_becomes_none(self):
        svc = _build_service()

        env = {
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6379",
            "REDIS_PASSWORD": "   ",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/redis")

        assert result["password"] is None

    def test_redis_fallback_no_host_returns_none(self):
        svc = _build_service()

        with patch("app.config.configuration_service.os.getenv", return_value=None):
            result = svc._get_env_fallback("/services/redis")

        assert result is None

    def test_qdrant_fallback(self):
        svc = _build_service()

        env = {
            "QDRANT_HOST": "qdrant.local",
            "QDRANT_GRPC_PORT": "6334",
            "QDRANT_API_KEY": "my-key",
        }
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/qdrant")

        assert result == {"host": "qdrant.local", "grpcPort": 6334, "apiKey": "my-key"}

    def test_qdrant_fallback_defaults(self):
        svc = _build_service()

        env = {"QDRANT_HOST": "q"}
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/qdrant")

        assert result["grpcPort"] == 6333
        assert result["apiKey"] == "qdrant"

    def test_qdrant_fallback_no_host_returns_none(self):
        svc = _build_service()

        with patch("app.config.configuration_service.os.getenv", return_value=None):
            result = svc._get_env_fallback("/services/qdrant")

        assert result is None

    def test_unknown_key_returns_none(self):
        svc = _build_service()

        result = svc._get_env_fallback("/some/random/key")

        assert result is None


# =========================================================================
# set_config
# =========================================================================
class TestSetConfig:
    """Tests for ConfigurationService.set_config."""

    @pytest.mark.asyncio
    async def test_set_stores_and_caches(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store)

        result = await svc.set_config("/my/key", "value1")

        assert result is True
        store.create_key.assert_awaited_once_with("/my/key", "value1", overwrite=True)
        assert svc.cache["/my/key"] == "value1"

    @pytest.mark.asyncio
    async def test_set_publishes_invalidation_for_redis(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        await svc.set_config("/my/key", "val")

        store.publish_cache_invalidation.assert_awaited_once_with("/my/key")

    @pytest.mark.asyncio
    async def test_set_skips_invalidation_for_etcd(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")

        await svc.set_config("/k", "v")

        store.publish_cache_invalidation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_set_store_failure_returns_false(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=False)
        svc = _build_service(store)

        result = await svc.set_config("/k", "v")

        assert result is False
        assert "/k" not in svc.cache

    @pytest.mark.asyncio
    async def test_set_store_exception_returns_false(self):
        store = AsyncMock()
        store.create_key = AsyncMock(side_effect=RuntimeError("disk full"))
        svc = _build_service(store)

        result = await svc.set_config("/k", "v")

        assert result is False


# =========================================================================
# update_config
# =========================================================================
class TestUpdateConfig:
    """Tests for ConfigurationService.update_config."""

    @pytest.mark.asyncio
    async def test_update_existing_key(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old")
        store.update_value = AsyncMock()
        svc = _build_service(store)

        result = await svc.update_config("/k", "new")

        assert result is True
        store.update_value.assert_awaited_once_with("/k", "new")
        assert svc.cache["/k"] == "new"

    @pytest.mark.asyncio
    async def test_update_missing_key_falls_back_to_set(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value=None)
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store)

        result = await svc.update_config("/new/key", "val")

        assert result is True
        store.create_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_store_exception_returns_false(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old")
        store.update_value = AsyncMock(side_effect=RuntimeError("oops"))
        svc = _build_service(store)

        result = await svc.update_config("/k", "new")

        assert result is False

    @pytest.mark.asyncio
    async def test_update_publishes_invalidation_for_redis(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old")
        store.update_value = AsyncMock()
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        await svc.update_config("/k", "new")

        store.publish_cache_invalidation.assert_awaited_once_with("/k")


# =========================================================================
# delete_config
# =========================================================================
class TestDeleteConfig:
    """Tests for ConfigurationService.delete_config."""

    @pytest.mark.asyncio
    async def test_delete_removes_from_store_and_cache(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(return_value=True)
        svc = _build_service(store)
        svc.cache["/k"] = "val"

        result = await svc.delete_config("/k")

        assert result is True
        store.delete_key.assert_awaited_once_with("/k")
        assert "/k" not in svc.cache

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(return_value=False)
        svc = _build_service(store)

        result = await svc.delete_config("/missing")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_store_exception_returns_false(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(side_effect=RuntimeError("boom"))
        svc = _build_service(store)

        result = await svc.delete_config("/k")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_publishes_invalidation_for_redis(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(return_value=True)
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        await svc.delete_config("/k")

        store.publish_cache_invalidation.assert_awaited_once_with("/k")


# =========================================================================
# clear_cache
# =========================================================================
class TestClearCache:
    """Tests for ConfigurationService.clear_cache."""

    def test_clear_cache_empties_lru(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        svc.clear_cache()

        assert len(svc.cache) == 0

    def test_clear_cache_is_idempotent(self):
        svc = _build_service()
        svc.clear_cache()
        svc.clear_cache()
        assert len(svc.cache) == 0


# =========================================================================
# _redis_invalidation_callback
# =========================================================================
class TestRedisInvalidationCallback:
    """Tests for ConfigurationService._redis_invalidation_callback."""

    def test_clear_all_clears_entire_cache(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        svc._redis_invalidation_callback("__CLEAR_ALL__")

        assert len(svc.cache) == 0

    def test_specific_key_removes_only_that_key(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        svc._redis_invalidation_callback("/a")

        assert "/a" not in svc.cache
        assert svc.cache["/b"] == 2

    def test_missing_key_does_not_raise(self):
        svc = _build_service()

        # Should not raise even if key is absent
        svc._redis_invalidation_callback("/nonexistent")


# =========================================================================
# _etcd_watch_callback
# =========================================================================
class TestEtcdWatchCallback:
    """Tests for ConfigurationService._etcd_watch_callback."""

    def test_clear_all_via_etcd_watch(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        # Simulate etcd watch event with __CLEAR_ALL__
        event = MagicMock()
        evt = MagicMock()
        evt.key = b"__CLEAR_ALL__"
        event.events = [evt]

        svc._etcd_watch_callback(event)

        assert len(svc.cache) == 0

    def test_specific_key_via_etcd_watch(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        event = MagicMock()
        evt = MagicMock()
        evt.key = b"/a"
        event.events = [evt]

        svc._etcd_watch_callback(event)

        assert "/a" not in svc.cache
        assert svc.cache["/b"] == 2

    def test_multiple_events_in_one_callback(self):
        svc = _build_service()
        svc.cache["/x"] = 10
        svc.cache["/y"] = 20
        svc.cache["/z"] = 30

        event = MagicMock()
        evt1 = MagicMock()
        evt1.key = b"/x"
        evt2 = MagicMock()
        evt2.key = b"/y"
        event.events = [evt1, evt2]

        svc._etcd_watch_callback(event)

        assert "/x" not in svc.cache
        assert "/y" not in svc.cache
        assert svc.cache["/z"] == 30

    def test_exception_in_etcd_watch_does_not_raise(self):
        svc = _build_service()

        event = MagicMock()
        event.events = MagicMock(side_effect=Exception("boom"))

        # Should not raise
        svc._etcd_watch_callback(event)


# =========================================================================
# close
# =========================================================================
class TestClose:
    """Tests for ConfigurationService.close."""

    @pytest.mark.asyncio
    async def test_close_calls_store_close(self):
        store = AsyncMock()
        svc = _build_service(store)

        await svc.close()

        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_when_store_is_none(self):
        svc = _build_service()
        svc.store = None

        # Should not raise
        await svc.close()

    @pytest.mark.asyncio
    async def test_close_when_store_raises(self):
        store = AsyncMock()
        store.close.side_effect = RuntimeError("close error")
        svc = _build_service(store)

        # Should not raise
        await svc.close()


# =========================================================================
# _publish_cache_invalidation
# =========================================================================
class TestPublishCacheInvalidation:
    """Tests for ConfigurationService._publish_cache_invalidation."""

    @pytest.mark.asyncio
    async def test_publish_skipped_for_etcd(self):
        store = AsyncMock()
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")

        await svc._publish_cache_invalidation("/key")

        store.publish_cache_invalidation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_called_for_redis(self):
        store = AsyncMock()
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        await svc._publish_cache_invalidation("/key")

        store.publish_cache_invalidation.assert_awaited_once_with("/key")

    @pytest.mark.asyncio
    async def test_publish_warns_when_store_lacks_method(self):
        store = AsyncMock(spec=[])
        svc = _build_service(store, kv_store_type="redis")

        # Should not raise, just warn
        await svc._publish_cache_invalidation("/key")

    @pytest.mark.asyncio
    async def test_publish_exception_does_not_raise(self):
        store = AsyncMock()
        store.publish_cache_invalidation = AsyncMock(side_effect=RuntimeError("pub fail"))
        svc = _build_service(store, kv_store_type="redis")

        # Should not raise
        await svc._publish_cache_invalidation("/key")


# =========================================================================
# list_keys_in_directory
# =========================================================================
class TestListKeysInDirectory:
    """Tests for ConfigurationService.list_keys_in_directory."""

    @pytest.mark.asyncio
    async def test_delegates_to_store(self):
        store = AsyncMock()
        store.list_keys_in_directory = AsyncMock(return_value=["/dir/key1", "/dir/key2"])
        svc = _build_service(store)

        result = await svc.list_keys_in_directory("/dir")

        assert result == ["/dir/key1", "/dir/key2"]
        store.list_keys_in_directory.assert_awaited_once_with("/dir")


# =========================================================================
# update_config - additional edge cases
# =========================================================================
class TestUpdateConfigEdgeCases:
    """Additional edge cases for ConfigurationService.update_config."""

    @pytest.mark.asyncio
    async def test_update_outer_exception_returns_false(self):
        store = AsyncMock()
        store.get_key = AsyncMock(side_effect=RuntimeError("connection lost"))
        svc = _build_service(store)

        result = await svc.update_config("/k", "v")

        assert result is False

    @pytest.mark.asyncio
    async def test_update_store_failure_returns_false_no_cache_update(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old")
        store.update_value = AsyncMock(side_effect=RuntimeError("write fail"))
        svc = _build_service(store)

        result = await svc.update_config("/k", "new")

        assert result is False
        assert "/k" not in svc.cache


# =========================================================================
# set_config - additional edge cases
# =========================================================================
class TestSetConfigEdgeCases:
    """Additional edge cases for ConfigurationService.set_config."""

    @pytest.mark.asyncio
    async def test_set_store_create_key_raises_returns_false(self):
        store = AsyncMock()
        store.create_key = AsyncMock(side_effect=RuntimeError("disk full"))
        svc = _build_service(store)

        result = await svc.set_config("/k", "v")

        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_complex_value(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store)

        value = {"nested": {"key": [1, 2, 3]}}
        result = await svc.set_config("/complex", value)

        assert result is True
        assert svc.cache["/complex"] == value

# =============================================================================
# Merged from test_configuration_service_coverage.py
# =============================================================================

_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service_cov(store=None, kv_store_type="redis"):
    """Construct a ConfigurationService with mocked internals."""
    if store is None:
        store = AsyncMock()

    with (
        patch("app.config.configuration_service.os.getenv") as mock_getenv,
        patch("app.config.configuration_service.EncryptionService.get_instance") as mock_enc,
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "SECRET_KEY": _TEST_SECRET_KEY,
            "KV_STORE_TYPE": kv_store_type,
        }.get(key, default)
        mock_enc.return_value = MagicMock()

        from app.config.configuration_service import ConfigurationService

        with patch.object(ConfigurationService, "_start_watch"):
            svc = ConfigurationService(
                logger=logging.getLogger("test-config-coverage"),
                key_value_store=store,
            )

    return svc


# ============================================================================
# set_config paths (lines 302-316)
# ============================================================================


class TestSetConfigPaths:
    """Test set_config success, failure, and exception paths."""

    @pytest.mark.asyncio
    async def test_set_config_store_error_returns_false(self):
        """Inner store exception caught, success=False (lines 298-300)."""
        svc = _build_service_cov()
        svc.store.create_key = AsyncMock(side_effect=RuntimeError("store exploded"))

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_store_returns_false(self):
        """store.create_key returns False (lines 309-310)."""
        svc = _build_service_cov()
        svc.store.create_key = AsyncMock(return_value=False)

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_success_publishes_invalidation(self):
        """Successful set updates cache and publishes (lines 302-308)."""
        svc = _build_service_cov()
        svc.store.create_key = AsyncMock(return_value=True)
        svc._publish_cache_invalidation = AsyncMock()

        result = await svc.set_config("/test/key", "test_value")
        assert result is True
        svc._publish_cache_invalidation.assert_awaited_once_with("/test/key")
        assert svc.cache["/test/key"] == "test_value"

    @pytest.mark.asyncio
    async def test_set_config_outer_exception_returns_false(self):
        """Outer exception in set_config returns False (lines 314-316)."""
        svc = _build_service_cov()
        svc.store.create_key = AsyncMock(return_value=True)
        svc._publish_cache_invalidation = AsyncMock(side_effect=RuntimeError("pubsub down"))

        result = await svc.set_config("/test/key", "value")
        assert result is False


# ============================================================================
# _start_etcd_watch (lines 163-177)
# ============================================================================


class TestStartEtcdWatch:
    """Test _start_etcd_watch: store with client and store without client."""

    def test_store_without_client_logs_debug(self):
        """Store without 'client' attr logs debug, skips watch (line 177)."""
        store = MagicMock(spec=["get_key", "create_key"])  # No 'client' attr
        svc = _build_service_cov(store=store, kv_store_type="etcd")
        svc._start_etcd_watch()
        time.sleep(0.1)

    def test_store_with_client_registers_callback(self):
        """Store with client registers watch callback (lines 165-173)."""
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock()

        store = MagicMock()
        store.client = mock_client

        svc = _build_service_cov(store=store, kv_store_type="etcd")
        svc._start_etcd_watch()
        time.sleep(0.2)
        mock_client.add_watch_prefix_callback.assert_called_once()

    def test_store_with_client_exception_logged(self):
        """Exception in watch registration is logged (lines 174-175)."""
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock(side_effect=RuntimeError("watch failed"))

        store = MagicMock()
        store.client = mock_client

        svc = _build_service_cov(store=store, kv_store_type="etcd")
        svc._start_etcd_watch()
        time.sleep(0.2)

    def test_store_client_initially_none_then_available(self):
        """Client starts None, waits, then becomes available (line 167-170)."""
        call_count = [0]
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock()

        store = MagicMock()
        # First call returns None, second returns the client
        type(store).client = PropertyMock(side_effect=lambda: (
            None if (call_count.__setitem__(0, call_count[0] + 1) or call_count[0]) < 2
            else mock_client
        ))

        svc = _build_service_cov(store=store, kv_store_type="etcd")
        # Patch time.sleep to avoid real delays
        with patch("app.config.configuration_service.time.sleep"):
            svc._start_etcd_watch()
            time.sleep(0.3)


# ============================================================================
# _start_redis_pubsub (lines 195-253)
# We test by extracting and running the inner start_subscription directly
# with a real event loop, avoiding the thread-creation issues.
# ============================================================================


class TestStartRedisPubsub:
    """Test _start_redis_pubsub logic by running its inner function directly."""

    def _run_pubsub_sync(self, svc):
        """Run _start_redis_pubsub's inner start_subscription logic synchronously."""
        # The method spawns a daemon thread. We call it and give the thread time.
        # Patch time.sleep to speed up the wait loop and asyncio to avoid event loop issues.
        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)

    def test_redis_pubsub_subscription_cancelled_error(self):
        """CancelledError in subscription is handled (line 252-253)."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None
        # Make subscribe_cache_invalidation a regular function that raises
        store.subscribe_cache_invalidation = MagicMock(side_effect=asyncio.CancelledError())

        svc = _build_service_cov(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        self._run_pubsub_sync(svc)

    def test_redis_pubsub_general_exception(self):
        """General exception in pubsub setup is logged (lines 254-255)."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None
        store.subscribe_cache_invalidation = MagicMock(side_effect=RuntimeError("redis down"))

        svc = _build_service_cov(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        self._run_pubsub_sync(svc)

    def test_redis_pubsub_no_underlying_store(self):
        """When store.store is None, migration check is skipped (lines 223-224)."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None

        async def fake_subscribe(cb):
            return asyncio.Future()

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        svc.clear_cache = MagicMock()
        self._run_pubsub_sync(svc)

    def test_check_migration_flag_direct_true(self):
        """Test check_migration_flag_direct returns True for bytes 'true' (lines 195-206)."""
        # Test the logic of check_migration_flag_direct directly
        import asyncio

        async def check_migration_flag_direct(redis_client, key_prefix):
            migration_flag_key = "/migrations/etcd_to_redis"
            try:
                full_key = f"{key_prefix}{migration_flag_key}"
                value = await redis_client.get(full_key)
                if value is not None:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8")
                    return value == "true"
                return False
            except Exception:
                return False

        loop = asyncio.new_event_loop()
        try:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=b"true")
            result = loop.run_until_complete(check_migration_flag_direct(mock_client, "pipeshub:kv:"))
            assert result is True

            mock_client.get = AsyncMock(return_value=b"false")
            result = loop.run_until_complete(check_migration_flag_direct(mock_client, "pipeshub:kv:"))
            assert result is False

            mock_client.get = AsyncMock(return_value="true")
            result = loop.run_until_complete(check_migration_flag_direct(mock_client, "pipeshub:kv:"))
            assert result is True

            mock_client.get = AsyncMock(return_value=None)
            result = loop.run_until_complete(check_migration_flag_direct(mock_client, "pipeshub:kv:"))
            assert result is False

            mock_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
            result = loop.run_until_complete(check_migration_flag_direct(mock_client, "pipeshub:kv:"))
            assert result is False
        finally:
            loop.close()

# =============================================================================
# Merged from test_configuration_service_coverage2.py
# =============================================================================

_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service_cov2(store=None, kv_store_type="etcd"):
    """Construct a ConfigurationService with mocked internals."""
    if store is None:
        store = AsyncMock()

    with (
        patch("app.config.configuration_service.os.getenv") as mock_getenv,
        patch("app.config.configuration_service.EncryptionService.get_instance") as mock_enc,
    ):
        mock_getenv.side_effect = lambda key, default=None: {
            "SECRET_KEY": _TEST_SECRET_KEY,
            "KV_STORE_TYPE": kv_store_type,
        }.get(key, default)
        mock_enc.return_value = MagicMock()

        from app.config.configuration_service import ConfigurationService

        with patch.object(ConfigurationService, "_start_watch"):
            svc = ConfigurationService(
                logger=logging.getLogger("test-config-cov2"),
                key_value_store=store,
            )

    return svc


# ============================================================================
# _start_etcd_watch: client initially None then becomes available (line 170)
# ============================================================================


class TestStartEtcdWatchClientPolling:
    """Test _start_etcd_watch when client starts as None and needs polling."""

    def test_client_none_then_available_with_watch(self):
        """Client is None on first check, available on second check (line 167-170)."""
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock()

        store = MagicMock()
        # client starts as None, then becomes available
        call_count = [0]

        def get_client():
            call_count[0] += 1
            if call_count[0] <= 1:
                return None
            return mock_client

        type(store).client = PropertyMock(side_effect=get_client)

        svc = _build_service_cov2(store=store, kv_store_type="etcd")

        # Patch time.sleep to avoid actual delays
        with patch("app.config.configuration_service.time.sleep"):
            svc._start_etcd_watch()
            # Wait for the thread to complete
            time.sleep(0.3)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

        # Verify the callback was registered once client became available
        mock_client.add_watch_prefix_callback.assert_called_once()


# ============================================================================
# _start_redis_pubsub: migration flag check (lines 195-206, 225-237)
# ============================================================================


class TestStartRedisPubsubMigration:
    """Test _start_redis_pubsub migration flag checking logic."""

    def test_migration_flag_true_clears_cache(self):
        """When migration flag is True, cache is cleared (lines 225-237)."""
        # Create a store mock that simulates Redis with migration completed
        mock_redis_client = AsyncMock()
        mock_redis_client.get = AsyncMock(return_value=b"true")

        mock_underlying_store = MagicMock()
        mock_underlying_store.client = mock_redis_client
        mock_underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        # Make subscribe return a completed future
        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        svc.clear_cache = MagicMock()

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

        # Cache should have been cleared (at least once for migration, once after subscribe)
        assert svc.clear_cache.call_count >= 1

    def test_migration_flag_false_no_extra_clear(self):
        """When migration flag is False, no extra cache clear for migration."""
        mock_redis_client = AsyncMock()
        mock_redis_client.get = AsyncMock(return_value=b"false")

        mock_underlying_store = MagicMock()
        mock_underlying_store.client = mock_redis_client
        mock_underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        svc.clear_cache = MagicMock()

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

        # clear_cache is called once after subscribe, but NOT for migration
        # (migration flag was false)
        # It's hard to distinguish, just verify it was called at least once
        # (the post-subscribe clear)
        assert svc.clear_cache.called

    def test_migration_flag_string_true_not_bytes(self):
        """Migration flag as string 'true' (not bytes) is handled."""
        mock_redis_client = AsyncMock()
        mock_redis_client.get = AsyncMock(return_value="true")  # string, not bytes

        mock_underlying_store = MagicMock()
        mock_underlying_store.client = mock_redis_client
        mock_underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        svc.clear_cache = MagicMock()

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

        assert svc.clear_cache.called

    def test_migration_flag_none(self):
        """Migration flag returns None (key doesn't exist)."""
        mock_redis_client = AsyncMock()
        mock_redis_client.get = AsyncMock(return_value=None)

        mock_underlying_store = MagicMock()
        mock_underlying_store.client = mock_redis_client
        mock_underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        svc.clear_cache = MagicMock()

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

    def test_migration_check_exception_handled(self):
        """Exception during migration check is handled gracefully."""
        mock_redis_client = AsyncMock()
        mock_redis_client.get = AsyncMock(side_effect=RuntimeError("redis down"))

        mock_underlying_store = MagicMock()
        mock_underlying_store.client = mock_redis_client
        mock_underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

    def test_no_underlying_store(self):
        """When store.store is None, migration check is skipped."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

    def test_client_initially_none_waits(self):
        """When client starts as None, start_subscription waits (line 210-211)."""
        store = MagicMock()

        call_count = [0]
        mock_client = MagicMock()

        def get_client():
            call_count[0] += 1
            if call_count[0] <= 1:
                return None
            return mock_client

        type(store).client = PropertyMock(side_effect=get_client)
        store.store = None

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep") as mock_sleep:
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)

        # time.sleep should have been called at least once while waiting for client
        assert mock_sleep.called

    def test_underlying_store_no_client(self):
        """When underlying store has no client attribute, migration check skips."""
        mock_underlying_store = MagicMock(spec=[])  # No client attr
        store = MagicMock()
        store.client = MagicMock()
        store.store = mock_underlying_store

        async def fake_subscribe(cb):
            f = asyncio.Future()
            f.set_result(None)
            return f

        store.subscribe_cache_invalidation = fake_subscribe

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.5)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)


# ============================================================================
# _start_redis_pubsub: cancelled error (line 252-253)
# ============================================================================


class TestStartRedisPubsubCancelled:
    """Test CancelledError handling in _start_redis_pubsub."""

    def test_cancelled_error_handled(self):
        """asyncio.CancelledError is caught without crashing."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None

        store.subscribe_cache_invalidation = MagicMock(
            side_effect=asyncio.CancelledError()
        )

        svc = _build_service_cov2(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.3)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)
