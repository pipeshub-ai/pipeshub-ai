"""Tests for app.config.providers.redis.redis_store — RedisDistributedKeyValueStore."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


def _make_store():
    """Create a RedisDistributedKeyValueStore with mocked Redis client."""
    from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

    store = RedisDistributedKeyValueStore(
        serializer=lambda v: json.dumps(v).encode(),
        deserializer=lambda b: json.loads(b.decode()),
        host="localhost",
        port=6379,
        password="testpass",
        db=0,
        key_prefix="test:kv:",
    )
    return store


def _mock_client(store):
    """Inject a mocked Redis client for the current thread."""
    mock = MagicMock()
    # Make all async methods actually async
    mock.ping = AsyncMock(return_value=True)
    mock.set = AsyncMock(return_value=True)
    mock.get = AsyncMock(return_value=None)
    mock.delete = AsyncMock(return_value=1)
    mock.publish = AsyncMock(return_value=1)
    mock.close = AsyncMock()

    tid = threading.get_ident()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    store._clients[tid] = (mock, loop)
    return mock


class TestBuildKey:
    def test_basic(self):
        store = _make_store()
        assert store._build_key("mykey") == "test:kv:mykey"

    def test_empty_key(self):
        store = _make_store()
        assert store._build_key("") == "test:kv:"

    def test_nested_key(self):
        store = _make_store()
        assert store._build_key("a/b/c") == "test:kv:a/b/c"


class TestStripPrefix:
    def test_strips(self):
        store = _make_store()
        assert store._strip_prefix("test:kv:mykey") == "mykey"

    def test_no_prefix(self):
        store = _make_store()
        assert store._strip_prefix("other:key") == "other:key"

    def test_empty_after_strip(self):
        store = _make_store()
        assert store._strip_prefix("test:kv:") == ""


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.ping = AsyncMock(return_value=True)
        assert await store.health_check() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.ping = AsyncMock(side_effect=Exception("connection refused"))
        assert await store.health_check() is False


class TestCreateKey:
    @pytest.mark.asyncio
    async def test_create_with_overwrite(self):
        store = _make_store()
        mock = _mock_client(store)
        result = await store.create_key("key1", {"data": 1})
        assert result is True
        mock.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_ttl(self):
        store = _make_store()
        mock = _mock_client(store)
        result = await store.create_key("key1", {"data": 1}, ttl=60)
        assert result is True
        mock.set.assert_called_once_with(
            "test:kv:key1", json.dumps({"data": 1}).encode(), ex=60
        )

    @pytest.mark.asyncio
    async def test_create_no_overwrite_key_exists(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(return_value=None)  # nx=True returns None if exists
        result = await store.create_key("key1", {"data": 1}, overwrite=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_create_no_overwrite_new_key(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(return_value=True)
        result = await store.create_key("key1", {"data": 1}, overwrite=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_create_failure_raises(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(side_effect=Exception("redis error"))
        with pytest.raises(ConnectionError):
            await store.create_key("key1", {"data": 1})


class TestUpdateValue:
    @pytest.mark.asyncio
    async def test_update_existing(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(return_value=True)
        await store.update_value("key1", {"data": 2})
        mock.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(return_value=None)
        with pytest.raises(KeyError):
            await store.update_value("missing_key", {"data": 2})

    @pytest.mark.asyncio
    async def test_update_with_ttl(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(return_value=True)
        await store.update_value("key1", {"data": 2}, ttl=120)
        mock.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_failure_raises(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.set = AsyncMock(side_effect=RuntimeError("redis error"))
        with pytest.raises(ConnectionError):
            await store.update_value("key1", {"data": 2})


class TestGetKey:
    @pytest.mark.asyncio
    async def test_get_existing(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.get = AsyncMock(return_value=json.dumps({"data": 1}).encode())
        result = await store.get_key("key1")
        assert result == {"data": 1}

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.get = AsyncMock(return_value=None)
        result = await store.get_key("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_deserialization_error(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.get = AsyncMock(return_value=b"not valid json")
        result = await store.get_key("badkey")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_failure_raises(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.get = AsyncMock(side_effect=Exception("redis error"))
        with pytest.raises(ConnectionError):
            await store.get_key("key1")


class TestDeleteKey:
    @pytest.mark.asyncio
    async def test_delete_existing(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.delete = AsyncMock(return_value=1)
        result = await store.delete_key("key1")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.delete = AsyncMock(return_value=0)
        result = await store.delete_key("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_failure_raises(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.delete = AsyncMock(side_effect=Exception("redis error"))
        with pytest.raises(ConnectionError):
            await store.delete_key("key1")


class TestGetAllKeys:
    @pytest.mark.asyncio
    async def test_returns_keys(self):
        store = _make_store()
        mock = _mock_client(store)

        async def fake_scan(*args, **kwargs):
            for k in [b"test:kv:key1", b"test:kv:key2"]:
                yield k

        mock.scan_iter = fake_scan
        keys = await store.get_all_keys()
        assert sorted(keys) == ["key1", "key2"]

    @pytest.mark.asyncio
    async def test_empty_keys(self):
        store = _make_store()
        mock = _mock_client(store)

        async def empty_scan(*args, **kwargs):
            return
            yield  # make it a generator

        mock.scan_iter = empty_scan
        keys = await store.get_all_keys()
        assert keys == []


class TestWatchKey:
    @pytest.mark.asyncio
    async def test_watch_registers_callback(self):
        store = _make_store()
        _mock_client(store)
        callback = MagicMock()
        watch_id = await store.watch_key("mykey", callback)
        assert watch_id is not None
        assert "mykey" in store._watchers
        store._watchers.clear()

    @pytest.mark.asyncio
    async def test_watch_with_custom_id(self):
        store = _make_store()
        _mock_client(store)
        callback = MagicMock()
        watch_id = await store.watch_key("mykey", callback, watch_id="custom123")
        assert watch_id == "custom123"
        store._watchers.clear()


class TestCancelWatch:
    @pytest.mark.asyncio
    async def test_cancel_existing_watch(self):
        store = _make_store()
        _mock_client(store)
        callback = MagicMock()
        watch_id = await store.watch_key("mykey", callback)
        await store.cancel_watch("mykey", watch_id)
        assert "mykey" not in store._watchers

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_watch(self):
        store = _make_store()
        _mock_client(store)
        # Should not raise
        await store.cancel_watch("nonexistent", "fake_id")


class TestNotifyWatchers:
    @pytest.mark.asyncio
    async def test_notifies_callback(self):
        store = _make_store()
        _mock_client(store)
        callback = MagicMock()
        await store.watch_key("mykey", callback)
        await store._notify_watchers("mykey", {"updated": True})
        callback.assert_called_once_with({"updated": True})
        store._watchers.clear()

    @pytest.mark.asyncio
    async def test_notify_no_watchers(self):
        store = _make_store()
        _mock_client(store)
        # Should not raise
        await store._notify_watchers("nokey", "value")

    @pytest.mark.asyncio
    async def test_callback_error_handled(self):
        store = _make_store()
        _mock_client(store)
        callback = MagicMock(side_effect=ValueError("callback error"))
        await store.watch_key("mykey", callback)
        # Should not raise
        await store._notify_watchers("mykey", "value")
        store._watchers.clear()


class TestListKeysInDirectory:
    @pytest.mark.asyncio
    async def test_list_keys(self):
        store = _make_store()
        mock = _mock_client(store)

        async def fake_scan(*args, **kwargs):
            for k in [b"test:kv:dir/key1", b"test:kv:dir/key2"]:
                yield k

        mock.scan_iter = fake_scan
        keys = await store.list_keys_in_directory("dir")
        assert sorted(keys) == ["dir/key1", "dir/key2"]

    @pytest.mark.asyncio
    async def test_list_with_trailing_slash(self):
        store = _make_store()
        mock = _mock_client(store)

        async def fake_scan(*args, **kwargs):
            return
            yield

        mock.scan_iter = fake_scan
        keys = await store.list_keys_in_directory("dir/")
        assert keys == []


class TestPublishCacheInvalidation:
    @pytest.mark.asyncio
    async def test_publish_success(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.publish = AsyncMock(return_value=1)
        await store.publish_cache_invalidation("mykey")
        mock.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_retries_on_failure(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.publish = AsyncMock(side_effect=Exception("pub fail"))
        # Should not raise, logs error after retries
        await store.publish_cache_invalidation("mykey")
        assert mock.publish.call_count == 3  # max_retries


class TestClose:
    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.close = AsyncMock()
        await store.close()
        assert store._is_closing is True
        assert len(store._clients) == 0
        mock.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_with_watchers(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.close = AsyncMock()
        store._watchers["key1"] = [(MagicMock(), "w1")]
        await store.close()
        assert len(store._watchers) == 0

    @pytest.mark.asyncio
    async def test_close_handles_client_error(self):
        store = _make_store()
        mock = _mock_client(store)
        mock.close = AsyncMock(side_effect=Exception("close error"))
        # Should not raise
        await store.close()


class TestClientProperty:
    def test_client_property(self):
        store = _make_store()
        with patch.object(store, "_get_client", return_value=MagicMock()) as mock_get:
            client = store.client
            mock_get.assert_called_once()
            assert client is not None
