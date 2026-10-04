"""Behaviour of EncryptedKeyValueStore over its real backends.

The store is built through its real __init__ and KeyValueStoreFactory, on a
real RedisDistributedKeyValueStore or Etcd3DistributedKeyValueStore. Only the
network edge is faked: the Redis connection provider, or the etcd3 client.
Encryption is the real AES-GCM service keyed from a throwaway test secret.
"""

import asyncio
import fnmatch
import logging
from collections.abc import AsyncIterator, Iterator
from unittest.mock import patch

import pytest
from etcd3.etcdrpc import kv_pb2, rpc_pb2
from etcd3.events import Event, new_event
from etcd3.watch import WatchResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.services.vector_db.strategy_resolver import (
    StrategyConfigurationError,
    resolve_persisted_strategy_name,
)
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService

ENDPOINTS_KEY = config_node_constants.ENDPOINTS.value
SECRET_KEY = "encstore-throwaway-test-secret"
OTHER_HEX_KEY = "22" * 32
REDIS_PREFIX = "t:"
REDIS_PROVIDER = "app.config.providers.redis.redis_store.get_redis_provider"
ETCD3_CLIENT = "app.config.providers.etcd.etcd3_connection_manager.etcd3.client"
LOGGER = logging.getLogger("encstore-test")


class FakeRedisServer:
    """What several processes share: the keyspace and the Pub/Sub channels."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.subscribers: dict = {}
        self.down = False

    def check(self) -> None:
        if self.down:
            raise ConnectionError("redis down")

    def publish(self, channel: str, message: str) -> int:
        self.check()
        queues = self.subscribers.get(channel, [])
        for queue in queues:
            queue.put_nowait({"type": "message", "data": message.encode("utf-8")})
        return len(queues)


class FakeRedisClient:
    """The slice of redis.asyncio.Redis the store uses."""

    def __init__(self, server: FakeRedisServer) -> None:
        self.server = server
        self.closed = False

    async def get(self, key: str) -> bytes | None:
        self.server.check()
        return self.server.data.get(key)

    async def set(self, key: str, value: bytes, ex=None, nx=False, xx=False) -> bool | None:
        self.server.check()
        if nx and key in self.server.data:
            return None
        if xx and key not in self.server.data:
            return None
        self.server.data[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.server.check()
        return 1 if self.server.data.pop(key, None) is not None else 0

    async def scan_iter(self, match: str) -> AsyncIterator[bytes]:
        self.server.check()
        for key in list(self.server.data):
            if fnmatch.fnmatchcase(key, match):
                yield key.encode("utf-8")

    async def close(self) -> None:
        self.closed = True


class FakePubSub:
    def __init__(self, server: FakeRedisServer) -> None:
        self.server = server
        self.queue: asyncio.Queue = asyncio.Queue()
        self.channel: str | None = None

    async def subscribe(self, channel: str) -> None:
        self.server.check()
        self.channel = channel
        self.server.subscribers.setdefault(channel, []).append(self.queue)

    async def listen(self) -> AsyncIterator[dict]:
        while True:
            yield await self.queue.get()

    async def unsubscribe(self, channel: str) -> None:
        queues = self.server.subscribers.get(channel, [])
        if self.queue in queues:
            queues.remove(self.queue)

    async def close(self) -> None:
        return None


class FakePubSubClient:
    def __init__(self, server: FakeRedisServer) -> None:
        self.server = server

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self.server)

    async def aclose(self) -> None:
        return None


class FakeRedisProvider:
    """Stands in for IRedisConnectionProvider; one per process, one server shared."""

    key_namespace = ""

    def __init__(self, server: FakeRedisServer) -> None:
        self.server = server
        self.clients: list = []

    def create_client(self, options=None) -> FakeRedisClient:
        client = FakeRedisClient(self.server)
        self.clients.append(client)
        return client

    def create_pubsub_client(self) -> FakePubSubClient:
        return FakePubSubClient(self.server)

    async def publish(self, channel: str, message: str) -> int:
        return self.server.publish(channel, message)


class _Meta:
    def __init__(self, key: str) -> None:
        self.key = key.encode("utf-8")


def _put_event(key: str, value: bytes) -> Event:
    return new_event(kv_pb2.Event(type=kv_pb2.Event.PUT, kv=kv_pb2.KeyValue(key=key.encode(), value=value)))


def _delete_event(key: str) -> Event:
    return new_event(kv_pb2.Event(type=kv_pb2.Event.DELETE, kv=kv_pb2.KeyValue(key=key.encode())))


def _watch_response(*events) -> WatchResponse:
    """What etcd3 0.12 hands a single-key watch callback."""
    return WatchResponse(rpc_pb2.ResponseHeader(), list(events))


class _WatchResponse:
    """The shape etcd3 hands a prefix-watch callback: a batch of events."""

    def __init__(self, keys: list) -> None:
        self.events = [_Meta(k) for k in keys]


class FakeEtcdClient:
    """The slice of etcd3.client the store uses, backed by a dict."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.watches: dict = {}
        self.cancelled: list = []
        self.closed = False
        self.down = False
        self._next_watch_id = 0

    def check(self) -> None:
        if self.down:
            raise ConnectionError("etcd down")

    def status(self) -> str:
        self.check()
        return "ok"

    def close(self) -> None:
        self.closed = True

    def get(self, key: str) -> tuple:
        self.check()
        value = self.data.get(key)
        return value, (_Meta(key) if value is not None else None)

    def put(self, key: str, value: bytes, lease=None) -> object:
        self.check()
        self.data[key] = value
        return object()

    def put_if_not_exists(self, key: str, value: bytes, lease=None) -> bool:
        self.check()
        if key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, key: str) -> bool:
        self.check()
        return self.data.pop(key, None) is not None

    def get_all(self) -> list:
        self.check()
        return [(value, _Meta(key)) for key, value in self.data.items()]

    def add_watch_callback(self, key: str, callback) -> int:
        self._next_watch_id += 1
        self.watches[self._next_watch_id] = (key, callback)
        return self._next_watch_id

    def add_watch_prefix_callback(self, prefix: str, callback) -> int:
        return self.add_watch_callback(prefix, callback)

    def cancel_watch(self, watch_id) -> None:
        self.cancelled.append(watch_id)
        self.watches.pop(watch_id, None)

    def emit_prefix_change(self, key: str) -> None:
        for prefix, callback in list(self.watches.values()):
            if key.startswith(prefix):
                callback(_WatchResponse([key]))


