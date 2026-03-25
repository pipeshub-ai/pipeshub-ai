"""
Extended tests for app.config.configuration_service covering:
- _start_watch dispatching (redis vs etcd)
- _start_etcd_watch (store with client, store without client)
- _start_redis_pubsub and its inner thread logic
- _redis_invalidation_callback exception handling
- clear_cache exception handling
- set_config with store exception on create_key
"""

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service_raw(store=None, kv_store_type="etcd", patch_start_watch=True):
    """Build service with option to NOT patch _start_watch."""
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

        if patch_start_watch:
            with patch.object(ConfigurationService, "_start_watch"):
                svc = ConfigurationService(
                    logger=logging.getLogger("test-config-ext"),
                    key_value_store=store,
                )
        else:
            # Patch background thread methods to be non-blocking
            with (
                patch.object(ConfigurationService, "_start_etcd_watch"),
                patch.object(ConfigurationService, "_start_redis_pubsub"),
            ):
                svc = ConfigurationService(
                    logger=logging.getLogger("test-config-ext"),
                    key_value_store=store,
                )

    return svc


# ============================================================================
# _start_watch dispatching
# ============================================================================


class TestStartWatch:
    def test_redis_dispatch(self):
        """_start_watch should call _start_redis_pubsub when kv_store_type is redis."""
        store = AsyncMock()
        with (
            patch("app.config.configuration_service.os.getenv") as mock_getenv,
            patch("app.config.configuration_service.EncryptionService.get_instance") as mock_enc,
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "redis",
            }.get(key, default)
            mock_enc.return_value = MagicMock()

            from app.config.configuration_service import ConfigurationService

            with (
                patch.object(ConfigurationService, "_start_redis_pubsub") as mock_redis,
                patch.object(ConfigurationService, "_start_etcd_watch") as mock_etcd,
            ):
                svc = ConfigurationService(
                    logger=logging.getLogger("test"),
                    key_value_store=store,
                )
                mock_redis.assert_called_once()
                mock_etcd.assert_not_called()

    def test_etcd_dispatch(self):
        """_start_watch should call _start_etcd_watch when kv_store_type is etcd."""
        store = AsyncMock()
        with (
            patch("app.config.configuration_service.os.getenv") as mock_getenv,
            patch("app.config.configuration_service.EncryptionService.get_instance") as mock_enc,
        ):
            mock_getenv.side_effect = lambda key, default=None: {
                "SECRET_KEY": _TEST_SECRET_KEY,
                "KV_STORE_TYPE": "etcd",
            }.get(key, default)
            mock_enc.return_value = MagicMock()

            from app.config.configuration_service import ConfigurationService

            with (
                patch.object(ConfigurationService, "_start_redis_pubsub") as mock_redis,
                patch.object(ConfigurationService, "_start_etcd_watch") as mock_etcd,
            ):
                svc = ConfigurationService(
                    logger=logging.getLogger("test"),
                    key_value_store=store,
                )
                mock_etcd.assert_called_once()
                mock_redis.assert_not_called()


# ============================================================================
# _start_etcd_watch
# ============================================================================


class TestStartEtcdWatch:
    def test_store_with_client(self):
        """When store has a client, etcd watch should register callback."""
        store = AsyncMock()
        mock_client = MagicMock()
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        # Now call _start_etcd_watch directly
        svc._start_etcd_watch()

        # Wait for thread to start
        time.sleep(0.1)
        assert hasattr(svc, "watch_thread")
        svc.watch_thread.join(timeout=2)
        mock_client.add_watch_prefix_callback.assert_called_once()

    def test_store_without_client(self):
        """When store has no 'client' attr, skip watch."""
        store = MagicMock(spec=[])  # no client attribute

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._start_etcd_watch()

        time.sleep(0.1)
        assert hasattr(svc, "watch_thread")
        svc.watch_thread.join(timeout=2)
        # No error expected

    def test_client_watch_exception(self):
        """When add_watch_prefix_callback raises, it should be caught."""
        store = AsyncMock()
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback.side_effect = Exception("watch error")
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._start_etcd_watch()

        time.sleep(0.1)
        svc.watch_thread.join(timeout=2)
        # No exception should propagate

    def test_client_initially_none(self):
        """When store.client is initially None, the watch thread should poll."""
        store = AsyncMock()
        # Make client initially None, then set it
        call_count = 0
        original_client = MagicMock()

        def get_client():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return None
            return original_client

        type(store).client = PropertyMock(side_effect=get_client)

        svc = _build_service_raw(store, kv_store_type="etcd")

        # Patch time.sleep to speed up test
        with patch("app.config.configuration_service.time.sleep"):
            svc._start_etcd_watch()
            svc.watch_thread.join(timeout=3)


# ============================================================================
# _start_redis_pubsub
# ============================================================================


class TestStartRedisPubsub:
    def test_pubsub_exception_handled(self):
        """General exceptions in pubsub should be caught and not crash."""
        store = AsyncMock()
        store.client = MagicMock()
        store.store = None

        store.subscribe_cache_invalidation = AsyncMock(
            side_effect=Exception("pubsub error")
        )

        svc = _build_service_raw(store, kv_store_type="redis")

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.3)
        # Thread should exist and have completed without crashing
        assert hasattr(svc, "watch_thread")


# ============================================================================
# _redis_invalidation_callback exception
# ============================================================================


class TestRedisInvalidationCallbackException:
    def test_exception_in_callback(self):
        """Exception during callback should be caught."""
        svc = _build_service_raw()

        # Make cache.pop raise
        svc.cache = MagicMock()
        svc.cache.pop.side_effect = Exception("pop error")

        # Should not raise
        svc._redis_invalidation_callback("some_key")


# ============================================================================
# clear_cache exception
# ============================================================================


class TestClearCacheException:
    def test_clear_cache_error_handled(self):
        """Exception in clear_cache should be caught."""
        svc = _build_service_raw()
        svc.cache = MagicMock()
        svc.cache.clear.side_effect = Exception("clear error")
        # Should not raise
        svc.clear_cache()


# ============================================================================
# set_config store exception on create_key
# ============================================================================


class TestSetConfigStoreException:
    @pytest.mark.asyncio
    async def test_store_create_key_exception_sets_success_false(self):
        """When store.create_key raises, success should be False."""
        store = AsyncMock()
        store.create_key = AsyncMock(side_effect=Exception("create error"))
        svc = _build_service_raw(store, kv_store_type="etcd")
        result = await svc.set_config("/test/key", "value")
        assert result is False
