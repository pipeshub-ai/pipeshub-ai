"""Tests for Etcd3DistributedKeyValueStore."""

import asyncio
import json
import threading
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest
from etcd3.etcdrpc import kv_pb2, rpc_pb2
from etcd3.events import Event, new_event
from etcd3.exceptions import RevisionCompactedError
from etcd3.watch import WatchResponse

# The sentinel KeyValueStore.subscribe_changes documents for "drop every cached value".
CLEAR_ALL = "__CLEAR_ALL__"


def _put_event(key: str, value: bytes) -> Event:
    return new_event(kv_pb2.Event(type=kv_pb2.Event.PUT, kv=kv_pb2.KeyValue(key=key.encode(), value=value)))


def _delete_event(key: str) -> Event:
    return new_event(kv_pb2.Event(type=kv_pb2.Event.DELETE, kv=kv_pb2.KeyValue(key=key.encode())))


def _watch_response(*events) -> WatchResponse:
    """What etcd3 0.12 hands a watch callback, built from the library's own classes."""
    return WatchResponse(rpc_pb2.ResponseHeader(), list(events))


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
        mock_client.put_if_not_exists = MagicMock(return_value=False)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key4", "new_value", overwrite=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_create_key_overwrite_false_claims_in_one_transaction(self, store, mock_client) -> None:
        """A read followed by a put lets concurrent claimants all win; the
        claim must be etcd's compare-and-put alone."""
        mock_client.put_if_not_exists = MagicMock(return_value=True)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key5", "mine", overwrite=False)

        assert result is True
        mock_client.put_if_not_exists.assert_called_once_with("key5", b"mine", None)
        mock_client.get.assert_not_called()
        mock_client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_key_lost_claim_revokes_its_lease(self, store, mock_client) -> None:
        mock_lease = MagicMock()
        mock_client.lease = MagicMock(return_value=mock_lease)
        mock_client.put_if_not_exists = MagicMock(return_value=False)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            result = await store.create_key("key6", "late", overwrite=False, ttl=30)

        assert result is False
        mock_client.put_if_not_exists.assert_called_once_with("key6", b"late", mock_lease)
        mock_client.revoke_lease.assert_called_once_with(mock_lease.id)

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
        mock_client.get = MagicMock(return_value=(b"old", MagicMock()))
        mock_client.put = MagicMock()
        mock_client.lease = MagicMock(return_value=MagicMock())

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            await store.update_value("key1", {"new": "data"})

    @pytest.mark.asyncio
    async def test_update_value_existing_key_with_ttl(self, store, mock_client):
        """Updating with TTL creates a lease."""
        mock_client.get = MagicMock(return_value=(b"old", MagicMock()))
        mock_client.put = MagicMock()
        mock_lease = MagicMock()
        mock_client.lease = MagicMock(return_value=mock_lease)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            await store.update_value("key1", {"new": "data"}, ttl=300)
        mock_client.lease.assert_called_once_with(300)

    @pytest.mark.asyncio
    async def test_update_value_missing_key_raises_key_error(self, store, mock_client):
        """Updating a non-existent key raises KeyError."""
        mock_client.get = MagicMock(return_value=(None, None))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(KeyError, match="does not exist"):
                await store.update_value("missing", "data")

    @pytest.mark.asyncio
    async def test_update_value_put_failure_revokes_lease(self, store, mock_client):
        """If put fails with a lease, the lease is revoked."""
        mock_client.get = MagicMock(return_value=(b"old", MagicMock()))
        mock_lease = MagicMock()
        mock_lease.revoke = MagicMock()
        mock_client.lease = MagicMock(return_value=mock_lease)
        mock_client.put = MagicMock(side_effect=RuntimeError("put failed"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError, match="Failed to update key"):
                await store.update_value("key1", "data", ttl=60)

        mock_client.revoke_lease.assert_called_once_with(mock_lease.id)

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
    async def test_get_key_deserialization_error_raises_when_asked(self, store, mock_client):
        """A stored value that cannot be read is not an absent key; a strict
        reader must not get the None that reads as "nothing stored"."""
        mock_client.get = MagicMock(return_value=(b"not-json{{{", MagicMock()))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            with pytest.raises(ConnectionError):
                await store.get_key("bad_json", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_get_key_absent_is_none_even_when_asked(self, store, mock_client):
        mock_client.get = MagicMock(return_value=(None, None))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            assert await store.get_key("missing", raise_on_error=True) is None

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

    async def _watch(self, store, mock_client, callback, error_callback=None) -> Callable:
        mock_client.add_watch_callback = MagicMock(return_value=42)
        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            await store.watch_key("key1", callback, error_callback=error_callback)
        args = mock_client.add_watch_callback.call_args[0]
        assert args[0] == "key1"
        return args[1]

    @pytest.mark.asyncio
    async def test_watch_key_put_event(self, store, mock_client) -> None:
        """A put reaches the callback as the deserialized value."""
        callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback)

        watch_fn(_watch_response(_put_event("key1", b'{"k": "v"}')))

        callback.assert_called_once_with({"k": "v"})

    @pytest.mark.asyncio
    async def test_watch_key_delete_event(self, store, mock_client) -> None:
        """A delete reaches the callback as None."""
        callback = MagicMock()
        error_callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback, error_callback)

        watch_fn(_watch_response(_delete_event("key1")))

        callback.assert_called_once_with(None)
        error_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_watch_key_every_event_in_a_response_is_delivered(self, store, mock_client) -> None:
        """etcd batches events; each one is delivered, in order."""
        callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback)

        watch_fn(_watch_response(
            _put_event("key1", b'"a"'), _delete_event("key1"), _put_event("key1", b'"b"'),
        ))

        assert [c.args[0] for c in callback.call_args_list] == ["a", None, "b"]

    @pytest.mark.asyncio
    async def test_watch_key_error_callback(self, store, mock_client) -> None:
        """A failing callback is reported and does not stop the next event."""
        callback = MagicMock(side_effect=[RuntimeError("callback error"), None])
        error_callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback, error_callback)

        watch_fn(_watch_response(_put_event("key1", b'"a"'), _put_event("key1", b'"b"')))

        error_callback.assert_called_once()
        assert isinstance(error_callback.call_args.args[0], RuntimeError)
        assert callback.call_count == 2

    @pytest.mark.asyncio
    async def test_watch_key_a_failed_watch_is_reported(self, store, mock_client) -> None:
        """etcd3 hands the callback the exception itself when the watch fails."""
        callback = MagicMock()
        error_callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback, error_callback)
        failure = RevisionCompactedError(7)

        watch_fn(failure)

        callback.assert_not_called()
        error_callback.assert_called_once_with(failure)

    @pytest.mark.asyncio
    async def test_watch_key_an_ended_watch_thread_is_reported(self, store, mock_client) -> None:
        """etcd3's Watcher._run calls every still-registered callback with None when
        its stream ends without an RpcError; that watch will never fire again."""
        callback = MagicMock()
        error_callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback, error_callback)

        watch_fn(None)

        callback.assert_not_called()
        error_callback.assert_called_once()
        assert isinstance(error_callback.call_args.args[0], ConnectionError)

    @pytest.mark.asyncio
    async def test_watch_key_a_progress_notify_is_quiet(self, store, mock_client) -> None:
        """An event-free WatchResponse is a progress notify, not a change."""
        callback = MagicMock()
        error_callback = MagicMock()
        watch_fn = await self._watch(store, mock_client, callback, error_callback)

        watch_fn(_watch_response())

        callback.assert_not_called()
        error_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_watch_key_stores_watcher_id(self, store, mock_client) -> None:
        """The returned handle maps to the id etcd gave the watch."""
        mock_client.add_watch_callback = MagicMock(return_value=99)

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            handle = await store.watch_key("key1", MagicMock())
        assert store._watches[handle].watch_id == 99

    @pytest.mark.asyncio
    async def test_watch_key_setup_failure(self, store, mock_client):
        """Exception during watch setup raises ConnectionError."""
        mock_client.add_watch_callback = MagicMock(side_effect=RuntimeError("watch failed"))

        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
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
        mock_client.cancel_watch = MagicMock()
        mock_connection_manager.get_client.return_value = mock_client

        mock_client.add_watch_callback = MagicMock(side_effect=[10, 20, 30])
        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            for key in ("a", "b", "c"):
                await store.watch_key(key, MagicMock())
            await store.close()

        assert [c.args[0] for c in mock_client.cancel_watch.call_args_list] == [10, 20, 30]
        assert len(store._watches) == 0
        mock_connection_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_handles_cancel_failure(self, store, mock_connection_manager):
        """Close continues even if canceling a watcher fails."""
        mock_client = MagicMock()
        mock_client.cancel_watch = MagicMock(side_effect=RuntimeError("cancel err"))
        mock_connection_manager.get_client.return_value = mock_client

        mock_client.add_watch_callback = MagicMock(side_effect=[10, 20])
        with patch("app.config.providers.etcd.etcd3_store.asyncio.to_thread", side_effect=_passthrough_to_thread):
            for key in ("a", "b"):
                await store.watch_key(key, MagicMock())
            await store.close()

        assert mock_client.cancel_watch.call_count == 2
        assert len(store._watches) == 0
        mock_connection_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_no_watchers(self, store, mock_connection_manager):
        """Close works cleanly with no active watchers."""
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