class Harness:
    """One EncryptedKeyValueStore plus direct access to what its backend holds."""

    def __init__(self, kind: str, store: EncryptedKeyValueStore, backend) -> None:
        self.kind = kind
        self.store = store
        self.backend = backend

    def _raw_key(self, key: str) -> str:
        return f"{REDIS_PREFIX}{key}" if self.kind == "redis" else key

    @property
    def raw(self) -> dict:
        data = self.backend.data
        if self.kind == "etcd":
            return data
        return {k[len(REDIS_PREFIX):]: v for k, v in data.items()}

    def put_raw(self, key: str, value: bytes) -> None:
        self.backend.data[self._raw_key(key)] = value

    def raw_value(self, key: str) -> bytes:
        return self.backend.data[self._raw_key(key)]

    def set_down(self, down: bool) -> None:
        self.backend.down = down

    def connection_closed(self) -> bool:
        if self.kind == "etcd":
            return self.backend.closed
        return all(c.closed for c in self.store.store._provider.clients)


@pytest.fixture(autouse=True)
def _fresh_encryption_singleton(monkeypatch) -> None:
    # EncryptionService caches its first instance process-wide; without a
    # reset, whichever test ran first would fix the key for every other one.
    monkeypatch.setattr(EncryptionService, "_instance", None)


