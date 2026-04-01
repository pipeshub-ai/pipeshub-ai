"""Unit tests for app.config.providers.in_memory_store.InMemoryKeyValueStore."""

import json
import logging
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from app.config.providers.in_memory_store import InMemoryKeyValueStore, KeyData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def logger():
    return logging.getLogger("test-inmemory")


@pytest.fixture
def store(logger):
    return InMemoryKeyValueStore(logger)


# =========================================================================
# KeyData
# =========================================================================
class TestKeyData:
    """Tests for the KeyData helper class."""

    def test_no_ttl_never_expires(self):
        kd = KeyData("hello", ttl=None)
        assert kd.is_expired() is False
        assert kd.value == "hello"
        assert kd.expiry is None

    def test_ttl_not_yet_expired(self):
        kd = KeyData("val", ttl=3600)
        assert kd.is_expired() is False
        assert kd.expiry is not None

    def test_ttl_expired(self):
        kd = KeyData("val", ttl=1)
        # Force expiry by setting expiry in the past
        kd.expiry = time.time() - 10
        assert kd.is_expired() is True

    def test_value_stored_correctly(self):
        kd = KeyData({"nested": [1, 2]}, ttl=60)
        assert kd.value == {"nested": [1, 2]}

    def test_ttl_zero_treated_as_no_ttl(self):
        """ttl=0 is falsy, so expiry should be None."""
        kd = KeyData("val", ttl=0)
        assert kd.expiry is None
        assert kd.is_expired() is False

    def test_expiry_set_correctly(self):
        """Verify the expiry timestamp is approximately now + ttl."""
        before = time.time()
        kd = KeyData("v", ttl=100)
        after = time.time()
        assert kd.expiry is not None
        assert before + 100 <= kd.expiry <= after + 100


