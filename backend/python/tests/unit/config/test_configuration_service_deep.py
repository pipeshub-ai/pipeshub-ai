"""
Additional tests for app.config.configuration_service targeting uncovered lines:
- ConfigurationService.__init__ without SECRET_KEY
- _get_env_fallback: Kafka (with/without SASL), ArangoDB, Redis, Qdrant
- _start_watch for redis vs etcd
- _start_etcd_watch: store with/without client
- _redis_invalidation_callback: __CLEAR_ALL__ and normal key
- _etcd_watch_callback
- set_config: success and failure
- update_config: key exists, key not exists, store error
- delete_config: success, failure, exception
- _publish_cache_invalidation: redis mode, non-redis mode, no method
- clear_cache: success and exception
- close: success, exception, no store
- list_keys_in_directory
"""

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service(store=None, kv_store_type="etcd"):
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
                logger=logging.getLogger("test-config"),
                key_value_store=store,
            )

    return svc


# ============================================================================
# __init__ edge cases
# ============================================================================


class TestConfigServiceInit:
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


# ============================================================================
# _get_env_fallback
# ============================================================================


class TestGetEnvFallback:
    def test_kafka_fallback_with_sasl(self):
        svc = _build_service()
        with patch.dict(os.environ, {
            "KAFKA_BROKERS": "broker1:9092,broker2:9093",
            "KAFKA_SSL": "true",
            "KAFKA_USERNAME": "user",
            "KAFKA_PASSWORD": "pass",
            "KAFKA_SASL_MECHANISM": "scram-sha-256",
        }):
            result = svc._get_env_fallback("/services/kafka")
            assert result is not None
            assert result["ssl"] is True
            assert result["sasl"]["username"] == "user"
            assert result["sasl"]["mechanism"] == "scram-sha-256"

    def test_kafka_fallback_without_sasl(self):
        svc = _build_service()
        with patch.dict(os.environ, {"KAFKA_BROKERS": "localhost:9092"}, clear=False):
            # Remove KAFKA_USERNAME if set
            env = os.environ.copy()
            env.pop("KAFKA_USERNAME", None)
            with patch.dict(os.environ, env, clear=True):
                with patch.dict(os.environ, {"KAFKA_BROKERS": "localhost:9092"}):
                    result = svc._get_env_fallback("/services/kafka")
                    assert result is not None
                    assert "sasl" not in result

    def test_kafka_no_env_returns_none(self):
        svc = _build_service()
        with patch.dict(os.environ, {}, clear=True):
            result = svc._get_env_fallback("/services/kafka")
            assert result is None

    def test_arangodb_fallback(self):
        svc = _build_service()
        with patch.dict(os.environ, {
            "ARANGO_URL": "http://localhost:8529",
            "ARANGO_USERNAME": "root",
            "ARANGO_PASSWORD": "password",
            "ARANGO_DB_NAME": "mydb",
        }):
            result = svc._get_env_fallback("/services/arangodb")
            assert result is not None
            assert result["url"] == "http://localhost:8529"
            assert result["db"] == "mydb"

    def test_arangodb_no_env_returns_none(self):
        svc = _build_service()
        with patch.dict(os.environ, {}, clear=True):
            result = svc._get_env_fallback("/services/arangodb")
            assert result is None

    def test_redis_fallback(self):
        svc = _build_service()
        with patch.dict(os.environ, {
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "secret",
        }):
            result = svc._get_env_fallback("/services/redis")
            assert result is not None
            assert result["host"] == "localhost"
            assert result["port"] == 6380
            assert result["password"] == "secret"

    def test_redis_empty_password(self):
        svc = _build_service()
        with patch.dict(os.environ, {"REDIS_HOST": "localhost", "REDIS_PASSWORD": "  "}):
            result = svc._get_env_fallback("/services/redis")
            assert result["password"] is None

    def test_redis_no_env_returns_none(self):
        svc = _build_service()
        with patch.dict(os.environ, {}, clear=True):
            result = svc._get_env_fallback("/services/redis")
            assert result is None

    def test_qdrant_fallback(self):
        svc = _build_service()
        with patch.dict(os.environ, {
            "QDRANT_HOST": "localhost",
            "QDRANT_GRPC_PORT": "6334",
            "QDRANT_API_KEY": "my-key",
        }):
            result = svc._get_env_fallback("/services/qdrant")
            assert result is not None
            assert result["grpcPort"] == 6334

    def test_qdrant_no_env_returns_none(self):
        svc = _build_service()
        with patch.dict(os.environ, {}, clear=True):
            result = svc._get_env_fallback("/services/qdrant")
            assert result is None

    def test_unknown_key_returns_none(self):
        svc = _build_service()
        result = svc._get_env_fallback("/some/unknown/key")
        assert result is None


# ============================================================================
# _redis_invalidation_callback
# ============================================================================


class TestRedisInvalidationCallback:
    def test_clear_all(self):
        svc = _build_service()
        svc.cache["key1"] = "val1"
        svc._redis_invalidation_callback("__CLEAR_ALL__")
        assert len(svc.cache) == 0

    def test_specific_key(self):
        svc = _build_service()
        svc.cache["key1"] = "val1"
        svc.cache["key2"] = "val2"
        svc._redis_invalidation_callback("key1")
        assert "key1" not in svc.cache
        assert "key2" in svc.cache


# ============================================================================
# _etcd_watch_callback
# ============================================================================