@pytest.fixture
def env(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("REDIS_HOST", "redis.test")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_DB", "0")
    monkeypatch.setenv("REDIS_KV_PREFIX", REDIS_PREFIX)
    monkeypatch.setenv("ETCD_URL", "http://etcd.test:2379")
    for name in ("REDIS_PASSWORD", "REDIS_TIMEOUT", "ETCD_TIMEOUT", "ETCD_USERNAME", "ETCD_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def redis_server() -> FakeRedisServer:
    return FakeRedisServer()


def _redis_store(monkeypatch, server: FakeRedisServer) -> EncryptedKeyValueStore:
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(server)):
        return EncryptedKeyValueStore(LOGGER)


@pytest.fixture
def redis_harness(env, monkeypatch, redis_server) -> Harness:
    return Harness("redis", _redis_store(monkeypatch, redis_server), redis_server)


@pytest.fixture
def etcd_harness(env, monkeypatch) -> Iterator[Harness]:
    monkeypatch.setenv("KV_STORE_TYPE", "etcd")
    fake = FakeEtcdClient()
    with patch(ETCD3_CLIENT, return_value=fake):
        yield Harness("etcd", EncryptedKeyValueStore(LOGGER), fake)


@pytest.fixture(params=["redis", "etcd"])
def h(request) -> Harness:
    return request.getfixturevalue(f"{request.param}_harness")


def _use_another_secret(store: EncryptedKeyValueStore) -> None:
    store.encryption_service = EncryptionService("aes-256-gcm", OTHER_HEX_KEY, LOGGER)


class TestRoundTrip:
    async def test_secret_values_are_stored_encrypted_and_read_back(self, h) -> None:
        secret = {"clientId": "id-1", "clientSecret": "not-a-real-secret"}

        assert await h.store.create_key("/services/connectors/x/config", secret) is True

        stored = h.raw_value("/services/connectors/x/config").decode("utf-8")
        assert "not-a-real-secret" not in stored
        assert stored.strip('"').count(":") == 2
        assert await h.store.get_key("/services/connectors/x/config") == secret

    async def test_excluded_keys_are_stored_as_plain_json(self, h) -> None:
        endpoints = {"connectors": {"endpoint": "http://connectors:8088"}}

        assert await h.store.create_key(ENDPOINTS_KEY, endpoints) is True

        assert await h.store.get_key(ENDPOINTS_KEY) == endpoints

    async def test_a_missing_key_is_none_even_when_asked_to_raise(self, h) -> None:
        assert await h.store.get_key("/absent") is None
        assert await h.store.get_key("/absent", raise_on_error=True) is None


class TestUnreadableValues:
    """A value that exists but cannot be read must never look like an empty slot
    to a caller that asked (raise_on_error=True)."""

    async def test_a_value_under_another_secret_key(self, h) -> None:
        await h.store.create_key("/k", {"v": 1})
        _use_another_secret(h.store)

        assert await h.store.get_key("/k") is None
        with pytest.raises(DecryptionError):
            await h.store.get_key("/k", raise_on_error=True)

    async def test_plain_text_under_an_encrypted_key(self, h) -> None:
        h.put_raw("/k", b'"just text"')

        assert await h.store.get_key("/k") is None
        with pytest.raises(DecryptionError):
            await h.store.get_key("/k", raise_on_error=True)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "A plain JSON object, list or number stored under a key that should be "
            "encrypted is returned as if it were real config, skipping decryption. "
            "The early return that does this was added on purpose in #3028 without "
            "a stated reason, so whether anything relies on it is for the owners."
        ),
    )
    async def test_a_plain_json_object_under_an_encrypted_key(self, redis_harness) -> None:
        redis_harness.put_raw("/k", b'{"v": 1}')

        with pytest.raises(DecryptionError):
            await redis_harness.store.get_key("/k", raise_on_error=True)

    async def test_bytes_that_are_not_utf8(self, h) -> None:
        h.put_raw("/k", b"\xff\xfe\xfd")

        assert await h.store.get_key("/k") is None
        with pytest.raises(ConnectionError):
            await h.store.get_key("/k", raise_on_error=True)

    async def test_the_store_being_down(self, h) -> None:
        await h.store.create_key("/k", {"v": 1})
        h.set_down(True)

        assert await h.store.get_key("/k") is None
        with pytest.raises(ConnectionError):
            await h.store.get_key("/k", raise_on_error=True)