class _RejectedToken(grpc.RpcError):
    """What etcd3 re-raises when etcd rejects an expired login token."""

    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.UNAUTHENTICATED

    def details(self) -> str:
        return "etcdserver: invalid auth token"


class _PermissionDenied(grpc.RpcError):
    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.PERMISSION_DENIED


class _LoggedInClient:
    """One etcd3.client, i.e. one login. ``rejects`` makes every data call fail."""

    def __init__(self, rejects: grpc.RpcError | None = None) -> None:
        self.rejects = rejects
        self.closed = False
        self.gets = 0
        self.watches: dict = {}
        self.cancelled: list = []
        self.added: list = []
        self._ids = iter(range(100))

    def status(self) -> str:
        return "ok"

    def close(self) -> None:
        self.closed = True

    def get(self, key: str) -> tuple:
        self.gets += 1
        if self.rejects is not None:
            raise self.rejects
        return b'"v"', None

    def add_watch_callback(self, key: str, callback) -> int:
        watch_id = next(self._ids)
        self.watches[watch_id] = (key, callback)
        self.added.append(key)
        return watch_id

    add_watch_prefix_callback = add_watch_callback

    def cancel_watch(self, watch_id) -> None:
        self.cancelled.append(watch_id)
        self.watches.pop(watch_id, None)


