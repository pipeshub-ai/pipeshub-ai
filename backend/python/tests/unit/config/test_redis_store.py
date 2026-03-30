"""Unit tests for app.config.providers.redis.redis_store — RedisDistributedKeyValueStore.

Covers: create_key, update_value, get_key, delete_key, get_all_keys,
watch_key, cancel_watch, list_keys_in_directory, health_check,
wait_for_connection, publish_cache_invalidation, subscribe_cache_invalidation,
close, _build_key, _strip_prefix, _notify_watchers.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(key_prefix="pipeshub:kv:"):
    """Build a RedisDistributedKeyValueStore with a mocked Redis client."""
    from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

    serializer = lambda v: json.dumps(v).encode("utf-8")  # noqa: E731
    deserializer = lambda b: json.loads(b.decode("utf-8"))  # noqa: E731

    with patch("app.config.providers.redis.redis_store.redis.Redis"):
        store = RedisDistributedKeyValueStore(
            serializer=serializer,
            deserializer=deserializer,
            host="localhost",
            port=6379,
            password=None,
            db=0,
            key_prefix=key_prefix,
        )

    # Replace _get_client to always return a mock
    mock_client = AsyncMock()
    store._get_client = MagicMock(return_value=mock_client)
    store._mock_client = mock_client  # expose for easy access in tests
    return store


# ============================================================================
# _build_key / _strip_prefix
# ============================================================================

class TestKeyHelpers:
    def test_build_key_adds_prefix(self):
        store = _make_store()
        assert store._build_key("foo/bar") == "pipeshub:kv:foo/bar"

    def test_strip_prefix_removes_prefix(self):
        store = _make_store()
        assert store._strip_prefix("pipeshub:kv:foo/bar") == "foo/bar"

    def test_strip_prefix_no_prefix_returns_as_is(self):
        store = _make_store()
        assert store._strip_prefix("other:foo") == "other:foo"

    def test_build_key_custom_prefix(self):
        store = _make_store(key_prefix="custom:")
        assert store._build_key("key") == "custom:key"


# ============================================================================
# health_check
# ============================================================================

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        store = _make_store()
        store._mock_client.ping = AsyncMock(return_value=True)
        assert await store.health_check() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        store = _make_store()
        store._mock_client.ping = AsyncMock(side_effect=ConnectionError("down"))
        assert await store.health_check() is False

    @pytest.mark.asyncio
    async def test_ping_returns_false(self):
        store = _make_store()
        store._mock_client.ping = AsyncMock(return_value=False)
        assert await store.health_check() is False


# ============================================================================
# create_key
# ============================================================================

class TestCreateKey:
    @pytest.mark.asyncio
    async def test_overwrite_no_ttl(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)
        result = await store.create_key("k1", {"data": 1})
        assert result is True
        store._mock_client.set.assert_called_once()
        call_args = store._mock_client.set.call_args
        assert call_args[0][0] == "pipeshub:kv:k1"

    @pytest.mark.asyncio
    async def test_overwrite_with_ttl(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)
        result = await store.create_key("k1", {"data": 1}, ttl=60)
        assert result is True
        call_kwargs = store._mock_client.set.call_args
        assert call_kwargs[1].get("ex") == 60 or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else True

    @pytest.mark.asyncio
    async def test_no_overwrite_key_exists(self):
        store = _make_store()
        # set with nx=True returns None when key exists
        store._mock_client.set = AsyncMock(return_value=None)
        result = await store.create_key("k1", {"data": 1}, overwrite=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_overwrite_key_created(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)
        result = await store.create_key("k1", {"data": 1}, overwrite=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_redis_error_raises_connection_error(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(side_effect=Exception("redis down"))
        with pytest.raises(ConnectionError, match="Failed to create key"):
            await store.create_key("k1", {"data": 1})

    @pytest.mark.asyncio
    async def test_notifies_watchers(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)

        callback = MagicMock()
        await store.watch_key("k1", callback)

        await store.create_key("k1", {"data": 42})
        callback.assert_called_once_with({"data": 42})


# ============================================================================
# update_value
# ============================================================================

class TestUpdateValue:
    @pytest.mark.asyncio
    async def test_success(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)
        await store.update_value("k1", {"updated": True})
        store._mock_client.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_key_does_not_exist_raises_key_error(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=None)
        with pytest.raises(KeyError, match="does not exist"):
            await store.update_value("missing", {"val": 1})

    @pytest.mark.asyncio
    async def test_with_ttl(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)
        await store.update_value("k1", {"val": 1}, ttl=120)
        call_kwargs = store._mock_client.set.call_args[1]
        assert call_kwargs.get("ex") == 120

    @pytest.mark.asyncio
    async def test_redis_error_raises_connection_error(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(side_effect=Exception("timeout"))
        with pytest.raises(ConnectionError, match="Failed to update key"):
            await store.update_value("k1", {"val": 1})

    @pytest.mark.asyncio
    async def test_notifies_watchers(self):
        store = _make_store()
        store._mock_client.set = AsyncMock(return_value=True)

        callback = MagicMock()
        await store.watch_key("k1", callback)

        await store.update_value("k1", {"new": True})
        callback.assert_called_once_with({"new": True})


# ============================================================================
# get_key
# ============================================================================

class TestGetKey:
    @pytest.mark.asyncio
    async def test_found(self):
        store = _make_store()
        store._mock_client.get = AsyncMock(return_value=b'{"hello": "world"}')
        result = await store.get_key("k1")
        assert result == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_not_found(self):
        store = _make_store()
        store._mock_client.get = AsyncMock(return_value=None)
        result = await store.get_key("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_deserialization_error_returns_none(self):
        store = _make_store()
        store._mock_client.get = AsyncMock(return_value=b"not json {{{")
        result = await store.get_key("bad")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_error_raises_connection_error(self):
        store = _make_store()
        store._mock_client.get = AsyncMock(side_effect=Exception("network"))
        with pytest.raises(ConnectionError, match="Failed to get key"):
            await store.get_key("k1")


# ============================================================================
# delete_key
# ============================================================================

class TestDeleteKey:
    @pytest.mark.asyncio
    async def test_success(self):
        store = _make_store()
        store._mock_client.delete = AsyncMock(return_value=1)
        result = await store.delete_key("k1")
        assert result is True

    @pytest.mark.asyncio
    async def test_key_not_found(self):
        store = _make_store()
        store._mock_client.delete = AsyncMock(return_value=0)
        result = await store.delete_key("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_redis_error_raises_connection_error(self):
        store = _make_store()
        store._mock_client.delete = AsyncMock(side_effect=Exception("oops"))
        with pytest.raises(ConnectionError, match="Failed to delete key"):
            await store.delete_key("k1")

    @pytest.mark.asyncio
    async def test_notifies_watchers_on_delete(self):
        store = _make_store()
        store._mock_client.delete = AsyncMock(return_value=1)

        callback = MagicMock()
        await store.watch_key("k1", callback)

        await store.delete_key("k1")
        callback.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_does_not_notify_watchers_when_not_found(self):
        store = _make_store()
        store._mock_client.delete = AsyncMock(return_value=0)

        callback = MagicMock()
        await store.watch_key("k1", callback)

        await store.delete_key("k1")
        callback.assert_not_called()


# ============================================================================
# get_all_keys
# ============================================================================

class TestGetAllKeys:
    @pytest.mark.asyncio
    async def test_returns_decoded_keys(self):
        store = _make_store()

        async def _scan_iter(match=None):
            for key in [b"pipeshub:kv:a", b"pipeshub:kv:b/c"]:
                yield key

        store._mock_client.scan_iter = _scan_iter
        keys = await store.get_all_keys()
        assert sorted(keys) == ["a", "b/c"]

    @pytest.mark.asyncio
    async def test_empty(self):
        store = _make_store()

        async def _scan_iter(match=None):
            return
            yield  # noqa: E501 - make it an async generator

        store._mock_client.scan_iter = _scan_iter
        keys = await store.get_all_keys()
        assert keys == []

    @pytest.mark.asyncio
    async def test_handles_string_keys(self):
        """If Redis returns string keys (decode_responses=True), handle them."""
        store = _make_store()

        async def _scan_iter(match=None):
            for key in ["pipeshub:kv:x"]:
                yield key

        store._mock_client.scan_iter = _scan_iter
        keys = await store.get_all_keys()
        assert keys == ["x"]

    @pytest.mark.asyncio
    async def test_redis_error_raises(self):
        store = _make_store()

        async def _scan_iter(match=None):
            raise Exception("scan fail")
            yield  # noqa: E501

        store._mock_client.scan_iter = _scan_iter
        with pytest.raises(ConnectionError, match="Failed to get all keys"):
            await store.get_all_keys()


# ============================================================================
# watch_key / cancel_watch / _notify_watchers
# ============================================================================

class TestWatchKey:
    @pytest.mark.asyncio
    async def test_watch_returns_id(self):
        store = _make_store()
        watch_id = await store.watch_key("k1", MagicMock())
        assert isinstance(watch_id, str)
        assert len(watch_id) > 0

    @pytest.mark.asyncio
    async def test_custom_watch_id(self):
        store = _make_store()
        watch_id = await store.watch_key("k1", MagicMock(), watch_id="custom123")
        assert watch_id == "custom123"

    @pytest.mark.asyncio
    async def test_multiple_watchers(self):
        store = _make_store()
        cb1 = MagicMock()
        cb2 = MagicMock()
        await store.watch_key("k1", cb1)
        await store.watch_key("k1", cb2)

        await store._notify_watchers("k1", "value")
        cb1.assert_called_once_with("value")
        cb2.assert_called_once_with("value")

    @pytest.mark.asyncio
    async def test_cancel_watch(self):
        store = _make_store()
        cb = MagicMock()
        watch_id = await store.watch_key("k1", cb)

        await store.cancel_watch("k1", watch_id)

        await store._notify_watchers("k1", "value")
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_watch(self):
        """Canceling a watch for a key with no watchers does not error."""
        store = _make_store()
        await store.cancel_watch("nonexistent", "fake_id")

    @pytest.mark.asyncio
    async def test_notify_watchers_callback_error_handled(self):
        """Callback errors in _notify_watchers are caught, not propagated."""
        store = _make_store()
        bad_cb = MagicMock(side_effect=RuntimeError("callback boom"))
        await store.watch_key("k1", bad_cb)

        # Should not raise
        await store._notify_watchers("k1", "value")

    @pytest.mark.asyncio
    async def test_cancel_removes_key_entry_when_empty(self):
        """After all watchers are canceled, the key is removed from _watchers."""
        store = _make_store()
        cb = MagicMock()
        watch_id = await store.watch_key("k1", cb)
        await store.cancel_watch("k1", watch_id)
        assert "k1" not in store._watchers


# ============================================================================
# list_keys_in_directory
# ============================================================================

class TestListKeysInDirectory:
    @pytest.mark.asyncio
    async def test_returns_keys(self):
        store = _make_store()

        async def _scan_iter(match=None):
            for key in [b"pipeshub:kv:dir/a", b"pipeshub:kv:dir/b"]:
                yield key

        store._mock_client.scan_iter = _scan_iter
        keys = await store.list_keys_in_directory("dir")
        assert sorted(keys) == ["dir/a", "dir/b"]

    @pytest.mark.asyncio
    async def test_appends_trailing_slash(self):
        """Directory without trailing slash gets one appended."""
        store = _make_store()

        scan_calls = []

        async def _scan_iter(match=None):
            scan_calls.append(match)
            return
            yield  # noqa: E501

        store._mock_client.scan_iter = _scan_iter
        await store.list_keys_in_directory("mydir")
        assert scan_calls[0] == "pipeshub:kv:mydir/*"

    @pytest.mark.asyncio
    async def test_directory_with_trailing_slash(self):
        """Directory already with trailing slash is not doubled."""
        store = _make_store()

        scan_calls = []

        async def _scan_iter(match=None):
            scan_calls.append(match)
            return
            yield  # noqa: E501

        store._mock_client.scan_iter = _scan_iter
        await store.list_keys_in_directory("mydir/")
        assert scan_calls[0] == "pipeshub:kv:mydir/*"

    @pytest.mark.asyncio
    async def test_redis_error_raises(self):
        store = _make_store()

        async def _scan_iter(match=None):
            raise Exception("fail")
            yield  # noqa: E501

        store._mock_client.scan_iter = _scan_iter
        with pytest.raises(ConnectionError, match="Failed to list keys"):
            await store.list_keys_in_directory("dir")


# ============================================================================
# publish_cache_invalidation
# ============================================================================

class TestPublishCacheInvalidation:
    @pytest.mark.asyncio
    async def test_success(self):
        store = _make_store()
        store._mock_client.publish = AsyncMock(return_value=1)
        await store.publish_cache_invalidation("some_key")
        store._mock_client.publish.assert_called_once_with(
            store.CACHE_INVALIDATION_CHANNEL, "some_key"
        )

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        store = _make_store()
        store._mock_client.publish = AsyncMock(
            side_effect=[Exception("fail1"), Exception("fail2"), None]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await store.publish_cache_invalidation("some_key")

        assert store._mock_client.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self):
        store = _make_store()
        store._mock_client.publish = AsyncMock(side_effect=Exception("always fail"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Should not raise, just log
            await store.publish_cache_invalidation("some_key")

        assert store._mock_client.publish.call_count == 3


# ============================================================================
# subscribe_cache_invalidation
# ============================================================================

class TestSubscribeCacheInvalidation:
    @pytest.mark.asyncio
    async def test_sets_up_task(self):
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        # Make listen() yield one message then stop
        async def _listen():
            yield {"type": "message", "data": b"invalidated_key"}
            store._is_closing = True

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        assert isinstance(task, asyncio.Task)

        # Let the task run
        await asyncio.sleep(0.05)

        callback.assert_called_once_with("invalidated_key")

        # Clean up
        store._is_closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_callback_stored(self):
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            store._is_closing = True
            return
            yield  # noqa: E501

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        assert store._pubsub_callback is callback

        store._is_closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================================
# close
# ============================================================================

class TestClose:
    @pytest.mark.asyncio
    async def test_close_cleans_resources(self):
        store = _make_store()

        # Add some watchers
        await store.watch_key("k1", MagicMock())

        # Set up client in the clients dict
        import threading
        tid = threading.get_ident()
        mock_client = AsyncMock()
        mock_client.close = AsyncMock()
        store._clients[tid] = (mock_client, None)

        await store.close()

        assert store._is_closing is True
        assert len(store._watchers) == 0
        assert len(store._clients) == 0
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_cancels_pubsub_task(self):
        store = _make_store()

        # Create a fake pubsub task
        async def _noop():
            await asyncio.sleep(100)

        store._pubsub_task = asyncio.create_task(_noop())

        await store.close()

        assert store._pubsub_task is None
        assert store._pubsub_callback is None


# ============================================================================
# wait_for_connection
# ============================================================================

class TestWaitForConnection:
    @pytest.mark.asyncio
    async def test_success_immediate(self):
        store = _make_store()
        store._mock_client.ping = AsyncMock(return_value=True)
        result = await store.wait_for_connection(timeout=5.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        store = _make_store()
        store._mock_client.ping = AsyncMock(side_effect=Exception("down"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await store.wait_for_connection(timeout=0.01)

        assert result is False

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        """Ping fails initially but succeeds on retry, covering line 217 branch."""
        store = _make_store()
        call_count = 0

        async def _ping():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("not ready")
            return True

        store._mock_client.ping = _ping

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await store.wait_for_connection(timeout=60.0)

        assert result is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_remaining_time_exhausted_mid_retry(self):
        """When remaining <= 0 during retry loop, exits with False (line 217)."""
        store = _make_store()
        store._mock_client.ping = AsyncMock(side_effect=Exception("down"))

        # Simulate time progressing rapidly by making loop.time() return
        # increasing values that exceed timeout
        loop = asyncio.get_running_loop()
        original_time = loop.time

        call_idx = [0]

        def mock_time():
            call_idx[0] += 1
            # First call: start_time = 0, second call: 0.5, third call: already past timeout
            return original_time() + (call_idx[0] * 100)

        with patch.object(loop, "time", side_effect=mock_time):
            result = await store.wait_for_connection(timeout=0.001)

        assert result is False


# ============================================================================
# _get_client — lazy client creation and stale detection
# ============================================================================

class TestGetClient:
    """Cover lines 105-159: _get_client lazy client creation and stale detection."""

    def _make_raw_store(self):
        """Create a store with redis.Redis patched throughout."""
        import threading
        from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

        serializer = lambda v: json.dumps(v).encode("utf-8")  # noqa: E731
        deserializer = lambda b: json.loads(b.decode("utf-8"))  # noqa: E731

        with patch("app.config.providers.redis.redis_store.redis.Redis"):
            store = RedisDistributedKeyValueStore(
                serializer=serializer,
                deserializer=deserializer,
                host="localhost",
                port=6379,
            )
        return store

    def test_creates_new_client_on_first_call(self):
        """First call to _get_client creates a new Redis client (lines 134-157)."""
        import threading

        store = self._make_raw_store()
        store._clients.clear()

        mock_client = MagicMock()
        with patch("app.config.providers.redis.redis_store.redis.Redis", return_value=mock_client):
            result = store._get_client()

        assert result is mock_client
        tid = threading.get_ident()
        assert tid in store._clients

    def test_returns_existing_client(self):
        """Subsequent calls return existing client (line 114-115 hit, 134 skip)."""
        store = self._make_raw_store()
        store._clients.clear()

        mock_client = MagicMock()
        with patch("app.config.providers.redis.redis_store.redis.Redis", return_value=mock_client):
            client1 = store._get_client()
            client2 = store._get_client()

        assert client1 is client2

    def test_discards_stale_client_closed_loop(self):
        """Discards client when event loop has been closed (lines 118-132)."""
        import threading

        store = self._make_raw_store()
        store._clients.clear()

        mock_client_old = MagicMock()
        closed_loop = MagicMock()
        closed_loop.is_closed.return_value = True

        tid = threading.get_ident()
        store._clients[tid] = (mock_client_old, closed_loop)

        mock_client_new = MagicMock()
        with patch("app.config.providers.redis.redis_store.redis.Redis", return_value=mock_client_new):
            result = store._get_client()

        assert result is mock_client_new

    def test_discards_stale_client_changed_loop(self):
        """Discards client when a different event loop is running (lines 119-132)."""
        import threading

        store = self._make_raw_store()
        store._clients.clear()

        mock_client_old = MagicMock()
        old_loop = MagicMock()
        old_loop.is_closed.return_value = False
        tid = threading.get_ident()
        store._clients[tid] = (mock_client_old, old_loop)

        current_loop = MagicMock()
        current_loop.is_closed.return_value = False

        mock_client_new = MagicMock()
        with patch("app.config.providers.redis.redis_store.redis.Redis", return_value=mock_client_new):
            with patch(
                "app.config.providers.redis.redis_store.asyncio.get_running_loop",
                return_value=current_loop,
            ):
                result = store._get_client()

        assert result is mock_client_new

    def test_no_running_loop(self):
        """When no event loop is running, current_loop is None (lines 108-111)."""
        store = self._make_raw_store()
        store._clients.clear()

        mock_client = MagicMock()
        with patch("app.config.providers.redis.redis_store.redis.Redis", return_value=mock_client):
            with patch(
                "app.config.providers.redis.redis_store.asyncio.get_running_loop",
                side_effect=RuntimeError("no running loop"),
            ):
                result = store._get_client()

        assert result is mock_client


class TestClientProperty:
    """Cover line 164: client property."""

    def test_client_property_returns_redis_client(self):
        """The client property delegates to _get_client."""
        store = _make_store()
        result = store.client
        assert result is store._mock_client


# ============================================================================
# close — error handling during client close
# ============================================================================

class TestCloseErrorHandling:
    """Cover lines 455, 469-470: close error handling for client close."""

    @pytest.mark.asyncio
    async def test_close_handles_client_close_error(self):
        """When client.close() raises, error is logged but not propagated."""
        import threading

        store = _make_store()

        tid = threading.get_ident()
        mock_client = AsyncMock()
        mock_client.close = AsyncMock(side_effect=RuntimeError("close failed"))
        store._clients[tid] = (mock_client, None)

        # Should not raise
        await store.close()

        assert store._is_closing is True
        assert len(store._clients) == 0


# ============================================================================
# cancel_watch — when key not in watchers
# ============================================================================

class TestCancelWatchEdgeCases:
    """Cover line 409->411: cancel_watch when key already removed."""

    @pytest.mark.asyncio
    async def test_cancel_watch_key_not_in_watchers(self):
        """Canceling a watch for a key that doesn't exist in _watchers."""
        store = _make_store()
        # Key is not in _watchers at all
        assert "nonexistent_key" not in store._watchers
        await store.cancel_watch("nonexistent_key", "some_id")
        # No error raised