# =========================================================================
# __init__ with JSON file
# =========================================================================
class TestInitWithJson:
    """Tests for InMemoryKeyValueStore initialisation from a JSON file."""

    def test_load_from_json_file(self, logger):
        """Loading a JSON file should populate the store with KeyData entries."""
        data = {"key1": "value1", "key2": 42, "key3": [1, 2, 3]}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            json_path = f.name

        try:
            store = InMemoryKeyValueStore(logger, default_json_file_path=json_path)
            assert store.json_file_path == json_path
            assert "key1" in store.store
            assert store.store["key1"].value == "value1"
            assert store.store["key2"].value == 42
            assert store.store["key3"].value == [1, 2, 3]
            # Keys loaded from JSON should have no expiry
            for kd in store.store.values():
                assert kd.expiry is None
        finally:
            os.unlink(json_path)

    def test_load_from_json_empty_object(self, logger):
        """Loading an empty JSON object should result in an empty store."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({}, f)
            json_path = f.name

        try:
            store = InMemoryKeyValueStore(logger, default_json_file_path=json_path)
            assert len(store.store) == 0
        finally:
            os.unlink(json_path)

    def test_no_json_file_path(self, logger):
        """Without a JSON path, the store starts empty and has no json_file_path attr."""
        store = InMemoryKeyValueStore(logger)
        assert len(store.store) == 0
        assert not hasattr(store, "json_file_path")


# =========================================================================
# _load_from_json
# =========================================================================
class TestLoadFromJson:
    """Direct tests for the _load_from_json helper."""

    def test_returns_dict_of_keydata(self, logger):
        data = {"a": 1, "b": "two"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            json_path = f.name

        try:
            store = InMemoryKeyValueStore(logger)
            result = store._load_from_json(json_path)
            assert isinstance(result, dict)
            assert set(result.keys()) == {"a", "b"}
            assert result["a"].value == 1
            assert result["b"].value == "two"
            for kd in result.values():
                assert kd.expiry is None
        finally:
            os.unlink(json_path)


# =========================================================================
# create_key
# =========================================================================
class TestCreateKey:
    """Tests for InMemoryKeyValueStore.create_key."""

    @pytest.mark.asyncio
    async def test_create_new_key(self, store):
        result = await store.create_key("/a", "value_a")
        assert result is True
        assert (await store.get_key("/a")) == "value_a"

    @pytest.mark.asyncio
    async def test_create_with_overwrite_true_replaces(self, store):
        await store.create_key("/a", "v1")
        result = await store.create_key("/a", "v2", overwrite=True)
        assert result is True
        assert (await store.get_key("/a")) == "v2"

    @pytest.mark.asyncio
    async def test_create_with_overwrite_false_raises(self, store):
        await store.create_key("/a", "v1")
        with pytest.raises(KeyError, match="already exists"):
            await store.create_key("/a", "v2", overwrite=False)

    @pytest.mark.asyncio
    async def test_create_with_overwrite_false_allows_expired(self, store):
        """If existing key is expired, overwrite=False should still succeed."""
        await store.create_key("/a", "v1", ttl=1)
        # Force expiry
        store.store["/a"].expiry = time.time() - 10
        result = await store.create_key("/a", "v2", overwrite=False)
        assert result is True
        assert (await store.get_key("/a")) == "v2"

    @pytest.mark.asyncio
    async def test_create_with_ttl(self, store):
        await store.create_key("/t", "temp", ttl=3600)
        assert store.store["/t"].expiry is not None
        assert (await store.get_key("/t")) == "temp"

    @pytest.mark.asyncio
    async def test_create_notifies_watcher(self, store):
        callback = MagicMock()
        await store.watch_key("/w", callback)

        await store.create_key("/w", "watched")

        callback.assert_called_once_with("watched")

    @pytest.mark.asyncio
    async def test_create_without_ttl_has_no_expiry(self, store):
        await store.create_key("/no_ttl", "val")
        assert store.store["/no_ttl"].expiry is None


# =========================================================================
# update_value
# =========================================================================
class TestUpdateValue:
    """Tests for InMemoryKeyValueStore.update_value."""

    @pytest.mark.asyncio
    async def test_update_existing_key(self, store):
        await store.create_key("/u", "old")
        await store.update_value("/u", "new")
        assert (await store.get_key("/u")) == "new"

    @pytest.mark.asyncio
    async def test_update_missing_key_raises(self, store):
        with pytest.raises(KeyError, match="does not exist"):
            await store.update_value("/missing", "val")

    @pytest.mark.asyncio
    async def test_update_expired_key_raises(self, store):
        await store.create_key("/e", "val", ttl=1)
        store.store["/e"].expiry = time.time() - 10
        with pytest.raises(KeyError, match="does not exist"):
            await store.update_value("/e", "new")

    @pytest.mark.asyncio
    async def test_update_notifies_watcher(self, store):
        await store.create_key("/w", "v1")
        callback = MagicMock()
        await store.watch_key("/w", callback)

        await store.update_value("/w", "v2")

        callback.assert_called_once_with("v2")

    @pytest.mark.asyncio
    async def test_update_with_ttl(self, store):
        await store.create_key("/u", "v1")
        await store.update_value("/u", "v2", ttl=7200)
        assert store.store["/u"].expiry is not None
        assert (await store.get_key("/u")) == "v2"


# =========================================================================
# get_key
# =========================================================================
class TestGetKey:
    """Tests for InMemoryKeyValueStore.get_key."""

    @pytest.mark.asyncio
    async def test_get_existing(self, store):
        await store.create_key("/g", 42)
        assert (await store.get_key("/g")) == 42

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store):
        assert (await store.get_key("/nope")) is None

    @pytest.mark.asyncio
    async def test_get_expired_returns_none(self, store):
        await store.create_key("/exp", "val", ttl=1)
        store.store["/exp"].expiry = time.time() - 10
        assert (await store.get_key("/exp")) is None

    @pytest.mark.asyncio
    async def test_get_expired_key_still_in_store_returns_none(self, store):
        """Exercise the branch where key IS in store but IS expired (line 189).

        _cleanup_expired_keys runs first, so we need to ensure the key
        is expired but the cleanup doesn't remove it (by patching cleanup
        to be a no-op), so the else branch at line 188-189 is hit.
        """
        await store.create_key("/exp2", "val", ttl=1)
        # Force expiry
        store.store["/exp2"].expiry = time.time() - 10
        # Patch _cleanup_expired_keys to skip removal so the key remains
        with patch.object(store, "_cleanup_expired_keys"):
            result = await store.get_key("/exp2")
        assert result is None


# =========================================================================
# delete_key
# =========================================================================
class TestDeleteKey:
    """Tests for InMemoryKeyValueStore.delete_key."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, store):
        await store.create_key("/d", "val")
        result = await store.delete_key("/d")
        assert result is True
        assert (await store.get_key("/d")) is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, store):
        result = await store.delete_key("/no")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_notifies_watcher_with_none(self, store):
        await store.create_key("/dw", "val")
        callback = MagicMock()
        await store.watch_key("/dw", callback)

        await store.delete_key("/dw")

        callback.assert_called_once_with(None)


