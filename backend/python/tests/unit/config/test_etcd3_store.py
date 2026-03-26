"""Tests for Etcd3DistributedKeyValueStore."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _passthrough_to_thread(func, *args, **kwargs):
    """Drop-in replacement for asyncio.to_thread that just calls func synchronously."""
    return func(*args, **kwargs)


class TestEtcd3DistributedKeyValueStore:
    """Tests for the Etcd3DistributedKeyValueStore class."""

    @pytest.fixture
    def serializer(self):
        return lambda v: json.dumps(v).encode()

    @pytest.fixture
    def deserializer(self):
        return lambda b: json.loads(b.decode())

    @pytest.fixture
    def mock_connection_manager(self):
        mgr = MagicMock()
        mgr.get_client = AsyncMock()
        mgr.close = AsyncMock()
        return mgr

    @pytest.fixture
    def store(self, serializer, deserializer, mock_connection_manager):
        with patch(
            "app.config.providers.etcd.etcd3_store.Etcd3ConnectionManager",
            return_value=mock_connection_manager,
        ):
            from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore

            s = Etcd3DistributedKeyValueStore(
                serializer=serializer,
                deserializer=deserializer,
                host="localhost",
                port=2379,
                timeout=5.0,
            )
            s.connection_manager = mock_connection_manager
            return s

    @pytest.fixture
    def mock_client(self, store, mock_connection_manager):
        client = MagicMock()
        mock_connection_manager.get_client.return_value = client
        return client

    # ------------------------------------------------------------------ #
    # create_key tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_create_key_new_without_ttl(self, store, mock_client):
        """Creating a key that doesn't exist, no TTL."""
        mock_client.get = MagicMock(return_value=(None, None))
        mock_client.put = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key1", "value1")

        assert result is True

    @pytest.mark.asyncio
    async def test_create_key_new_with_ttl(self, store, mock_client):
        """Creating a key with a TTL sets up a lease."""
        mock_client.get = MagicMock(return_value=(None, None))
        mock_lease = MagicMock()
        mock_client.lease = MagicMock(return_value=mock_lease)
        mock_client.put = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key2", "value2", ttl=60)

        assert result is True

    @pytest.mark.asyncio
    async def test_create_key_exists_overwrite_true(self, store, mock_client):
        """Overwriting an existing key when overwrite=True."""
        mock_client.get = MagicMock(return_value=(b"old_value", MagicMock()))
        mock_client.put = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key3", "new_value", overwrite=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_create_key_exists_overwrite_false(self, store, mock_client):
        """Not overwriting when overwrite=False and key exists."""
        mock_client.get = MagicMock(return_value=(b"existing", MagicMock()))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key4", "new_value", overwrite=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_create_key_exception_raises_connection_error(self, store, mock_client):
        """Exceptions during create_key are wrapped in ConnectionError."""
        mock_client.get = MagicMock(side_effect=RuntimeError("boom"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError, match="Failed to create key"):
                await store.create_key("fail_key", "val")

    @pytest.mark.asyncio
    async def test_create_key_non_string_value_converted(self, store, mock_client):
        """Non-string values are converted to str before encoding."""
        mock_client.get = MagicMock(return_value=(None, None))
        mock_client.put = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("numeric_key", 12345)

        assert result is True

    # ------------------------------------------------------------------ #
    # update_value tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_update_value_existing_key(self, store, mock_client):
        """Updating a key that exists succeeds."""
        mock_client.get = AsyncMock(return_value=(b"old", MagicMock()))
        mock_client.put = AsyncMock()
        mock_client.lease = MagicMock(return_value=MagicMock())

        await store.update_value("key1", {"new": "data"})

    @pytest.mark.asyncio
    async def test_update_value_existing_key_with_ttl(self, store, mock_client):
        """Updating with TTL creates a lease."""
        mock_client.get = AsyncMock(return_value=(b"old", MagicMock()))
        mock_client.put = AsyncMock()
        mock_lease = MagicMock()
        mock_client.lease = MagicMock(return_value=mock_lease)

        await store.update_value("key1", {"new": "data"}, ttl=300)
        mock_client.lease.assert_called_once_with(300)

    @pytest.mark.asyncio
    async def test_update_value_missing_key_raises_key_error(self, store, mock_client):
        """Updating a non-existent key raises KeyError."""
        mock_client.get = AsyncMock(return_value=(None, None))

        with pytest.raises(KeyError, match="does not exist"):
            await store.update_value("missing", "data")

    @pytest.mark.asyncio
    async def test_update_value_put_failure_revokes_lease(self, store, mock_client):
        """If put fails with a lease, the lease is revoked."""
        mock_client.get = AsyncMock(return_value=(b"old", MagicMock()))
        mock_lease = MagicMock()
        mock_lease.revoke = AsyncMock()
        mock_client.lease = MagicMock(return_value=mock_lease)
        mock_client.put = AsyncMock(side_effect=RuntimeError("put failed"))

        with pytest.raises(ConnectionError, match="Failed to update key"):
            await store.update_value("key1", "data", ttl=60)

        mock_lease.revoke.assert_awaited_once()

    # ------------------------------------------------------------------ #
    # get_key tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_key_found(self, store, mock_client):
        """Getting an existing key returns deserialized value."""
        mock_client.get = MagicMock(return_value=(b'{"a": 1}', MagicMock()))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.get_key("key1")

        assert result == {"a": 1}

    @pytest.mark.asyncio
    async def test_get_key_not_found(self, store, mock_client):
        """Getting a missing key returns None."""
        mock_client.get = MagicMock(return_value=(None, None))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.get_key("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_key_empty_bytes(self, store, mock_client):
        """Getting a key with empty bytes returns None."""
        mock_client.get = MagicMock(return_value=(b"", MagicMock()))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.get_key("empty_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_key_deserialization_error_returns_none(self, store, mock_client):
        """JSON decode error during deserialization returns None."""
        mock_client.get = MagicMock(return_value=(b"not-json{{{", MagicMock()))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.get_key("bad_json")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_key_connection_error(self, store, mock_client):
        """Connection errors during get are propagated."""
        mock_client.get = MagicMock(side_effect=RuntimeError("conn failed"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError, match="Failed to get key"):
                await store.get_key("key1")

    # ------------------------------------------------------------------ #
    # delete_key tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_delete_key_success(self, store, mock_client):
        """Deleting an existing key returns True."""
        mock_client.delete = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.delete_key("key1")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_key_failure_raises(self, store, mock_client):
        """Exception during delete raises ConnectionError."""
        mock_client.delete = MagicMock(side_effect=RuntimeError("fail"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError, match="Failed to delete key"):
                await store.delete_key("key1")

    # ------------------------------------------------------------------ #
    # get_all_keys tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_all_keys_returns_decoded(self, store, mock_client):
        """get_all_keys returns decoded UTF-8 strings."""
        meta1 = MagicMock()
        meta1.key = b"/app/key1"
        meta2 = MagicMock()
        meta2.key = b"/app/key2"
        mock_client.get_all = MagicMock(return_value=[
            (b"val1", meta1),
            (b"val2", meta2),
        ])

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.get_all_keys()

        assert result == ["/app/key1", "/app/key2"]

    @pytest.mark.asyncio
    async def test_get_all_keys_exception(self, store, mock_client):
        """Exception during get_all raises ConnectionError."""
        mock_client.get_all = MagicMock(side_effect=RuntimeError("error"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError, match="Failed to get all keys"):
                await store.get_all_keys()

    # ------------------------------------------------------------------ #
    # watch_key tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_watch_key_put_event(self, store, mock_client):
        """Watch callback processes PUT events correctly."""
        mock_client.add_watch_callback = AsyncMock(return_value=42)

        callback = MagicMock()
        await store.watch_key("key1", callback)

        # Verify the internal watch callback was registered
        mock_client.add_watch_callback.assert_awaited_once()
        args = mock_client.add_watch_callback.call_args
        assert args[0][0] == "key1"

        # Simulate a PUT event
        watch_fn = args[0][1]
        event = MagicMock()
        event.type = "PUT"
        event.value = b'{"k": "v"}'
        watch_fn(event)
        callback.assert_called_once_with({"k": "v"})

    @pytest.mark.asyncio
    async def test_watch_key_delete_event(self, store, mock_client):
        """Watch callback processes DELETE events correctly."""
        mock_client.add_watch_callback = AsyncMock(return_value=43)

        callback = MagicMock()
        await store.watch_key("key1", callback)

        watch_fn = mock_client.add_watch_callback.call_args[0][1]
        event = MagicMock()
        event.type = "DELETE"
        event.value = None
        watch_fn(event)
        callback.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_watch_key_error_callback(self, store, mock_client):
        """Error in watch callback invokes error_callback."""
        mock_client.add_watch_callback = AsyncMock(return_value=44)

        callback = MagicMock(side_effect=RuntimeError("callback error"))
        error_callback = MagicMock()
        await store.watch_key("key1", callback, error_callback=error_callback)

        watch_fn = mock_client.add_watch_callback.call_args[0][1]
        event = MagicMock()
        event.type = "PUT"
        event.value = b'{"k": "v"}'
        watch_fn(event)
        error_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_watch_key_stores_watcher_id(self, store, mock_client):
        """Watch IDs are stored in _active_watchers."""
        mock_client.add_watch_callback = AsyncMock(return_value=99)

        await store.watch_key("key1", MagicMock())
        assert 99 in store._active_watchers

    @pytest.mark.asyncio
    async def test_watch_key_setup_failure(self, store, mock_client):
        """Exception during watch setup raises ConnectionError."""
        mock_client.add_watch_callback = AsyncMock(side_effect=RuntimeError("watch failed"))

        with pytest.raises(ConnectionError, match="Failed to watch key"):
            await store.watch_key("key1", MagicMock())

    # ------------------------------------------------------------------ #
    # list_keys_in_directory tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_list_keys_in_directory_with_trailing_slash(self, store, mock_client):
        """Prefix matching uses trailing slash."""
        mock_client.get_prefix = MagicMock(return_value=[
            (b"/app/dir/key1", MagicMock()),
            (b"/app/dir/key2", MagicMock()),
        ])

        result = await store.list_keys_in_directory("/app/dir/")
        assert result == ["/app/dir/key1", "/app/dir/key2"]

    @pytest.mark.asyncio
    async def test_list_keys_in_directory_without_trailing_slash(self, store, mock_client):
        """Directory without trailing slash gets one appended."""
        mock_client.get_prefix = MagicMock(return_value=[])

        await store.list_keys_in_directory("/app/dir")
        mock_client.get_prefix.assert_called_once_with("/app/dir/")

    @pytest.mark.asyncio
    async def test_list_keys_in_directory_exception(self, store, mock_client):
        """Exception during listing raises ConnectionError."""
        mock_client.get_prefix = MagicMock(side_effect=RuntimeError("fail"))

        with pytest.raises(ConnectionError, match="Failed to list keys"):
            await store.list_keys_in_directory("/app/dir/")

    # ------------------------------------------------------------------ #
    # close tests
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_close_cancels_watchers(self, store, mock_connection_manager):
        """Close cancels all active watchers."""
        mock_client = MagicMock()
        mock_client.cancel_watch = AsyncMock()
        mock_connection_manager.get_client.return_value = mock_client

        store._active_watchers = [10, 20, 30]
        await store.close()

        assert mock_client.cancel_watch.await_count == 3
        assert len(store._active_watchers) == 0
        mock_connection_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_handles_cancel_failure(self, store, mock_connection_manager):
        """Close continues even if canceling a watcher fails."""
        mock_client = MagicMock()
        mock_client.cancel_watch = AsyncMock(side_effect=RuntimeError("cancel err"))
        mock_connection_manager.get_client.return_value = mock_client

        store._active_watchers = [10, 20]
        await store.close()

        assert len(store._active_watchers) == 0
        mock_connection_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_no_watchers(self, store, mock_connection_manager):
        """Close works cleanly with no active watchers."""
        store._active_watchers = []
        await store.close()
        mock_connection_manager.close.assert_awaited_once()

    # ------------------------------------------------------------------ #
    # client property
    # ------------------------------------------------------------------ #

    def test_client_property_initial_none(self, store):
        """Client property is None before any operation."""
        assert store.client is None

    @pytest.mark.asyncio
    async def test_client_property_set_after_get(self, store, mock_client):
        """Client property is set after _get_client is called."""
        await store._get_client()
        assert store.client == mock_client