# ============================================================================
# list_keys_in_directory — bytes decode
# ============================================================================

class TestListKeysInDirectoryBytes:
    """Cover lines 424->426: bytes decode in list_keys_in_directory."""

    @pytest.mark.asyncio
    async def test_handles_bytes_keys(self):
        """Bytes keys are properly decoded in list_keys_in_directory."""
        store = _make_store()

        async def _scan_iter(match=None):
            for key in [b"pipeshub:kv:dir/key1", b"pipeshub:kv:dir/key2"]:
                yield key

        store._mock_client.scan_iter = _scan_iter
        keys = await store.list_keys_in_directory("dir")
        assert sorted(keys) == ["dir/key1", "dir/key2"]

    @pytest.mark.asyncio
    async def test_handles_string_keys(self):
        """String keys (non-bytes) are handled in list_keys_in_directory."""
        store = _make_store()

        async def _scan_iter(match=None):
            for key in ["pipeshub:kv:dir/key1"]:
                yield key

        store._mock_client.scan_iter = _scan_iter
        keys = await store.list_keys_in_directory("dir")
        assert keys == ["dir/key1"]


# ============================================================================
# subscribe_cache_invalidation — reconnection, error handling, cleanup
# ============================================================================

class TestSubscribeCacheInvalidationAdvanced:
    """Cover lines 541, 542->539, 544->546, 549-575, 577->531, 581."""

    @pytest.mark.asyncio
    async def test_reconnects_on_error(self):
        """Pub/Sub listener reconnects after connection error (lines 555-575)."""
        store = _make_store()

        attempt = [0]

        def make_mock_pubsub():
            ps = AsyncMock()
            ps.subscribe = AsyncMock()
            ps.unsubscribe = AsyncMock()
            ps.close = AsyncMock()

            async def _listen():
                attempt[0] += 1
                if attempt[0] == 1:
                    raise ConnectionError("connection lost")
                yield {"type": "message", "data": b"key1"}
                store._is_closing = True

            ps.listen = _listen
            return ps

        store._mock_client.pubsub = MagicMock(side_effect=make_mock_pubsub)

        callback = MagicMock()

        with patch("app.config.providers.redis.redis_store.asyncio.sleep", new_callable=AsyncMock):
            with patch("app.config.providers.redis.redis_store.random.random", return_value=0.5):
                task = await store.subscribe_cache_invalidation(callback)
                # Wait for the background task to complete
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        callback.assert_called_with("key1")

    @pytest.mark.asyncio
    async def test_callback_error_handled(self):
        """Error in callback is caught and logged (lines 549-550)."""
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            yield {"type": "message", "data": b"bad_key"}
            store._is_closing = True

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        bad_callback = MagicMock(side_effect=RuntimeError("callback boom"))
        task = await store.subscribe_cache_invalidation(bad_callback)

        # Wait for the task
        await asyncio.sleep(0.05)

        # Should not crash; error is logged
        bad_callback.assert_called_once_with("bad_key")

        store._is_closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_string_data_not_decoded(self):
        """When message data is already a string (not bytes), it's used directly (line 544 branch)."""
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            yield {"type": "message", "data": "already_string_key"}
            store._is_closing = True

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        await asyncio.sleep(0.05)

        callback.assert_called_once_with("already_string_key")

        store._is_closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_non_message_type_ignored(self):
        """Non-message type messages are ignored (line 542 branch miss)."""
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            yield {"type": "subscribe", "data": 1}  # Not a "message" type
            store._is_closing = True

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        await asyncio.sleep(0.05)

        # callback should NOT have been called since type != "message"
        callback.assert_not_called()

        store._is_closing = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_cancelled_error_exits_cleanly(self):
        """CancelledError during listen exits cleanly (line 552-554)."""
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            raise asyncio.CancelledError()
            yield  # noqa: E501

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        await asyncio.sleep(0.05)

        # Task should have finished cleanly
        assert task.done()

    @pytest.mark.asyncio
    async def test_is_closing_during_error_exits(self):
        """When _is_closing is True during error, loop exits (line 556-557)."""
        store = _make_store()

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        async def _listen():
            store._is_closing = True
            raise ConnectionError("lost")
            yield  # noqa: E501

        mock_pubsub.listen = _listen
        store._mock_client.pubsub = MagicMock(return_value=mock_pubsub)

        callback = MagicMock()
        task = await store.subscribe_cache_invalidation(callback)

        await asyncio.sleep(0.05)

        # Task should have exited due to _is_closing
        callback.assert_not_called()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_pubsub_cleanup_on_error(self):
        """Pubsub is cleaned up (unsubscribe + close) in finally block (lines 577-582)."""
        store = _make_store()

        attempt = [0]
        first_pubsub = AsyncMock()
        first_pubsub.subscribe = AsyncMock()
        first_pubsub.unsubscribe = AsyncMock()
        first_pubsub.close = AsyncMock()

        second_pubsub = AsyncMock()
        second_pubsub.subscribe = AsyncMock()
        second_pubsub.unsubscribe = AsyncMock()
        second_pubsub.close = AsyncMock()

        def make_mock_pubsub():
            attempt[0] += 1
            if attempt[0] == 1:
                async def _listen_err():
                    raise ConnectionError("lost")
                    yield  # noqa: E501
                first_pubsub.listen = _listen_err
                return first_pubsub
            async def _listen_ok():
                store._is_closing = True
                return
                yield  # noqa: E501
            second_pubsub.listen = _listen_ok
            return second_pubsub

        store._mock_client.pubsub = MagicMock(side_effect=make_mock_pubsub)

        callback = MagicMock()

        with patch("app.config.providers.redis.redis_store.asyncio.sleep", new_callable=AsyncMock):
            with patch("app.config.providers.redis.redis_store.random.random", return_value=0.5):
                task = await store.subscribe_cache_invalidation(callback)
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        # First pubsub should have been cleaned up in the finally block
        first_pubsub.unsubscribe.assert_called()
        first_pubsub.close.assert_called()

    @pytest.mark.asyncio
    async def test_pubsub_cleanup_error_ignored(self):
        """Errors during pubsub cleanup (unsubscribe/close) are silently ignored (line 581-582)."""
        store = _make_store()

        attempt = [0]

        def make_mock_pubsub():
            ps = AsyncMock()
            ps.subscribe = AsyncMock()
            # First pubsub: cleanup will fail
            ps.unsubscribe = AsyncMock(side_effect=RuntimeError("cleanup fail"))
            ps.close = AsyncMock(side_effect=RuntimeError("close fail"))

            attempt[0] += 1
            if attempt[0] == 1:
                async def _listen_err():
                    raise ConnectionError("connection lost")
                    yield  # noqa: E501
                ps.listen = _listen_err
            else:
                async def _listen_ok():
                    store._is_closing = True
                    return
                    yield  # noqa: E501
                ps.listen = _listen_ok
            return ps

        store._mock_client.pubsub = MagicMock(side_effect=make_mock_pubsub)

        callback = MagicMock()

        with patch("app.config.providers.redis.redis_store.asyncio.sleep", new_callable=AsyncMock):
            with patch("app.config.providers.redis.redis_store.random.random", return_value=0.5):
                task = await store.subscribe_cache_invalidation(callback)
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        # Should not crash even though unsubscribe and close both raised
        assert task.done()


# ============================================================================
# publish_cache_invalidation — edge cases
# ============================================================================

class TestPublishCacheInvalidationEdgeCases:
    """Cover line 485->exit: publish success on retry, line 541 publish retry."""

    @pytest.mark.asyncio
    async def test_success_on_second_try(self):
        """Publish succeeds on second attempt after first failure."""
        store = _make_store()
        store._mock_client.publish = AsyncMock(
            side_effect=[Exception("fail1"), None]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await store.publish_cache_invalidation("retry_key")

        assert store._mock_client.publish.call_count == 2