# =========================================================================
# get_all_keys
# =========================================================================
class TestGetAllKeys:
    """Tests for InMemoryKeyValueStore.get_all_keys."""

    @pytest.mark.asyncio
    async def test_returns_all_valid_keys(self, store):
        await store.create_key("/a", 1)
        await store.create_key("/b", 2)
        keys = await store.get_all_keys()
        assert sorted(keys) == ["/a", "/b"]

    @pytest.mark.asyncio
    async def test_filters_expired_keys(self, store):
        await store.create_key("/live", 1)
        await store.create_key("/dead", 2, ttl=1)
        store.store["/dead"].expiry = time.time() - 10

        keys = await store.get_all_keys()

        assert keys == ["/live"]

    @pytest.mark.asyncio
    async def test_empty_store(self, store):
        keys = await store.get_all_keys()
        assert keys == []


# =========================================================================
# watch_key / cancel_watch
# =========================================================================
class TestWatchAndCancel:
    """Tests for InMemoryKeyValueStore.watch_key and cancel_watch."""

    @pytest.mark.asyncio
    async def test_watch_registers_callback(self, store):
        callback = MagicMock()
        watch_id = await store.watch_key("/wk", callback)

        assert isinstance(watch_id, int)
        assert "/wk" in store.watchers
        assert len(store.watchers["/wk"]) == 1

    @pytest.mark.asyncio
    async def test_multiple_watchers(self, store):
        cb1 = MagicMock()
        cb2 = MagicMock()
        await store.watch_key("/wk", cb1)
        await store.watch_key("/wk", cb2)

        await store.create_key("/wk", "hello")

        cb1.assert_called_once_with("hello")
        cb2.assert_called_once_with("hello")

    @pytest.mark.asyncio
    async def test_cancel_removes_watcher(self, store):
        callback = MagicMock()
        watch_id = await store.watch_key("/wk", callback)

        await store.cancel_watch("/wk", watch_id)

        # Watcher list should be cleaned up
        assert "/wk" not in store.watchers

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_key_does_not_raise(self, store):
        await store.cancel_watch("/nonexistent", 12345)

    @pytest.mark.asyncio
    async def test_cancel_one_of_many(self, store):
        cb1 = MagicMock()
        cb2 = MagicMock()
        wid1 = await store.watch_key("/wk", cb1)
        wid2 = await store.watch_key("/wk", cb2)

        await store.cancel_watch("/wk", wid1)

        assert len(store.watchers["/wk"]) == 1
        # Remaining watcher should still fire
        await store.create_key("/wk", "v")
        cb1.assert_not_called()
        cb2.assert_called_once_with("v")

    @pytest.mark.asyncio
    async def test_watch_with_error_callback_param(self, store):
        """error_callback is accepted but unused in-memory; ensure it doesn't break."""
        cb = MagicMock()
        err_cb = MagicMock()
        watch_id = await store.watch_key("/ek", cb, error_callback=err_cb)
        assert isinstance(watch_id, int)