class TestEtcdWatchCallback:
    def test_clear_all_event(self):
        svc = _build_service()
        svc.cache["key1"] = "val1"
        event = MagicMock()
        event.events = [MagicMock(key=b"__CLEAR_ALL__")]
        svc._etcd_watch_callback(event)
        assert len(svc.cache) == 0

    def test_specific_key_event(self):
        svc = _build_service()
        svc.cache["key1"] = "val1"
        event = MagicMock()
        event.events = [MagicMock(key=b"key1")]
        svc._etcd_watch_callback(event)
        assert "key1" not in svc.cache

    def test_exception_handled(self):
        svc = _build_service()
        event = MagicMock()
        event.events = None  # Will cause iteration error
        # Should not raise
        svc._etcd_watch_callback(event)


# ============================================================================
# set_config
# ============================================================================


class TestSetConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store, kv_store_type="etcd")
        result = await svc.set_config("/test/key", "value")
        assert result is True
        assert svc.cache["/test/key"] == "value"

    @pytest.mark.asyncio
    async def test_store_failure(self):
        store = AsyncMock()
        store.create_key = AsyncMock(return_value=False)
        svc = _build_service(store)
        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_store_exception(self):
        store = AsyncMock()
        store.create_key = AsyncMock(side_effect=Exception("store error"))
        svc = _build_service(store)
        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        store = AsyncMock()
        store.create_key = AsyncMock(side_effect=Exception("fail"))
        svc = _build_service(store)
        result = await svc.set_config("/test/key", "value")
        assert result is False


# ============================================================================
# update_config
# ============================================================================


class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_key_exists_update_success(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old_value")
        store.update_value = AsyncMock()
        svc = _build_service(store, kv_store_type="etcd")
        result = await svc.update_config("/test/key", "new_value")
        assert result is True
        assert svc.cache["/test/key"] == "new_value"

    @pytest.mark.asyncio
    async def test_key_not_exists_delegates_to_set(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value=None)
        store.create_key = AsyncMock(return_value=True)
        svc = _build_service(store, kv_store_type="etcd")
        result = await svc.update_config("/test/key", "value")
        assert result is True

    @pytest.mark.asyncio
    async def test_store_update_error(self):
        store = AsyncMock()
        store.get_key = AsyncMock(return_value="old")
        store.update_value = AsyncMock(side_effect=Exception("update failed"))
        svc = _build_service(store, kv_store_type="etcd")
        result = await svc.update_config("/test/key", "new")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        store = AsyncMock()
        store.get_key = AsyncMock(side_effect=Exception("fail"))
        svc = _build_service(store)
        result = await svc.update_config("/test/key", "value")
        assert result is False


# ============================================================================
# delete_config
# ============================================================================


class TestDeleteConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(return_value=True)
        svc = _build_service(store, kv_store_type="etcd")
        svc.cache["/test/key"] = "val"
        result = await svc.delete_config("/test/key")
        assert result is True
        assert "/test/key" not in svc.cache

    @pytest.mark.asyncio
    async def test_failure(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(return_value=False)
        svc = _build_service(store)
        result = await svc.delete_config("/test/key")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        store = AsyncMock()
        store.delete_key = AsyncMock(side_effect=Exception("fail"))
        svc = _build_service(store)
        result = await svc.delete_config("/test/key")
        assert result is False


# ============================================================================
# _publish_cache_invalidation
# ============================================================================


class TestPublishCacheInvalidation:
    @pytest.mark.asyncio
    async def test_non_redis_skips(self):
        svc = _build_service(kv_store_type="etcd")
        await svc._publish_cache_invalidation("/key")
        # No error

    @pytest.mark.asyncio
    async def test_redis_with_method(self):
        store = AsyncMock()
        store.publish_cache_invalidation = AsyncMock()
        svc = _build_service(store, kv_store_type="redis")
        await svc._publish_cache_invalidation("/key")
        store.publish_cache_invalidation.assert_awaited_once_with("/key")

    @pytest.mark.asyncio
    async def test_redis_without_method(self):
        store = AsyncMock(spec=[])  # no methods
        svc = _build_service(store, kv_store_type="redis")
        # Should log warning but not raise
        await svc._publish_cache_invalidation("/key")

    @pytest.mark.asyncio
    async def test_redis_publish_exception(self):
        store = AsyncMock()
        store.publish_cache_invalidation = AsyncMock(side_effect=Exception("publish fail"))
        svc = _build_service(store, kv_store_type="redis")
        # Should not raise
        await svc._publish_cache_invalidation("/key")


# ============================================================================
# clear_cache
# ============================================================================


class TestClearCache:
    def test_clears_cache(self):
        svc = _build_service()
        svc.cache["k1"] = "v1"
        svc.clear_cache()
        assert len(svc.cache) == 0


# ============================================================================
# close
# ============================================================================


class TestClose:
    @pytest.mark.asyncio
    async def test_success(self):
        store = AsyncMock()
        svc = _build_service(store)
        await svc.close()
        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_handled(self):
        store = AsyncMock()
        store.close = AsyncMock(side_effect=Exception("close fail"))
        svc = _build_service(store)
        # Should not raise
        await svc.close()

    @pytest.mark.asyncio
    async def test_no_store(self):
        svc = _build_service()
        svc.store = None
        # Should not raise
        await svc.close()


# ============================================================================
# list_keys_in_directory
# ============================================================================


class TestListKeysInDirectory:
    @pytest.mark.asyncio
    async def test_delegates_to_store(self):
        store = AsyncMock()
        store.list_keys_in_directory = AsyncMock(return_value=["/a", "/b"])
        svc = _build_service(store)
        result = await svc.list_keys_in_directory("/")
        assert result == ["/a", "/b"]