class TestCreateIfAbsent:
    """overwrite=False must not replace a value merely because it can't be read."""

    async def test_an_absent_key_is_created(self, h) -> None:
        assert await h.store.create_key("/k", {"v": 1}, overwrite=False) is True

        assert await h.store.get_key("/k") == {"v": 1}

    async def test_a_readable_value_is_kept(self, h) -> None:
        await h.store.create_key("/k", {"v": "theirs"})

        assert await h.store.create_key("/k", {"v": "mine"}, overwrite=False) is False

        assert await h.store.get_key("/k") == {"v": "theirs"}

    async def test_a_value_under_another_secret_key_is_kept(self, h) -> None:
        await h.store.create_key("/k", {"v": "theirs"})
        before = h.raw_value("/k")
        _use_another_secret(h.store)

        assert await h.store.create_key("/k", {"v": "mine"}, overwrite=False) is False

        assert h.raw_value("/k") == before

    async def test_bytes_that_are_not_utf8_are_kept(self, h) -> None:
        h.put_raw("/k", b"\xff\xfe\xfd")

        assert await h.store.create_key("/k", {"v": "mine"}, overwrite=False) is False

        assert h.raw_value("/k") == b"\xff\xfe\xfd"

    async def test_an_outage_is_raised_not_reported_as_already_there(self, h) -> None:
        h.set_down(True)

        with pytest.raises(ConnectionError):
            await h.store.create_key("/k", {"v": "mine"}, overwrite=False)

        assert h.raw == {}

    async def test_an_outage_is_raised_by_create_config_if_absent(self, h) -> None:
        """The method documents that it does not swallow store failures."""
        service = TestChangeNotifications._service(h.store)
        h.set_down(True)

        with pytest.raises(ConnectionError):
            await service.create_config_if_absent("/k", {"v": "mine"})

        assert h.raw == {}

    async def test_set_config_still_answers_false_during_an_outage(self, h) -> None:
        service = TestChangeNotifications._service(h.store)
        h.set_down(True)

        assert await service.set_config("/k", {"v": "mine"}) is False

    async def test_the_strategy_resolver_names_the_outage(self, h, monkeypatch) -> None:
        """Its only caller used to hear "already exists" and then report a value
        it could not read back, when really the store was down."""
        monkeypatch.delenv("VECTOR_COLLECTION_STRATEGY", raising=False)
        service = TestChangeNotifications._service(h.store)
        h.set_down(True)

        with pytest.raises(StrategyConfigurationError, match="Could not persist") as raised:
            await resolve_persisted_strategy_name(service, LOGGER)

        assert "is running and reachable" in str(raised.value)


class TestRecovery:
    async def test_reads_work_again_once_the_store_is_back(self, h) -> None:
        h.set_down(True)
        with pytest.raises(ConnectionError):
            await h.store.get_key("/k", raise_on_error=True)
        with pytest.raises(ConnectionError):
            await h.store.create_key("/k", {"v": 1})

        h.set_down(False)

        assert await h.store.create_key("/k", {"v": 1}) is True
        assert await h.store.get_key("/k", raise_on_error=True) == {"v": 1}


class TestClose:
    async def test_close_releases_the_connection(self, h) -> None:
        await h.store.get_key("/any")

        await h.store.close()

        assert h.connection_closed() is True


class TestChangeNotifications:
    """ConfigurationService drops its cached copy of a key when another process
    changes it, and hears of the change only through this store."""

    @staticmethod
    def _service(store: EncryptedKeyValueStore) -> ConfigurationService:
        from tests.unit.config.test_configuration_service import _build_service

        return _build_service(store=store)

    async def test_a_redis_write_in_one_process_clears_the_cache_in_another(
        self, env, monkeypatch, redis_server
    ) -> None:
        writer = self._service(_redis_store(monkeypatch, redis_server))
        reader_store = _redis_store(monkeypatch, redis_server)
        reader = self._service(reader_store)
        reader.cache["/services/connectors/x/config"] = {"v": "stale"}

        handle = await reader_store.subscribe_changes(reader._invalidation_callback)
        for _ in range(10):
            if redis_server.subscribers:
                break
            await asyncio.sleep(0)
        assert await writer.set_config("/services/connectors/x/config", {"v": "fresh"}) is True
        for _ in range(10):
            if "/services/connectors/x/config" not in reader.cache:
                break
            await asyncio.sleep(0)

        assert "/services/connectors/x/config" not in reader.cache
        await reader_store.unsubscribe_changes(handle)
        await asyncio.sleep(0)
        assert handle.done()
        assert all(not queues for queues in redis_server.subscribers.values())

    async def test_an_etcd_change_clears_the_cache(self, etcd_harness) -> None:
        reader = self._service(etcd_harness.store)
        reader.cache["/services/connectors/x/config"] = {"v": "stale"}

        handle = await etcd_harness.store.subscribe_changes(reader._invalidation_callback)
        etcd_harness.backend.emit_prefix_change("/services/connectors/x/config")

        assert "/services/connectors/x/config" not in reader.cache
        await etcd_harness.store.unsubscribe_changes(handle)
        assert etcd_harness.backend.cancelled == [handle]