class _Cancelled(grpc.RpcError):
    """What an RPC in flight gets when its channel is closed under it."""

    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.CANCELLED

    def details(self) -> str:
        return "Channel closed!"


class _ClosedUnderAGet(_LoggedInClient):
    """Rejects the token for "/expired"; any other get is in flight until the
    client is closed, then fails the way grpc fails it."""

    def __init__(self, failure: Exception) -> None:
        super().__init__()
        self.failure = failure
        self.in_flight = threading.Event()
        self.closed_event = threading.Event()

    def close(self) -> None:
        super().close()
        self.closed_event.set()

    def get(self, key: str) -> tuple:
        self.gets += 1
        if key == "/expired":
            raise _RejectedToken()
        self.in_flight.set()
        self.closed_event.wait(5)
        raise self.failure


class TestLoggingInAgain:
    """etcd3 logs in once, when the client is built; etcd's tokens expire (5
    minutes by default), after which every call is rejected."""

    @pytest.fixture
    def make_store(self):
        from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore

        def make(*clients) -> tuple:
            factory = patch(
                "app.config.providers.etcd.etcd3_connection_manager.etcd3.client",
                side_effect=list(clients),
            )
            store = Etcd3DistributedKeyValueStore(
                serializer=lambda v: json.dumps(v).encode(),
                deserializer=lambda b: json.loads(b.decode()),
                host="etcd.test",
                port=2379,
                username="pipeshub",
                password="etcd-throwaway-test-password",
            )
            return store, factory

        return make

    async def test_an_expired_token_logs_in_again_and_the_call_is_retried(self, make_store) -> None:
        first, second = _LoggedInClient(rejects=_RejectedToken()), _LoggedInClient()
        store, factory = make_store(first, second)

        with factory as client_factory:
            assert await store.get_key("/k") == "v"

        assert client_factory.call_count == 2
        assert all(c.kwargs["user"] == "pipeshub" for c in client_factory.call_args_list)
        assert first.closed
        assert (first.gets, second.gets) == (1, 1)

    async def test_a_second_rejection_is_raised_not_retried_again(self, make_store) -> None:
        first, second = _LoggedInClient(rejects=_RejectedToken()), _LoggedInClient(rejects=_RejectedToken())
        store, factory = make_store(first, second)

        with factory as client_factory, pytest.raises(ConnectionError, match="invalid auth token|Failed to get key"):
            await store.get_key("/k")

        assert client_factory.call_count == 2
        assert (first.gets, second.gets) == (1, 1)

    async def test_other_grpc_errors_do_not_log_in_again(self, make_store) -> None:
        only = _LoggedInClient(rejects=_PermissionDenied())
        store, factory = make_store(only)

        with factory as client_factory, pytest.raises(ConnectionError):
            await store.get_key("/k")

        assert client_factory.call_count == 1

    async def test_calls_rejected_together_log_in_once(self, make_store) -> None:
        first, second = _LoggedInClient(rejects=_RejectedToken()), _LoggedInClient()
        store, factory = make_store(first, second)

        with factory as client_factory:
            results = await asyncio.gather(store.get_key("/a"), store.get_key("/b"))

        assert results == ["v", "v"]
        assert client_factory.call_count == 2

    async def test_watches_move_to_the_new_login(self, make_store) -> None:
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        received: list = []
        errors: list = []

        with factory as client_factory:
            handle = await store.watch_key("/k", received.append, errors.append)
            await store.subscribe_changes(lambda _key: None)
            first.rejects = _RejectedToken()
            assert await store.get_key("/k") == "v"

            assert client_factory.call_count == 2
            assert sorted(first.cancelled) == [0, 1]
            assert sorted(key for key, _ in second.watches.values()) == ["/", "/k"]
            new_id = next(i for i, (key, _) in second.watches.items() if key == "/k")
            second.watches[new_id][1](_watch_response(_put_event("/k", b'"changed"')))

            await store.cancel_watch("/k", handle)

        assert received == ["changed"]
        assert errors == []
        assert second.cancelled == [new_id]

    async def test_watches_wait_for_the_next_client_when_logging_in_again_fails(self, make_store) -> None:
        first, third = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, ConnectionError("etcd down"), third)
        received: list = []
        errors: list = []
        changes: list = []

        with factory:
            await store.watch_key("/k", received.append, errors.append)
            await store.subscribe_changes(changes.append)
            first.rejects = _RejectedToken()
            with pytest.raises(ConnectionError):
                await store.get_key("/k")
            assert errors == []
            # Cached reads never call the store, so the gap is reported now.
            assert changes == [CLEAR_ALL]

            assert await store.get_key("/k") == "v"

        assert sorted(first.cancelled) == [0, 1]
        assert sorted(third.added) == ["/", "/k"]
        new_id = next(i for i, (key, _) in third.watches.items() if key == "/k")
        third.watches[new_id][1](_watch_response(_put_event("/k", b'"changed"')))
        assert received == ["changed"]
        assert errors == []
        assert changes == [CLEAR_ALL, CLEAR_ALL]

    async def test_cancelling_on_a_rejected_client_leaves_other_watches_alone(self, make_store) -> None:
        """A rejected cancel must not log in again and retry the old stream's id
        on the new client, where that id now belongs to another watch."""
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        other: list = []

        with factory as client_factory:
            doomed = await store.watch_key("/a", lambda _value: None)
            await store.watch_key("/b", other.append)
            first.cancel_watch = MagicMock(side_effect=_RejectedToken())

            await store.cancel_watch("/a", doomed)

        assert client_factory.call_count == 1
        assert second.cancelled == []
        first.watches[1][1](_watch_response(_put_event("/b", b'"still here"')))
        assert other == ["still here"]
        assert [w.key for w in store._watches.values()] == ["/b"]

    async def test_a_key_watch_that_stopped_is_not_revived_by_a_later_login(self, make_store) -> None:
        """The caller was told to watch again; a revived copy would fire twice."""
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        errors: list = []

        with factory:
            await store.watch_key("/k", lambda _value: None, errors.append)
            first.watches[0][1](None)
            first.rejects = _RejectedToken()
            assert await store.get_key("/k") == "v"

        assert len(errors) == 1
        assert second.added == []
        assert store._watches == {}

    @pytest.mark.parametrize("ending", [None, RevisionCompactedError(7)])
    async def test_a_subscription_that_stopped_drops_the_cache_and_is_watched_again(
        self, make_store, ending
    ) -> None:
        only = _LoggedInClient()
        store, factory = make_store(only)
        changes: list = []

        with factory:
            await store.subscribe_changes(changes.append)
            only.watches[0][1](ending)
            # Before any store call: cached reads never reach the store, so
            # only this clear makes the next one miss and bring the watch back.
            assert changes == [CLEAR_ALL]

            await store.get_key("/k")

        assert only.added == ["/", "/"]
        only.watches[1][1](_watch_response(_put_event("/x", b'"1"')))
        assert changes == [CLEAR_ALL, CLEAR_ALL, "/x"]

    async def test_a_watch_added_during_a_new_login_lands_only_on_the_new_client(self, make_store) -> None:
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        cancelling, release = threading.Event(), threading.Event()
        cancel = first.cancel_watch

        def slow_cancel(watch_id) -> None:
            cancelling.set()
            release.wait(5)
            cancel(watch_id)

        with factory:
            await store.watch_key("/k", lambda _value: None)
            first.cancel_watch = slow_cancel
            first.rejects = _RejectedToken()
            login = asyncio.create_task(store.get_key("/k"))
            await asyncio.to_thread(cancelling.wait, 5)
            added = asyncio.create_task(store.watch_key("/new", lambda _value: None))
            await asyncio.sleep(0.05)
            release.set()
            await asyncio.gather(login, added)

        assert first.added == ["/k"]
        assert sorted(second.added) == ["/k", "/new"]
        assert len(store._watches) == 2

    @pytest.mark.parametrize(
        "failure", [_Cancelled(), ValueError("Cannot invoke RPC on closed channel!")]
    )
    async def test_a_call_whose_client_a_new_login_closed_is_retried(self, make_store, failure) -> None:
        first, second = _ClosedUnderAGet(failure), _LoggedInClient()
        store, factory = make_store(first, second)

        with factory as client_factory:
            in_flight = asyncio.create_task(store.get_key("/k"))
            await asyncio.to_thread(first.in_flight.wait, 5)
            expired = asyncio.create_task(store.get_key("/expired"))
            results = await asyncio.gather(in_flight, expired)

        assert results == ["v", "v"]
        assert client_factory.call_count == 2
        assert second.gets == 2

    async def test_a_failure_on_the_current_client_is_not_retried(self, make_store) -> None:
        only = _LoggedInClient(rejects=_Cancelled())
        store, factory = make_store(only)

        with factory as client_factory, pytest.raises(ConnectionError):
            await store.get_key("/k")

        assert client_factory.call_count == 1
        assert only.gets == 1

    async def test_a_subscription_etcd_keeps_refusing_is_retried_with_backoff(self, make_store) -> None:
        only = _LoggedInClient()
        store, factory = make_store(only)
        now = [1000.0]
        store._clock = lambda: now[0]
        changes: list = []
        refusing = MagicMock(side_effect=ConnectionError("etcd refused the watch"))

        async def reads(count: int) -> None:
            for _ in range(count):
                assert await store.get_key("/k") == "v"

        with factory:
            await store.subscribe_changes(changes.append)
            only.add_watch_prefix_callback = refusing
            only.watches[0][1](None)
            assert changes == [CLEAR_ALL]

            await reads(5)
            assert refusing.call_count == 1

            # Nothing is due inside the pause, so reads never wait on the lock.
            async with store._login_lock:
                await asyncio.wait_for(reads(3), timeout=2)
            assert refusing.call_count == 1

            for step, attempts in [(1.0, 2), (1.0, 2), (1.0, 3), (3.9, 3), (0.1, 4)]:
                now[0] += step
                await reads(3)
                assert refusing.call_count == attempts
            assert changes == [CLEAR_ALL]

            del only.add_watch_prefix_callback
            now[0] += 8.0
            await reads(3)

        assert refusing.call_count == 4
        assert changes == [CLEAR_ALL, CLEAR_ALL]
        new_id = max(only.watches)
        only.watches[new_id][1](_watch_response(_put_event("/x", b'"1"')))
        assert changes == [CLEAR_ALL, CLEAR_ALL, "/x"]

    async def test_a_subscription_that_fails_to_start_is_not_kept(self, make_store) -> None:
        only = _LoggedInClient()
        only.add_watch_prefix_callback = MagicMock(side_effect=ConnectionError("refused"))
        store, factory = make_store(only)

        with factory, pytest.raises(ConnectionError):
            await store.subscribe_changes(lambda _key: None)

        assert store._watches == {}

    async def test_a_new_login_that_cannot_move_the_subscription_clears_once(self, make_store) -> None:
        """The re-login cancels the subscription before closing the old client,
        so the stopped branch never runs; the failed move reports the gap."""
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        now = [1000.0]
        store._clock = lambda: now[0]
        changes: list = []
        refusing = MagicMock(side_effect=ConnectionError("etcd refused the watch"))

        with factory:
            await store.subscribe_changes(changes.append)
            second.add_watch_prefix_callback = refusing
            first.rejects = _RejectedToken()
            assert await store.get_key("/k") == "v"
            assert changes == [CLEAR_ALL]

            for _ in range(3):
                now[0] += 60.0
                assert await store.get_key("/k") == "v"
            assert refusing.call_count == 4
            assert changes == [CLEAR_ALL]

            del second.add_watch_prefix_callback
            now[0] += 60.0
            assert await store.get_key("/k") == "v"

        assert changes == [CLEAR_ALL, CLEAR_ALL]
        assert second.added == ["/"]

    async def test_a_new_login_restarts_the_backoff_at_one_second(self, make_store) -> None:
        first, second = _LoggedInClient(), _LoggedInClient()
        store, factory = make_store(first, second)
        now = [1000.0]
        store._clock = lambda: now[0]
        first_refusing = MagicMock(side_effect=ConnectionError("refused"))
        second_refusing = MagicMock(side_effect=ConnectionError("refused"))

        with factory:
            await store.subscribe_changes(lambda _key: None)
            first.add_watch_prefix_callback = first_refusing
            first.watches[0][1](None)
            for step in (0.0, 1.0, 2.0, 4.0, 8.0):
                now[0] += step
                await store.get_key("/k")
            assert first_refusing.call_count == 5

            second.add_watch_prefix_callback = second_refusing
            first.rejects = _RejectedToken()
            await store.get_key("/k")
            assert second_refusing.call_count == 1

            now[0] += 1.0
            await store.get_key("/k")

        assert second_refusing.call_count == 2
