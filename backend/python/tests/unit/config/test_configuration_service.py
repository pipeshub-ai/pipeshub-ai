"""Unit tests for app.config.configuration_service.ConfigurationService."""

import asyncio
import contextlib
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

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

        assert result == {"host": "qdrant.local", "port": 6333, "grpcPort": 6334, "apiKey": "my-key"}

    def test_qdrant_fallback_defaults(self):
        svc = _build_service()

        env = {"QDRANT_HOST": "q"}
        with patch("app.config.configuration_service.os.getenv", side_effect=lambda k, d=None: env.get(k, d)):
            result = svc._get_env_fallback("/services/qdrant")

        assert result["grpcPort"] == 6334
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
        svc = _build_service(store, kv_store_type="redis")

        await svc.set_config("/my/key", "val")

        store.publish_change.assert_awaited_once_with("/my/key")

    @pytest.mark.asyncio
    async def test_set_publishes_invalidation_for_etcd_too(self):
        """R15: publish_change is called unconditionally; etcd's own store
        implementation is the one that no-ops, not ConfigurationService."""
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store, kv_store_type="etcd")

        await svc.set_config("/k", "v")

        store.publish_change.assert_awaited_once_with("/k")

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
        svc = _build_service(store, kv_store_type="redis")

        await svc.update_config("/k", "new")

        store.publish_change.assert_awaited_once_with("/k")


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
        svc = _build_service(store, kv_store_type="redis")

        await svc.delete_config("/k")

        store.publish_change.assert_awaited_once_with("/k")


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
# _invalidation_callback (R15: single backend-agnostic callback; etcd's own
# prefix-watch adapter and Redis's Pub/Sub subscriber both decode down to a
# plain key string before calling this, so there is nothing per-backend left
# to test here.)
# =========================================================================
class TestInvalidationCallback:
    """Tests for ConfigurationService._invalidation_callback."""

    def test_clear_all_clears_entire_cache(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        svc._invalidation_callback("__CLEAR_ALL__")

        assert len(svc.cache) == 0

    def test_specific_key_removes_only_that_key(self):
        svc = _build_service()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        svc._invalidation_callback("/a")

        assert "/a" not in svc.cache
        assert svc.cache["/b"] == 2

    def test_missing_key_does_not_raise(self):
        svc = _build_service()

        # Should not raise even if key is absent
        svc._invalidation_callback("/nonexistent")

    def test_exception_does_not_raise(self):
        svc = _build_service()
        svc.cache = MagicMock()
        svc.cache.pop.side_effect = Exception("boom")

        # Should not raise
        svc._invalidation_callback("/a")


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

    @pytest.mark.asyncio
    async def test_close_cancels_already_established_task_subscription(self):
        """Once the watch thread has registered an asyncio.Task subscription
        handle, close() must cancel it via call_soon_threadsafe under the
        pubsub-state lock (regression for the lock-based rewrite guarding the
        shutdown race in test_close_during_watch_setup_prevents_late_subscribe)."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        async def _never_completes() -> None:
            await asyncio.sleep(3600)

        fake_task = asyncio.ensure_future(_never_completes())
        fake_loop = MagicMock()
        svc._change_subscription = fake_task
        svc._pubsub_loop = fake_loop

        await svc.close()

        fake_loop.call_soon_threadsafe.assert_called_once_with(fake_task.cancel)

        fake_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_task

    @pytest.mark.asyncio
    async def test_close_cancels_non_task_subscription_via_store(self):
        """A non-Task handle (e.g. etcd's watch id) is cancelled through
        store.unsubscribe_changes() rather than the event loop."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")
        svc._change_subscription = "etcd-watch-id-42"

        await svc.close()

        store.unsubscribe_changes.assert_awaited_once_with("etcd-watch-id-42")

    @pytest.mark.asyncio
    async def test_close_during_watch_setup_prevents_late_subscribe(self):
        """Regression: if close() runs while the watch thread is still
        inside watch setup (e.g. the migration-flag check) — before
        _change_subscription has been assigned — the watch thread must
        observe _stopping once it reaches the subscribe step and bail out,
        rather than subscribing on a store that close() is about to (or
        already did) close."""
        paused = threading.Event()
        resume = threading.Event()

        async def slow_get(key):
            paused.set()
            resume.wait(timeout=5.0)
            return None

        redis_client = MagicMock()
        redis_client.get = slow_get

        underlying_store = MagicMock()
        underlying_store.client = redis_client
        underlying_store.key_prefix = "pipeshub:kv:"

        store = MagicMock()
        store.store = underlying_store
        store.subscribe_changes = AsyncMock()
        store.close = AsyncMock()

        svc = _build_service(store, kv_store_type="redis")
        svc._start_watch()

        assert paused.wait(timeout=2.0), "watch thread never reached the migration check"

        with patch.object(threading.Thread, "join"):
            await svc.close()

        resume.set()
        svc.watch_thread.join(timeout=2.0)

        store.subscribe_changes.assert_not_called()


# =========================================================================
# _publish_cache_invalidation
# =========================================================================
class TestPublishCacheInvalidation:
    """Tests for ConfigurationService._publish_cache_invalidation."""

    @pytest.mark.asyncio
    async def test_publish_called_for_etcd_too(self):
        """R15: no branching on KV store type -- etcd's own store
        implementation is the no-op, not ConfigurationService."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")

        await svc._publish_cache_invalidation("/key")

        store.publish_change.assert_awaited_once_with("/key")

    @pytest.mark.asyncio
    async def test_publish_called_for_redis(self):
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        await svc._publish_cache_invalidation("/key")

        store.publish_change.assert_awaited_once_with("/key")

    @pytest.mark.asyncio
    async def test_publish_warns_when_store_lacks_method(self):
        store = AsyncMock(spec=[])
        svc = _build_service(store, kv_store_type="redis")

        # Should not raise, just warn
        await svc._publish_cache_invalidation("/key")

    @pytest.mark.asyncio
    async def test_publish_exception_does_not_raise(self):
        store = AsyncMock()
        store.publish_change = AsyncMock(side_effect=RuntimeError("pub fail"))
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


# =============================================================================
# set_config: additional store-error/exception paths
# =============================================================================
class TestSetConfigPaths:
    """Test set_config success, failure, and exception paths."""

    @pytest.mark.asyncio
    async def test_set_config_store_error_returns_false(self):
        svc = _build_service()
        svc.store.create_key = AsyncMock(side_effect=RuntimeError("store exploded"))

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_store_returns_false(self):
        svc = _build_service()
        svc.store.create_key = AsyncMock(return_value=False)

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_success_publishes_invalidation(self):
        svc = _build_service()
        svc.store.create_key = AsyncMock(return_value=True)
        svc._publish_cache_invalidation = AsyncMock()

        result = await svc.set_config("/test/key", "test_value")
        assert result is True
        svc._publish_cache_invalidation.assert_awaited_once_with("/test/key")
        assert svc.cache["/test/key"] == "test_value"

    @pytest.mark.asyncio
    async def test_set_config_outer_exception_returns_false(self):
        svc = _build_service()
        svc.store.create_key = AsyncMock(return_value=True)
        svc._publish_cache_invalidation = AsyncMock(side_effect=RuntimeError("pubsub down"))

        result = await svc.set_config("/test/key", "value")
        assert result is False


# =============================================================================
# __init__ edge cases
# =============================================================================
class TestConfigServiceInit:
    """Tests for ConfigurationService.__init__."""

    def test_missing_secret_key_raises(self):
        with (
            patch("app.config.configuration_service.os.getenv", return_value=None),
            patch("app.config.configuration_service.EncryptionService.get_instance"),
        ):
            from app.config.configuration_service import ConfigurationService

            with pytest.raises(ValueError, match="SECRET_KEY"):
                ConfigurationService(
                    logger=logging.getLogger("test"),
                    key_value_store=AsyncMock(),
                )


# =============================================================================
# _start_watch (R15: single backend-agnostic watch thread; every backend's
# KeyValueStore.subscribe_changes() is awaited the same way, so there is no
# per-backend dispatch left to test.)
# =============================================================================
class TestStartWatch:
    """Tests for ConfigurationService._start_watch."""

    def _run_watch_sync(self, svc, timeout: float = 1.0) -> None:
        svc._start_watch()
        svc.watch_thread.join(timeout=timeout)

    def test_subscribes_and_clears_cache(self):
        """The watch thread registers the subscription and clears the cache
        once it is active, regardless of backend."""
        store = AsyncMock()
        store.store = None  # no nested Redis client -> migration check skipped
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="etcd")
        svc.clear_cache = MagicMock()

        self._run_watch_sync(svc)

        store.subscribe_changes.assert_awaited_once_with(svc._invalidation_callback)
        assert svc._change_subscription == "handle-1"
        svc.clear_cache.assert_called_once()

    def test_migration_flag_true_clears_cache_before_subscribe(self):
        """When the etcd->Redis migration flag is set, the cache is cleared
        an extra time before the subscription is even registered."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=b"true")

        underlying_store = MagicMock()
        underlying_store.client = redis_client
        underlying_store.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying_store
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")
        svc.clear_cache = MagicMock()

        self._run_watch_sync(svc)

        assert svc.clear_cache.call_count >= 2

    def test_migration_flag_false_clears_cache_once(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=b"false")

        underlying_store = MagicMock()
        underlying_store.client = redis_client
        underlying_store.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying_store
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")
        svc.clear_cache = MagicMock()

        self._run_watch_sync(svc)

        assert svc.clear_cache.call_count == 1

    def test_migration_check_exception_does_not_block_subscribe(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(side_effect=RuntimeError("redis down"))

        underlying_store = MagicMock()
        underlying_store.client = redis_client
        underlying_store.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying_store
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")

        self._run_watch_sync(svc)

        store.subscribe_changes.assert_awaited_once()

    def test_cancelled_error_during_subscribe_is_handled(self):
        store = AsyncMock()
        store.store = None
        store.subscribe_changes = AsyncMock(side_effect=asyncio.CancelledError())
        svc = _build_service(store, kv_store_type="etcd")

        # Should not raise, and the thread should exit cleanly.
        self._run_watch_sync(svc)
        assert svc.watch_thread.is_alive() is False

    def test_general_exception_during_subscribe_is_logged_not_raised(self):
        store = AsyncMock()
        store.store = None
        store.subscribe_changes = AsyncMock(side_effect=RuntimeError("boom"))
        svc = _build_service(store, kv_store_type="etcd")

        # Should not raise, and the thread should exit cleanly.
        self._run_watch_sync(svc)
        assert svc.watch_thread.is_alive() is False


class TestCloseSubscriptionHandles:
    """Coverage for the shutdown paths the R15 rewrite reshaped.

    `close()` used to know, statically, that Redis gave it an asyncio.Task
    and etcd gave it a watch id. Now it gets one opaque handle from
    `store.subscribe_changes()` and has to dispatch on its type, so both
    arms — and the lock-timeout arm above them — need pinning.
    """

    @pytest.mark.asyncio
    async def test_a_non_task_handle_is_cancelled_through_the_store(self):
        """etcd's watch id is an int, not a Task: it must go back to the
        store's own `unsubscribe_changes`, not to `loop.call_soon_threadsafe`."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")
        svc._change_subscription = 4242
        svc._pubsub_loop = MagicMock()

        await svc.close()

        store.unsubscribe_changes.assert_awaited_once_with(4242)
        svc._pubsub_loop.call_soon_threadsafe.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_unsubscribe_does_not_abort_shutdown(self):
        store = AsyncMock()
        store.unsubscribe_changes.side_effect = RuntimeError("etcd gone")
        svc = _build_service(store, kv_store_type="etcd")
        svc._change_subscription = 4242

        await svc.close()

        # The store is still closed even though cancelling the watch failed.
        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_handle_is_cleared_so_a_second_close_is_a_no_op(self):
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")
        svc._change_subscription = 7

        await svc.close()
        await svc.close()

        assert svc._change_subscription is None
        store.unsubscribe_changes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_proceeds_when_the_state_lock_is_held(self, caplog):
        """A watch thread wedged inside `subscribe_changes` holds the lock
        forever; shutdown must time out and continue rather than hang."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")
        svc._pubsub_state_lock.acquire()
        try:
            with (
                caplog.at_level(logging.WARNING),
                patch(
                    "app.config.configuration_service._PUBSUB_LOCK_TIMEOUT_SECONDS",
                    0.05,
                ),
            ):
                await svc.close()
            assert "Timed out waiting for the change-subscription state lock" in caplog.text
        finally:
            svc._pubsub_state_lock.release()

        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_dead_pubsub_loop_does_not_break_shutdown(self):
        """`call_soon_threadsafe` raises RuntimeError once the watch thread's
        loop has closed; that must not stop the store from being closed."""
        store = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")

        async def _never_completes() -> None:
            await asyncio.sleep(3600)

        task = asyncio.ensure_future(_never_completes())
        loop = MagicMock()
        loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")
        svc._change_subscription = task
        svc._pubsub_loop = loop

        await svc.close()

        store.close.assert_awaited_once()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestWatchWorkerMigrationFlag:
    """The Redis-only migration-flag probe inside the watch worker."""

    def _run_watch_sync(self, svc, timeout: float = 1.0) -> None:
        svc._start_watch()
        svc.watch_thread.join(timeout=timeout)

    def test_a_completed_migration_flag_clears_the_cache_on_startup(self):
        """Handles the race where the etcd->Redis migration finishes before
        this process subscribes, so it never sees the invalidation publish."""
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=b"true")
        underlying = MagicMock()
        underlying.client = redis_client
        underlying.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")
        svc.clear_cache = MagicMock()

        self._run_watch_sync(svc)

        redis_client.get.assert_awaited_with("pipeshub:kv:/migrations/etcd_to_redis")
        # Once for the migration flag, once after the subscription is live.
        assert svc.clear_cache.call_count == 2

    def test_an_unset_migration_flag_only_clears_after_subscribing(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=None)
        underlying = MagicMock()
        underlying.client = redis_client
        underlying.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")
        svc.clear_cache = MagicMock()

        self._run_watch_sync(svc)

        assert svc.clear_cache.call_count == 1

    def test_a_failing_migration_probe_does_not_stop_the_subscription(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(side_effect=RuntimeError("redis down"))
        underlying = MagicMock()
        underlying.client = redis_client
        underlying.key_prefix = "pipeshub:kv:"

        store = AsyncMock()
        store.store = underlying
        store.subscribe_changes = AsyncMock(return_value="handle-1")
        svc = _build_service(store, kv_store_type="redis")

        self._run_watch_sync(svc)

        store.subscribe_changes.assert_awaited_once()

    def test_etcd_skips_the_migration_probe_entirely(self):
        """The flag is written by the Node.js etcd->Redis migration; an etcd
        deployment never ran it, so the probe must not fire."""
        redis_client = AsyncMock()
        underlying = MagicMock()
        underlying.client = redis_client

        store = AsyncMock()
        store.store = underlying
        store.subscribe_changes = AsyncMock(return_value=99)
        svc = _build_service(store, kv_store_type="etcd")

        self._run_watch_sync(svc)

        redis_client.get.assert_not_awaited()
