import asyncio
import itertools
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, List, Optional, TypeVar

import etcd3
import grpc
from etcd3.events import DeleteEvent, PutEvent

from app.config.key_value_store import KeyValueStore
from app.config.providers.etcd.etcd3_connection_manager import (
    ConnectionConfig,
    Etcd3ConnectionManager,
)
from app.utils.logger import create_logger

logger = create_logger("etcd")

T = TypeVar("T")
R = TypeVar("R")


def _is_rejected_login(error: grpc.RpcError) -> bool:
    """etcd answers UNAUTHENTICATED ("etcdserver: invalid auth token") once the
    token the client got at login has expired; etcd3 re-raises it untranslated."""
    code = getattr(error, "code", None)
    return callable(code) and code() == grpc.StatusCode.UNAUTHENTICATED


CLEAR_ALL = "__CLEAR_ALL__"
_RETRY_FIRST_SECONDS = 1.0
_RETRY_MAX_SECONDS = 60.0


@dataclass
class _Watch:
    """A watch the store can register again on a new client.

    ``dead`` is set, from etcd3's watch thread, once etcd3 has dropped it.
    A subscription (``keep_alive``) is then registered again; a key watch has
    already told its caller, through ``error_callback``, and is forgotten.
    ``on_missed_changes`` runs once when a gap in a subscription starts and
    once when it ends; ``gap_reported`` records that the start was reported.
    """

    add: str
    key: str
    keep_alive: bool
    on_missed_changes: Callable[[], None] | None
    on_response: Callable[[object], None] = lambda _response: None
    client: Any = None
    watch_id: Any = None
    dead: bool = False
    gap_reported: bool = False
    retry_in: float = 0.0
    retry_at: float = 0.0


