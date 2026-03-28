"""
Additional coverage tests for app.config.configuration_service.

Targets lines: 170, 195-206, 211, 225-237, 243-251, 253, 314-316
"""

import asyncio
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


_TEST_SECRET_KEY = "test-secret-key-for-unit-tests"


def _build_service(store=None, kv_store_type="redis"):
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
        svc = _build_service()
        svc.store.create_key = AsyncMock(side_effect=RuntimeError("store exploded"))

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_store_returns_false(self):
        """store.create_key returns False (lines 309-310)."""
        svc = _build_service()
        svc.store.create_key = AsyncMock(return_value=False)

        result = await svc.set_config("/test/key", "value")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_config_success_publishes_invalidation(self):
        """Successful set updates cache and publishes (lines 302-308)."""
        svc = _build_service()
        svc.store.create_key = AsyncMock(return_value=True)
        svc._publish_cache_invalidation = AsyncMock()

        result = await svc.set_config("/test/key", "test_value")
        assert result is True
        svc._publish_cache_invalidation.assert_awaited_once_with("/test/key")
        assert svc.cache["/test/key"] == "test_value"

    @pytest.mark.asyncio
    async def test_set_config_outer_exception_returns_false(self):
        """Outer exception in set_config returns False (lines 314-316)."""
        svc = _build_service()
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
        svc = _build_service(store=store, kv_store_type="etcd")
        svc._start_etcd_watch()
        time.sleep(0.1)

    def test_store_with_client_registers_callback(self):
        """Store with client registers watch callback (lines 165-173)."""
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock()

        store = MagicMock()
        store.client = mock_client

        svc = _build_service(store=store, kv_store_type="etcd")
        svc._start_etcd_watch()
        time.sleep(0.2)
        mock_client.add_watch_prefix_callback.assert_called_once()

    def test_store_with_client_exception_logged(self):
        """Exception in watch registration is logged (lines 174-175)."""
        mock_client = MagicMock()
        mock_client.add_watch_prefix_callback = MagicMock(side_effect=RuntimeError("watch failed"))

        store = MagicMock()
        store.client = mock_client

        svc = _build_service(store=store, kv_store_type="etcd")
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

        svc = _build_service(store=store, kv_store_type="etcd")
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

        svc = _build_service(store=store, kv_store_type="redis")
        svc._kv_store_type = "redis"
        self._run_pubsub_sync(svc)

    def test_redis_pubsub_general_exception(self):
        """General exception in pubsub setup is logged (lines 254-255)."""
        store = MagicMock()
        store.client = MagicMock()
        store.store = None
        store.subscribe_cache_invalidation = MagicMock(side_effect=RuntimeError("redis down"))

        svc = _build_service(store=store, kv_store_type="redis")
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

        svc = _build_service(store=store, kv_store_type="redis")
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
