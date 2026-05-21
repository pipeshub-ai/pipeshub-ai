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


# ============================================================================
# _etcd_watch_callback guard against non-WatchResponse objects
# ============================================================================


class TestEtcdWatchCallbackGuard:
    """The etcd3 library may invoke the watch callback with a gRPC error
    (e.g. _MultiThreadedRendezvous) instead of a WatchResponse when the
    connection breaks.  The callback must handle this gracefully."""

    def test_grpc_error_object_is_skipped(self):
        """Callback should not raise when it receives an object without .events."""
        svc = _build_service_raw()
        grpc_error = MagicMock(spec=[])  # no .events attribute
        svc._etcd_watch_callback(grpc_error)

    def test_normal_watch_response_still_works(self):
        """Callback should still process a proper WatchResponse."""
        svc = _build_service_raw()
        svc.cache["/foo"] = "bar"

        evt = MagicMock()
        evt.key = b"/foo"
        watch_response = MagicMock()
        watch_response.events = [evt]

        svc._etcd_watch_callback(watch_response)
        assert "/foo" not in svc.cache

    def test_clear_all_event(self):
        """__CLEAR_ALL__ event should clear the entire cache."""
        svc = _build_service_raw()
        svc.cache["/a"] = 1
        svc.cache["/b"] = 2

        evt = MagicMock()
        evt.key = b"__CLEAR_ALL__"
        watch_response = MagicMock()
        watch_response.events = [evt]

        svc._etcd_watch_callback(watch_response)
        assert len(svc.cache) == 0


# ============================================================================
# _start_etcd_watch stores watch_id
# ============================================================================


class TestEtcdWatchIdTracking:
    def test_watch_id_stored(self):
        """_start_etcd_watch should store the watch_id returned by the client."""
        store = AsyncMock()
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback.return_value = 42
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._start_etcd_watch()
        svc.watch_thread.join(timeout=2)

        assert svc._etcd_watch_id == 42

    def test_watch_id_none_when_no_client(self):
        """_etcd_watch_id should remain None when store has no client."""
        store = MagicMock(spec=[])  # no client attribute

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._start_etcd_watch()
        svc.watch_thread.join(timeout=2)

        assert svc._etcd_watch_id is None

    def test_watch_id_none_on_registration_failure(self):
        """_etcd_watch_id should remain None when registration raises."""
        store = AsyncMock()
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback.side_effect = Exception("boom")
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._start_etcd_watch()
        svc.watch_thread.join(timeout=2)

        assert svc._etcd_watch_id is None


# ============================================================================
# close() cancels the etcd prefix watch
# ============================================================================


class TestCloseEtcdWatch:
    @pytest.mark.asyncio
    async def test_close_cancels_etcd_watch(self):
        """close() should call cancel_watch with the stored watch_id."""
        store = AsyncMock()
        mock_client = MagicMock()
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._etcd_watch_id = 42

        await svc.close()

        mock_client.cancel_watch.assert_called_once_with(42)
        assert svc._etcd_watch_id is None

    @pytest.mark.asyncio
    async def test_close_skips_when_no_watch_id(self):
        """close() should not attempt cancel_watch when no watch was registered."""
        store = AsyncMock()
        mock_client = MagicMock()
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        assert svc._etcd_watch_id is None

        await svc.close()

        mock_client.cancel_watch.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_handles_cancel_watch_error(self):
        """close() should not raise if cancel_watch fails."""
        store = AsyncMock()
        mock_client = MagicMock()
        mock_client.cancel_watch.side_effect = Exception("cancel error")
        store.client = mock_client

        svc = _build_service_raw(store, kv_store_type="etcd")
        svc._etcd_watch_id = 42

        await svc.close()
        store.close.assert_called_once()