class Etcd3DistributedKeyValueStore(KeyValueStore[T], Generic[T]):
    """
    ETCD3-based implementation of the distributed key-value store.

    This implementation provides a robust, distributed key-value store using ETCD3
    as the backend, with support for watching keys, TTL, and automatic reconnection.

    Attributes:
        connection_manager: Manages ETCD3 connection and reconnection
        serializer: Function to convert values to bytes
        deserializer: Function to convert bytes back to values
    """

    def __init__(
        self,
        serializer: Callable[[T], bytes],
        deserializer: Callable[[bytes], T],
        host: str,
        port: int,
        timeout: float = 5.0,
        ca_cert: Optional[str] = None,
        cert_key: Optional[str] = None,
        cert_cert: Optional[str] = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """
        Initialize the ETCD3 store.

        Args:
            serializer: Function to convert values to bytes
            deserializer: Function to convert bytes back to values
            host: ETCD server host
            port: ETCD server port
            timeout: Connection timeout in seconds
            ca_cert: Optional CA certificate path for TLS
            cert_key: Optional client key path for TLS
            cert_cert: Optional client certificate path for TLS
            username: Optional username for authentication, used only with password
            password: Optional password for authentication, used only with username
        """
        logger.debug("🔧 Initializing ETCD3 store")
        logger.debug("📋 Configuration:")
        logger.debug("   - Host: %s", host)
        logger.debug("   - Port: %s", port)
        logger.debug("   - Timeout: %s", timeout)
        logger.debug("   - SSL enabled: %s", bool(ca_cert or cert_key))

        config = ConnectionConfig(
            hosts=[host],
            port=port,
            timeout=timeout,
            ca_cert=ca_cert,
            cert_key=cert_key,
            cert_cert=cert_cert,
            username=username,
            password=password,
        )
        self._client: Optional[etcd3.client] = None
        self.connection_manager = Etcd3ConnectionManager(config)
        self.serializer = serializer
        self.deserializer = deserializer
        # Keyed by a handle of our own: etcd numbers watches per stream, so a
        # new client after a fresh login reuses the ids the old one gave out.
        self._watches: dict[int, _Watch] = {}
        self._handles = itertools.count(1)
        self._login_lock = asyncio.Lock()
        self._clock: Callable[[], float] = time.monotonic
        logger.debug("✅ ETCD3 store initialized")

    @property
    def client(self) -> Optional[etcd3.client]:
        """Expose the underlying etcd client for watchers and diagnostics."""
        return self._client

    async def _get_client(self) -> etcd3.client:
        """Get the ETCD client, ensuring connection is available."""
        logger.debug("🔄 Getting ETCD client")
        client = await self.connection_manager.get_client()
        logger.debug("✅ Got ETCD client: %s", client)
        self._client = client
        return client

    async def _run(self, operation: Callable[[etcd3.client], R]) -> R:
        """Run one blocking client call off the event loop.

        The etcd3 client logs in only once, when it is built, so when etcd
        rejects its token this builds a new client (a fresh login) and retries
        the call once. A call that fails because another call's new login
        closed its client (in-flight RPCs end CANCELLED, later ones hit a
        closed channel) is retried once on the new client too. A second
        failure, or any failure on the client that is still current, is raised.
        """
        client = await self._client_for_call()
        try:
            return await asyncio.to_thread(operation, client)
        except Exception as e:
            rejected = isinstance(e, grpc.RpcError) and _is_rejected_login(e)
            # Taken before looking: a new login holds this lock from closing
            # the old client until the new one is in place.
            async with self._login_lock:
                if rejected:
                    client = await self._log_in_again_locked(client)
                elif client is not self.connection_manager.client:
                    client = await self._get_client()
                else:
                    raise
            return await asyncio.to_thread(operation, client)

    # Every change to self._watches, and every registration or cancel on a
    # client, happens under _login_lock, so a watch always knows the client
    # it lives on and the id that client gave it.

    def _due(self, watch: _Watch, client: etcd3.client) -> bool:
        return (watch.dead or watch.client is not client) and watch.retry_at <= self._clock()

    async def _client_for_call(self) -> etcd3.client:
        client = await self._get_client()
        if any(self._due(w, client) for w in list(self._watches.values())):
            async with self._login_lock:
                client = await self._get_client()
                await self._sync_watches_locked(client)
        return client

    async def _log_in_again_locked(self, rejected: etcd3.client) -> etcd3.client:
        if self.connection_manager.client is rejected:
            logger.warning(
                "etcd rejected this connection's login token, most likely because "
                "it expired. Logging in to etcd again."
            )
            for watch in list(self._watches.values()):
                if watch.client is rejected and not watch.dead:
                    # Unregistered first, or the old watch thread would report
                    # each one as stopped when its client is closed.
                    await self._cancel_on_owner(watch)
                    watch.client = None
            # If this fails the watches stay listed and move to the next
            # client that connects. Their gap starts now, and cached reads
            # never call the store, so report it here or no one will.
            try:
                await self.connection_manager.reconnect()
            except Exception:
                for watch in list(self._watches.values()):
                    if watch.client is None and not watch.dead and not watch.gap_reported:
                        watch.gap_reported = True
                        self._report_missed_changes(watch)
                raise
            for watch in self._watches.values():
                watch.retry_in = watch.retry_at = 0.0  # a fresh login is a fresh chance
        client = await self._get_client()
        await self._sync_watches_locked(client)
        return client

    async def _sync_watches_locked(self, client: etcd3.client) -> None:
        for handle, watch in list(self._watches.items()):
            if not self._due(watch, client):
                continue
            if watch.dead and not watch.keep_alive:
                del self._watches[handle]
                continue
            if watch.client is not None and not watch.dead:
                await self._cancel_on_owner(watch)
                watch.client = None
            try:
                await self._register_locked(watch, client)
            except Exception as e:
                if not watch.keep_alive:
                    logger.error("❌ Could not watch %s on the new etcd client: %s", watch.key, str(e))
                    del self._watches[handle]
                    watch.on_response(e)
                    continue
                # Kept, and tried again after a growing pause, so a refusing
                # etcd costs one attempt and one log line per step rather
                # than one per store call.
                watch.retry_in = min(
                    max(watch.retry_in * 2, _RETRY_FIRST_SECONDS), _RETRY_MAX_SECONDS
                )
                watch.retry_at = self._clock() + watch.retry_in
                logger.error(
                    "❌ Could not watch %s on etcd (%s); trying again in %.0f s",
                    watch.key, str(e), watch.retry_in,
                )
                # A dead watch reported its gap when it died; a moved one whose
                # first registration fails reports it here, once.
                if not watch.dead and not watch.gap_reported:
                    watch.gap_reported = True
                    self._report_missed_changes(watch)
                continue
            watch.retry_in = watch.retry_at = 0.0
            watch.gap_reported = False
            self._report_missed_changes(watch)

    @staticmethod
    def _report_missed_changes(watch: _Watch) -> None:
        if watch.on_missed_changes is not None:
            watch.on_missed_changes()

    async def _register_locked(self, watch: _Watch, client: etcd3.client) -> None:
        watch.watch_id = await asyncio.to_thread(
            getattr(client, watch.add), watch.key, watch.on_response
        )
        watch.client = client
        watch.dead = False

    async def _cancel_on_owner(self, watch: _Watch) -> None:
        try:
            await asyncio.to_thread(watch.client.cancel_watch, watch.watch_id)
        except Exception as e:
            logger.warning("⚠️ Could not cancel the etcd watch on %s: %s", watch.key, str(e))

    async def _add_watch(
        self,
        add: str,
        key: str,
        handler: Callable[[object], None],
        *,
        keep_alive: bool = False,
        on_missed_changes: Callable[[], None] | None = None,
    ) -> int:
        watch = _Watch(
            add=add, key=key, keep_alive=keep_alive, on_missed_changes=on_missed_changes
        )

        # etcd3 drops a watch once it hands it an exception, or None when its
        # watch thread exits.
        def on_response(response: object) -> None:
            if response is None or isinstance(response, Exception):
                watch.dead = True
            handler(response)

        watch.on_response = on_response
        async with self._login_lock:
            client = await self._get_client()
            try:
                await self._register_locked(watch, client)
            except grpc.RpcError as e:
                if not _is_rejected_login(e):
                    raise
                await self._register_locked(watch, await self._log_in_again_locked(client))
            handle = next(self._handles)
            self._watches[handle] = watch
        return handle

    async def _cancel(self, handle: object) -> None:
        async with self._login_lock:
            watch = self._watches.pop(handle, None)
            if watch is not None and watch.client is not None and not watch.dead:
                await self._cancel_on_owner(watch)

    async def create_key(self, key: str, value: T, overwrite: bool = True, ttl: Optional[int] = None) -> bool:
        """Create a new key in etcd."""
        logger.debug("🔄 Creating key in ETCD: %s", key)
        logger.debug("📋 TTL: %s seconds", ttl if ttl else "None")

        try:
            # Serialize to a JSON-compatible string.  str() on dicts/lists
            # produces Python repr (single quotes) which is not valid JSON.
            if isinstance(value, str):
                value_str = value
            else:
                value_str = json.dumps(value, default=str)
            logger.debug("📋 Serialized value: %s", value_str)

            if not overwrite:
                # One transaction, not get-then-put: with a separate read, every
                # process that starts at once sees the key absent and each is
                # told it owns the value it then overwrites.
                lease = await self._run(lambda c: c.lease(ttl)) if ttl else None
                created = await self._run(
                    lambda c: c.put_if_not_exists(key, value_str.encode(), lease)
                )
                if not created and lease is not None:
                    await self._run(lambda c: c.revoke_lease(lease.id))
                return bool(created)

            # Check if key exists
            logger.debug("🔍 Checking if key exists")
            existing_value = await self._run(lambda c: c.get(key))

            if existing_value[0] is not None:
                logger.debug("📋 Key exists, updating value")
                success = await self._run(lambda c: c.put(key, value_str.encode()))
            else:
                logger.debug("📋 Key doesn't exist, creating new")
                if ttl:
                    logger.debug("🔄 Creating lease with TTL: %s seconds", ttl)
                    lease = await self._run(lambda c: c.lease(ttl))
                    success = await self._run(
                        lambda c: c.put(key, value_str.encode(), lease=lease)
                    )
                else:
                    success = await self._run(lambda c: c.put(key, value_str.encode()))

            logger.debug("✅ Key operation successful: %s", success is not None)
            return success is not None

        except Exception as e:
            logger.error("❌ Failed to create key %s: %s", key, str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to create key: {str(e)}")

    async def update_value(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        # Check if key exists
        existing_value = await self._run(lambda c: c.get(key))
        if existing_value[0] is None:
            raise KeyError(f'Key "{key}" does not exist.')

        # Create lease if TTL is specified
        lease = None
        if ttl is not None:
            lease = await self._run(lambda c: c.lease(ttl))

        # Update value with optional lease
        try:
            serialized_value = self.serializer(value)
            if lease:
                await self._run(lambda c: c.put(key, serialized_value, lease=lease))
            else:
                await self._run(lambda c: c.put(key, serialized_value))
        except Exception as e:
            if lease:
                await self._run(lambda c: c.revoke_lease(lease.id))
            raise ConnectionError(f"Failed to update key: {str(e)}")

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Optional[T]:
        """Get value for key from etcd."""
        logger.debug("🔍 Getting key from ETCD: %s", key)
        try:
            logger.debug("🔄 Executing get operation")
            result = await self._run(lambda c: c.get(key))

            if result[0] is None:
                logger.debug("⚠️ No value found for key")
                return None

            value_bytes = result[0]
            if not value_bytes:
                logger.debug("⚠️ Empty value found for key")
                return None

            try:
                deserialized = self.deserializer(value_bytes)
                # Present bytes that deserialize to nothing could not be read:
                # the factory deserializer answers None for bytes that are not
                # valid UTF-8 instead of raising, so the decode handler below
                # never sees them. Empty bytes are how None is stored, and stay
                # absent.
                if deserialized is None and value_bytes and raise_on_error:
                    raise ValueError("Stored value could not be decoded")
                return deserialized
            except json.JSONDecodeError as e:
                logger.error("❌ Failed to deserialize value: %s", str(e))
                logger.error("📋 Value that failed: %s", value_bytes)
                # A stored value that cannot be read is not an absent one.
                # Surfaces as ConnectionError via the handler below, as every
                # failed read from this store does.
                if raise_on_error:
                    raise
                return None

        except Exception as e:
            logger.error("❌ Failed to get key %s: %s", key, str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to get key: {str(e)}")

    async def delete_key(self, key: str) -> bool:
        try:
            result = await self._run(lambda c: c.delete(key))
            return result is not None
        except Exception as e:
            raise ConnectionError(f"Failed to delete key: {str(e)}")

    async def get_all_keys(self) -> List[str]:
        """Get all keys from etcd."""
        logger.debug("🔍 Getting all keys from ETCD")
        try:
            logger.debug("🔄 Executing get_all operation")
            keys = await self._run(lambda c: list(c.get_all()))
            decoded_keys = [key[1].key.decode("utf-8") for key in keys]
            return decoded_keys
        except Exception as e:
            logger.error("❌ Failed to get all keys: %s", str(e))
            logger.error("📋 Error details:")
            logger.error("   - Type: %s", type(e).__name__)
            logger.error("   - Message: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to get all keys: {str(e)}")

    async def watch_key(
        self,
        key: str,
        callback: Callable[[Optional[T]], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        logger.debug("🔄 Setting up watch for key: %s", key)

        def report(error: Exception) -> None:
            logger.error("❌ Error in watch callback for key %s: %s", key, str(error))
            if error_callback:
                error_callback(error)

        # etcd3 calls this with a WatchResponse holding a batch of events (none
        # for a progress notify), with the exception when the watch fails, or
        # with None when its watch thread exits cleanly. Cancelling a watch
        # unregisters it first, so None always means the watch is gone.
        def watch_callback(response: object) -> None:
            if response is None:
                report(ConnectionError(
                    f"The etcd watch on {key} stopped, so changes to it are no "
                    "longer reported. Watch the key again to resume."
                ))
                return
            if isinstance(response, Exception):
                report(response)
                return
            for event in response.events:
                logger.debug("📋 Watch event for key %s: %s", key, type(event).__name__)
                try:
                    if isinstance(event, PutEvent):
                        callback(self.deserializer(event.value))
                    elif isinstance(event, DeleteEvent):
                        callback(None)
                except Exception as e:
                    report(e)

        try:
            logger.debug("🔄 Adding watch callback")
            watch_id = await self._add_watch("add_watch_callback", key, watch_callback)
            logger.debug("✅ Watch setup complete. ID: %s", watch_id)
            return watch_id
        except Exception as e:
            logger.error("❌ Failed to setup watch: %s", str(e))
            logger.exception("Detailed error stack:")
            raise ConnectionError(f"Failed to watch key: {str(e)}")

    async def list_keys_in_directory(self, directory: str) -> List[str]:
        try:
            # Ensure directory ends with '/' for proper prefix matching
            prefix = directory if directory.endswith("/") else f"{directory}/"
            results = await self._run(lambda c: list(c.get_prefix(prefix)))
            return [key.decode("utf-8") for key, _ in results]
        except Exception as e:
            raise ConnectionError(f"Failed to list keys in directory: {str(e)}")

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        await self._cancel(watch_id)

    # -- KeyValueStore cross-process notification interface (R15) -----------
    #
    # etcd already has a native, cross-process watch mechanism (unlike
    # Redis, which needs Pub/Sub bolted on) -- this just exposes it through
    # the same three methods every store implements, so callers never check
    # `hasattr(self.store, 'client')` / branch on KV_STORE_TYPE to reach it.

    async def subscribe_changes(self, callback: Callable[[str], None]) -> int:
        # There is no error callback here, so every cached value is dropped
        # once when the watch stops, which makes the next cached read reach
        # the store and register it again, and once more when it is back,
        # for values read in between. Refused retries in between stay quiet.
        def _prefix_watch_adapter(event: Any) -> None:  # noqa: ANN401
            if event is None or isinstance(event, Exception):
                logger.error(
                    "The etcd watch behind cross-process change notifications "
                    "stopped (%s). Dropping every cached value; it is watched "
                    "again on the next etcd call.",
                    event if event is not None else "its watch thread exited",
                )
                callback(CLEAR_ALL)
                return
            try:
                for evt in event.events:
                    callback(evt.key.decode("utf-8"))
            except Exception as e:
                logger.error("Error in etcd prefix-watch adapter: %s", str(e))

        return await self._add_watch(
            "add_watch_prefix_callback",
            "/",
            _prefix_watch_adapter,
            keep_alive=True,
            on_missed_changes=lambda: callback(CLEAR_ALL),
        )

    async def publish_change(self, key: str) -> None:  # noqa: ARG002
        """No-op: etcd's own watch above already notifies other processes."""
        return None

    async def unsubscribe_changes(self, handle: object) -> None:
        if handle is None:
            return
        await self._cancel(handle)

    async def close(self) -> None:
        """Clean up resources and close connection."""
        logger.debug("🔄 Closing ETCD3 store")
        logger.debug("📋 Active watchers: %d", len(self._watches))

        for handle in list(self._watches):
            try:
                logger.debug("🔄 Canceling watch: %s", handle)
                await self._cancel(handle)
                logger.debug("✅ Watch canceled successfully")
            except Exception as e:
                logger.warning("⚠️ Failed to cancel watch %s: %s", handle, str(e))

        self._watches.clear()
        logger.debug("🔄 Closing connection manager")
        await self.connection_manager.close()
        logger.debug("✅ ETCD3 store closed successfully")
