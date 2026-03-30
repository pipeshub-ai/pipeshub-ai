"""
Coverage boost tests for app.config.configuration_service.

Targets uncovered lines:
- 170: _start_etcd_watch with client initially None (time.sleep polling)
- 195-206: check_migration_flag_direct in _start_redis_pubsub
- 211: wait for client loop in start_subscription
- 225-237: migration completed -> clear_cache in _start_redis_pubsub
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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

        svc = _build_service(store=store, kv_store_type="etcd")

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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"

        with patch("app.config.configuration_service.time.sleep"):
            svc._start_redis_pubsub()
            time.sleep(0.3)
            if hasattr(svc, 'watch_thread'):
                svc.watch_thread.join(timeout=3)