# =========================================================================
# _notify_watchers — error handling branch
# =========================================================================
class TestNotifyWatchersErrorHandling:
    """Tests for the exception-handling branch in _notify_watchers (lines 111-113)."""

    @pytest.mark.asyncio
    async def test_watcher_callback_exception_is_caught(self, store):
        """When a watcher callback raises, the error is logged but not propagated."""
        bad_callback = MagicMock(side_effect=RuntimeError("callback boom"))
        good_callback = MagicMock()

        await store.watch_key("/err", bad_callback)
        await store.watch_key("/err", good_callback)

        # Should not raise; the bad callback's error is swallowed
        await store.create_key("/err", "val")

        bad_callback.assert_called_once_with("val")
        # The good callback should still be called even though bad_callback raised
        good_callback.assert_called_once_with("val")

    @pytest.mark.asyncio
    async def test_notify_no_watchers_for_key(self, store):
        """_notify_watchers with no watchers registered should be a no-op."""
        # Just ensure no exception
        store._notify_watchers("/no_watchers", "value")


# =========================================================================
# list_keys_in_directory
# =========================================================================
class TestListKeysInDirectory:
    """Tests for InMemoryKeyValueStore.list_keys_in_directory."""

    @pytest.mark.asyncio
    async def test_prefix_matching(self, store):
        await store.create_key("/services/kafka", "k")
        await store.create_key("/services/redis", "r")
        await store.create_key("/other/key", "o")

        result = await store.list_keys_in_directory("/services/")

        assert sorted(result) == ["/services/kafka", "/services/redis"]

    @pytest.mark.asyncio
    async def test_filters_expired(self, store):
        await store.create_key("/dir/live", 1)
        await store.create_key("/dir/dead", 2, ttl=1)
        store.store["/dir/dead"].expiry = time.time() - 10

        result = await store.list_keys_in_directory("/dir/")

        assert result == ["/dir/live"]

    @pytest.mark.asyncio
    async def test_no_match(self, store):
        await store.create_key("/a/b", 1)

        result = await store.list_keys_in_directory("/x/")

        assert result == []


# =========================================================================
# close
# =========================================================================
class TestClose:
    """Tests for InMemoryKeyValueStore.close."""

    @pytest.mark.asyncio
    async def test_close_clears_store_and_watchers(self, store):
        await store.create_key("/a", 1)
        await store.watch_key("/a", MagicMock())

        await store.close()

        assert len(store.store) == 0
        assert len(store.watchers) == 0


# =========================================================================
# _cleanup_expired_keys
# =========================================================================
class TestCleanupExpiredKeys:
    """Tests for InMemoryKeyValueStore._cleanup_expired_keys."""

    @pytest.mark.asyncio
    async def test_removes_expired_entries(self, store):
        await store.create_key("/live", "alive")
        await store.create_key("/dead1", "d1", ttl=1)
        await store.create_key("/dead2", "d2", ttl=1)

        store.store["/dead1"].expiry = time.time() - 10
        store.store["/dead2"].expiry = time.time() - 5

        store._cleanup_expired_keys()

        assert "/live" in store.store
        assert "/dead1" not in store.store
        assert "/dead2" not in store.store

    @pytest.mark.asyncio
    async def test_no_expired_keys(self, store):
        await store.create_key("/a", 1)
        await store.create_key("/b", 2)

        store._cleanup_expired_keys()

        assert len(store.store) == 2

    def test_empty_store_cleanup(self, store):
        """Cleanup on empty store should be safe."""
        store._cleanup_expired_keys()
        assert len(store.store) == 0


# =========================================================================
# client property
# =========================================================================
class TestClientProperty:
    """The in-memory store has no network client."""

    def test_client_returns_none(self, store):
        assert store.client is None