class TestListKeysInDirectory:
    @pytest.mark.parametrize("directory", ["", "/"])
    async def test_root_lists_every_key(self, h, directory) -> None:
        await h.store.create_key("/a/1", 1)
        await h.store.create_key("/b/2", 2)

        assert sorted(await h.store.list_keys_in_directory(directory)) == ["/a/1", "/b/2"]

    async def test_keys_outside_the_directory_are_left_out(self, h) -> None:
        await h.store.create_key("/services/toolsets/i1/u1", 1)
        await h.store.create_key("/services/mcp/x", 2)

        assert await h.store.list_keys_in_directory("/services/toolsets/") == [
            "/services/toolsets/i1/u1"
        ]

    async def test_an_outage_is_raised_not_answered_with_an_empty_list(self, h) -> None:
        await h.store.create_key("/services/toolsets/i1/u1", 1)
        h.set_down(True)

        with pytest.raises(ConnectionError):
            await h.store.list_keys_in_directory("/services/toolsets/")


class TestWatchKey:
    """A watcher gets exactly what get_key would return, or an error through
    error_callback, never the stored ciphertext."""

    async def test_a_redis_watcher_gets_the_decrypted_value(self, redis_harness) -> None:
        received: list = []
        await redis_harness.store.watch_key("/k", received.append)

        await redis_harness.store.create_key("/k", {"v": 1})
        await redis_harness.store.delete_key("/k")

        assert received == [{"v": 1}, None]

    async def test_a_redis_watcher_on_an_excluded_key_gets_the_parsed_value(
        self, redis_harness
    ) -> None:
        received: list = []
        await redis_harness.store.watch_key(ENDPOINTS_KEY, received.append)

        await redis_harness.store.create_key(ENDPOINTS_KEY, {"a": 1})

        assert received == [{"a": 1}]
        assert received[0] == await redis_harness.store.get_key(ENDPOINTS_KEY)

    async def test_a_redis_watcher_hears_an_undecryptable_value_as_an_error(
        self, redis_harness
    ) -> None:
        """The Redis store drops error_callback and only logs a callback's
        exception, so this store has to deliver the error itself."""
        received: list = []
        errors: list = []
        await redis_harness.store.watch_key("/k", received.append, errors.append)

        await redis_harness.store.store.create_key("/k", "aa:bb:cc")

        assert received == []
        assert len(errors) == 1
        assert isinstance(errors[0], DecryptionError)

    async def test_an_etcd_watcher_gets_the_decrypted_value(self, etcd_harness) -> None:
        received: list = []
        errors: list = []
        await etcd_harness.store.watch_key("/k", received.append, errors.append)
        (_, on_change), = etcd_harness.backend.watches.values()
        ciphertext = etcd_harness.store.encryption_service.encrypt('{"v": 1}')

        on_change(_watch_response(_put_event("/k", ciphertext.encode())))
        on_change(_watch_response(_put_event("/k", b"aa:bb:cc")))
        on_change(_watch_response(_delete_event("/k")))

        assert received == [{"v": 1}, None]
        assert len(errors) == 1
        assert isinstance(errors[0], DecryptionError)


class TestDirectoryBoundary:
    async def test_a_trailing_slash_keeps_neighbouring_paths_out(self, h) -> None:
        """Callers pass "/services/mcp/credentials/{id}/" and delete every key
        listed; the bare path and a key for instance "{id}-2" are not inside it."""
        for key in (
            "/services/mcp/credentials/i1/u1",
            "/services/mcp/credentials/i1",
            "/services/mcp/credentials/i1-2/u1",
        ):
            await h.store.create_key(key, {"token": "x"})

        listed = await h.store.list_keys_in_directory("/services/mcp/credentials/i1/")

        assert listed == ["/services/mcp/credentials/i1/u1"]

    async def test_a_directory_without_a_trailing_slash_is_unchanged(self, h) -> None:
        await h.store.create_key("/services/toolsets/i1/u1", 1)
        await h.store.create_key("/services/toolsets-old/u1", 2)

        assert sorted(await h.store.list_keys_in_directory("/services/toolsets")) == [
            "/services/toolsets-old/u1",
            "/services/toolsets/i1/u1",
        ]


class TestEtcdLogin:
    async def test_etcd_username_and_password_are_used_to_log_in(self, env, monkeypatch) -> None:
        monkeypatch.setenv("KV_STORE_TYPE", "etcd")
        monkeypatch.setenv("ETCD_USERNAME", "pipeshub")
        monkeypatch.setenv("ETCD_PASSWORD", "etcd-throwaway-test-password")
        with patch(ETCD3_CLIENT, return_value=FakeEtcdClient()) as client_factory:
            store = EncryptedKeyValueStore(LOGGER)
            await store.get_key("/any")

        kwargs = client_factory.call_args.kwargs
        assert kwargs["user"] == "pipeshub"
        assert kwargs["password"] == "etcd-throwaway-test-password"
