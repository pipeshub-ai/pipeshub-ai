"""Tests for Etcd3EncryptedKeyValueStore.

The store is built through its real __init__, on a real inner
Etcd3DistributedKeyValueStore and Etcd3ConnectionManager. Only the etcd3
client is swapped for an in-memory fake, so nothing touches the network, and
encryption is the real AES-GCM service keyed from a throwaway test secret.
"""

import datetime
import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.service import config_node_constants
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService

ENDPOINTS_KEY = config_node_constants.ENDPOINTS.value
SECRET_KEY = "etcdcov-throwaway-test-secret"
OTHER_HEX_KEY = "11" * 32
ETCD3_CLIENT = "app.config.providers.etcd.etcd3_connection_manager.etcd3.client"


class _Meta:
    def __init__(self, key: str) -> None:
        self.key = key.encode("utf-8")


class _Lease:
    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self.revoked = False

    def revoke(self) -> None:
        self.revoked = True


class _Kv:
    def __init__(self, key: str) -> None:
        self.key = key.encode("utf-8")


class _WatchResponse:
    """The shape etcd3 hands a prefix-watch callback: a batch of events."""

    def __init__(self, keys: list) -> None:
        self.events = [_Kv(k) for k in keys]


class FakeEtcdClient:
    """The slice of etcd3.client the store uses, backed by a dict."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.leases: dict = {}
        self.watches: dict = {}
        self.cancelled: list = []
        self.closed = False
        self._next_watch_id = 0

    def status(self) -> str:
        return "ok"

    def close(self) -> None:
        self.closed = True

    def get(self, key: str) -> tuple:
        value = self.data.get(key)
        return value, (_Meta(key) if value is not None else None)

    def put(self, key: str, value: bytes, lease=None) -> object:
        self.data[key] = value
        if lease is not None:
            self.leases[key] = lease
        return object()

    def put_if_not_exists(self, key: str, value: bytes, lease=None) -> bool:
        if key in self.data:
            return False
        self.put(key, value, lease)
        return True

    def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None

    def get_all(self) -> list:
        return [(value, _Meta(key)) for key, value in self.data.items()]

    def lease(self, ttl: int) -> _Lease:
        return _Lease(ttl)

    def add_watch_prefix_callback(self, prefix: str, callback) -> int:
        self._next_watch_id += 1
        self.watches[self._next_watch_id] = (prefix, callback)
        return self._next_watch_id

    def cancel_watch(self, watch_id) -> None:
        self.cancelled.append(watch_id)
        self.watches.pop(watch_id, None)

    def emit_prefix_change(self, key: str) -> None:
        for prefix, callback in list(self.watches.values()):
            if key.startswith(prefix):
                callback(_WatchResponse([key]))


@pytest.fixture(autouse=True)
def _fresh_encryption_singleton(monkeypatch) -> None:
    # EncryptionService caches its first instance process-wide; without a
    # reset, whichever test ran first would fix the key for every other one.
    monkeypatch.setattr(EncryptionService, "_instance", None)


@pytest.fixture
def env(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("ETCD_URL", "http://etcd.test:2379")
    for name in ("ETCD_TIMEOUT", "ETCD_USERNAME", "ETCD_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake() -> FakeEtcdClient:
    return FakeEtcdClient()


@pytest.fixture
def etcd_client_factory(fake) -> Iterator[MagicMock]:
    with patch(ETCD3_CLIENT, return_value=fake) as factory:
        yield factory


@pytest.fixture
def store(env, etcd_client_factory) -> Etcd3EncryptedKeyValueStore:
    return Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))


def _raw(fake: FakeEtcdClient, key: str) -> str:
    return fake.data[key].decode("utf-8")


class TestConstruction:
    def test_missing_secret_key_is_refused(self, env, monkeypatch) -> None:
        monkeypatch.delenv("SECRET_KEY")

        with pytest.raises(ValueError, match="SECRET_KEY"):
            Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))

    def test_missing_etcd_url_is_refused(self, env, monkeypatch) -> None:
        monkeypatch.delenv("ETCD_URL")

        with pytest.raises(ValueError, match="ETCD_URL"):
            Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))

    def test_url_scheme_is_dropped_and_host_port_timeout_parsed(
        self, env, monkeypatch, etcd_client_factory
    ) -> None:
        monkeypatch.setenv("ETCD_URL", "https://etcd.internal:12379")
        monkeypatch.setenv("ETCD_TIMEOUT", "2.5")

        store = Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))

        config = store.store.connection_manager.config
        assert config.hosts == ["etcd.internal"]
        assert config.port == 12379
        assert config.timeout == 2.5

    def test_url_without_scheme_is_accepted(self, env, monkeypatch, etcd_client_factory) -> None:
        monkeypatch.setenv("ETCD_URL", "etcd.internal:2379")

        store = Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))

        assert store.store.connection_manager.config.hosts == ["etcd.internal"]

    @pytest.mark.xfail(
        strict=True,
        raises=IndexError,
        reason=(
            "An ETCD_URL with no port, such as http://etcd, crashes start-up with "
            "'list index out of range' instead of using etcd's standard port 2379 as "
            "encrypted_store.py does. Picking a default port is a product decision, "
            "so it is left for the owners."
        ),
    )
    def test_url_without_port_uses_the_etcd_default(
        self, env, monkeypatch, etcd_client_factory
    ) -> None:
        monkeypatch.setenv("ETCD_URL", "http://etcd.internal")

        store = Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))

        assert store.store.connection_manager.config.port == 2379

    async def test_client_is_exposed_once_connected(self, store, fake) -> None:
        assert store.client is None

        await store.get_key("/any")

        assert store.client is fake


class TestSerializers:
    """The byte codecs handed to the inner store."""

    def test_serializer_encodes_none_primitives_and_containers(self, store) -> None:
        serialize = store.store.serializer

        assert serialize(None) == b""
        assert serialize("text") == b'"text"'
        assert serialize(3) == b"3"
        assert serialize(True) == b"true"
        assert json.loads(serialize({"a": [1, 2]})) == {"a": [1, 2]}

    def test_serializer_falls_back_to_str_for_unknown_types(self, store) -> None:
        moment = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)

        assert json.loads(store.store.serializer({"at": moment})) == {"at": str(moment)}

    def test_deserializer_decodes_json_and_plain_text(self, store) -> None:
        deserialize = store.store.deserializer

        assert deserialize(b"") is None
        assert deserialize(b'{"a": 1}') == {"a": 1}
        assert deserialize(b"iv:cipher:tag") == "iv:cipher:tag"

    def test_deserializer_answers_none_for_bytes_that_are_not_utf8(self, store) -> None:
        assert store.store.deserializer(b"\xff\xfe") is None


class TestWriteAndRead:
    async def test_secret_values_are_stored_encrypted_and_read_back(self, store, fake) -> None:
        secret = {"clientId": "id-1", "clientSecret": "not-a-real-secret"}

        assert await store.create_key("/services/connectors/x/config", secret) is True

        stored = _raw(fake, "/services/connectors/x/config")
        assert "not-a-real-secret" not in stored
        assert stored.count(":") == 2
        assert await store.get_key("/services/connectors/x/config") == secret

    async def test_string_values_round_trip_through_encryption(self, store) -> None:
        await store.create_key("/k", "plain words")

        assert await store.get_key("/k") == "plain words"

    async def test_excluded_keys_are_stored_as_plain_json(self, store, fake) -> None:
        endpoints = {"connectors": {"endpoint": "http://connectors:8088"}}

        assert await store.create_key(ENDPOINTS_KEY, endpoints) is True

        assert json.loads(_raw(fake, ENDPOINTS_KEY)) == endpoints
        assert await store.get_key(ENDPOINTS_KEY) == endpoints

    async def test_ttl_attaches_a_lease_to_a_new_key(self, store, fake) -> None:
        await store.create_key("/state/abc", {"v": 1}, ttl=600)

        assert fake.leases["/state/abc"].ttl == 600

    async def test_update_value_overwrites(self, store) -> None:
        await store.create_key("/k", {"v": 1})

        assert await store.update_value("/k", {"v": 2}) is True

        assert await store.get_key("/k") == {"v": 2}

    async def test_missing_key_reads_as_none(self, store) -> None:
        assert await store.get_key("/absent") is None
        assert await store.get_key("/absent", raise_on_error=True) is None

    async def test_a_rejected_write_reports_false(self, store) -> None:
        with patch.object(store.store, "create_key", AsyncMock(return_value=False)):
            assert await store.create_key("/k", {"v": 1}) is False

    async def test_a_write_that_reads_back_different_reports_false(self, store) -> None:
        someone_else = store.encryption_service.encrypt(json.dumps({"v": "other"}))
        with patch.object(store.store, "create_key", AsyncMock(return_value=True)), patch.object(
            store.store, "get_key", AsyncMock(side_effect=[None, someone_else])
        ):
            assert await store.create_key("/k", {"v": "mine"}) is False

    async def test_a_write_while_etcd_is_down_reports_false_and_writes_nothing(
        self, store, fake
    ) -> None:
        with patch.object(fake, "get", side_effect=ConnectionError("etcd down")):
            assert await store.create_key("/k", {"v": 1}) is False

        assert fake.data == {}


class TestUnreadableValues:
    """A value that exists but cannot be read must never look like an empty slot."""

    async def test_value_under_another_secret_key_reads_as_none_by_default(
        self, store
    ) -> None:
        await store.create_key("/k", {"v": 1})
        store.encryption_service = EncryptionService("aes-256-gcm", OTHER_HEX_KEY, logging.getLogger("etcdcov-test"))

        assert await store.get_key("/k") is None

    async def test_value_under_another_secret_key_raises_when_asked(self, store) -> None:
        await store.create_key("/k", {"v": 1})
        store.encryption_service = EncryptionService("aes-256-gcm", OTHER_HEX_KEY, logging.getLogger("etcdcov-test"))

        with pytest.raises(DecryptionError):
            await store.get_key("/k", raise_on_error=True)

    async def test_plaintext_under_an_encrypted_key_is_not_passed_off_as_config(
        self, store, fake
    ) -> None:
        fake.data["/k"] = b'{"v": 1}'

        assert await store.get_key("/k") is None
        with pytest.raises(DecryptionError):
            await store.get_key("/k", raise_on_error=True)

    async def test_bytes_that_are_not_utf8_raise_when_asked(self, store, fake) -> None:
        fake.data["/k"] = b"\xff\xfe"

        assert await store.get_key("/k") is None
        with pytest.raises(ConnectionError):
            await store.get_key("/k", raise_on_error=True)

    async def test_an_undecryptable_value_is_not_replaced_by_a_claim(self, store, fake) -> None:
        fake.data["/k"] = b"aa:bb:cc"

        assert await store.create_key("/k", {"default": True}, overwrite=False) is False

        assert fake.data["/k"] == b"aa:bb:cc"

    async def test_non_utf8_bytes_are_not_replaced_by_a_claim(self, store, fake) -> None:
        fake.data["/k"] = b"\xff\xfe"

        assert await store.create_key("/k", {"default": True}, overwrite=False) is False

        assert fake.data["/k"] == b"\xff\xfe"

    async def test_etcd_outage_reads_as_none_by_default_and_raises_when_asked(
        self, store, fake
    ) -> None:
        with patch.object(fake, "get", side_effect=ConnectionError("etcd down")):
            assert await store.get_key("/k") is None
            with pytest.raises(ConnectionError):
                await store.get_key("/k", raise_on_error=True)


class TestLegacyPlainValues:
    async def test_python_repr_under_an_excluded_key_is_parsed(self, store, fake) -> None:
        fake.data[ENDPOINTS_KEY] = b"{'frontend': {'publicEndpoint': None}, 'on': True}"

        assert await store.get_key(ENDPOINTS_KEY) == {"frontend": {"publicEndpoint": None}, "on": True}

    async def test_free_text_under_an_excluded_key_is_returned_as_is(self, store, fake) -> None:
        fake.data[ENDPOINTS_KEY] = b"not json at all"

        assert await store.get_key(ENDPOINTS_KEY) == "not json at all"

    async def test_json_string_under_an_excluded_key_is_decoded(self, store, fake) -> None:
        fake.data[ENDPOINTS_KEY] = json.dumps(json.dumps({"a": 1})).encode()

        assert await store.get_key(ENDPOINTS_KEY) == {"a": 1}


class TestReconnect:
    async def test_store_recovers_once_etcd_is_reachable_again(self, env, fake) -> None:
        with patch(ETCD3_CLIENT, side_effect=[ConnectionError("etcd down"), fake]):
            store = Etcd3EncryptedKeyValueStore(logging.getLogger("etcdcov-test"))
            fake.data["/k"] = store.encryption_service.encrypt(json.dumps({"v": 1})).encode()

            with pytest.raises(ConnectionError):
                await store.get_key("/k", raise_on_error=True)

            assert await store.get_key("/k", raise_on_error=True) == {"v": 1}


class TestKeyOperations:
    async def test_delete_and_list_all_delegate(self, store, fake) -> None:
        await store.create_key("/a", 1)
        await store.create_key("/b", 2)

        assert sorted(await store.get_all_keys()) == ["/a", "/b"]
        assert await store.delete_key("/a") is True
        assert await store.get_all_keys() == ["/b"]

    async def test_cancel_watch_delegates(self, store, fake) -> None:
        await store.cancel_watch("/k", 7)

        assert fake.cancelled == [7]


class TestListKeysInDirectory:
    async def test_empty_store_lists_nothing(self, store) -> None:
        assert await store.list_keys_in_directory("/services/") == []

    @pytest.mark.parametrize("directory", ["", "/"])
    async def test_root_lists_every_key(self, store, fake, directory) -> None:
        fake.data = {"/a/1": b"x", "/b/2": b"y"}

        assert sorted(await store.list_keys_in_directory(directory)) == ["/a/1", "/b/2"]

    async def test_keys_outside_the_directory_are_left_out(self, store, fake) -> None:
        fake.data = {"/services/toolsets/i1/u1": b"x", "/services/mcp/x": b"y"}

        assert await store.list_keys_in_directory("/services/toolsets") == ["/services/toolsets/i1/u1"]

    async def test_key_names_in_encrypted_form_are_decrypted(self, store, fake) -> None:
        hidden = store.encryption_service.encrypt("/services/toolsets/i1/u1")
        fake.data = {hidden: b"x", "/other": b"y"}

        assert await store.list_keys_in_directory("/services/toolsets") == ["/services/toolsets/i1/u1"]

    async def test_names_that_only_look_encrypted_are_kept_as_is(self, store, fake) -> None:
        fake.data = {"/services/a:b:c": b"x"}

        assert await store.list_keys_in_directory("/services") == ["/services/a:b:c"]

    async def test_excluded_prefixes_are_never_decrypted(self, store, fake) -> None:
        fake.data = {f"{ENDPOINTS_KEY}/a:b:c": b"x"}
        with patch.object(store.encryption_service, "decrypt") as decrypt:
            assert await store.list_keys_in_directory(ENDPOINTS_KEY) == [f"{ENDPOINTS_KEY}/a:b:c"]

        decrypt.assert_not_called()

    async def test_a_key_that_cannot_be_examined_is_skipped(self, store, fake) -> None:
        fake.data = {"aa:bb:cc": b"x", "/fine": b"y"}
        with patch.object(store.encryption_service, "decrypt", return_value=None):
            assert await store.list_keys_in_directory("/fine") == ["/fine"]

    async def test_etcd_outage_is_raised_not_answered_with_an_empty_list(self, store, fake) -> None:
        with patch.object(fake, "get_all", side_effect=ConnectionError("etcd down")):
            with pytest.raises(ConnectionError):
                await store.list_keys_in_directory("/services/")


class TestClose:
    async def test_close_releases_the_etcd_connection(self, store, fake) -> None:
        await store.get_key("/any")

        await store.close()

        assert fake.closed is True
        assert store.store.connection_manager.client is None


class TestChangeNotifications:
    """ConfigurationService drops its cached copy of a key when another
    process changes it, and learns of the change only through these methods."""

    async def test_a_change_made_elsewhere_reaches_the_subscriber(self, store, fake) -> None:
        changed: list = []

        handle = await store.subscribe_changes(changed.append)
        fake.emit_prefix_change("/services/connectors/x/config")

        assert handle is not None
        assert changed == ["/services/connectors/x/config"]

    async def test_unsubscribing_cancels_the_watch(self, store, fake) -> None:
        handle = await store.subscribe_changes(lambda _key: None)

        await store.unsubscribe_changes(handle)

        assert fake.cancelled == [handle]
        assert fake.watches == {}

    async def test_publishing_is_left_to_etcd_itself(self, store) -> None:
        assert await store.publish_change("/k") is None


class TestWatchKey:
    """The inner store hands a watch callback the stored text; for every key
    but the excluded ones, that text is ciphertext."""

    @staticmethod
    async def _watch(store, key: str, error_callback=None) -> tuple:
        received: list = []
        with patch.object(store.store, "watch_key", AsyncMock(return_value=5)) as inner:
            watch_id = await store.watch_key(key, received.append, error_callback)
        wrapped = inner.await_args.args[1]
        assert inner.await_args.args[2] is error_callback
        return watch_id, wrapped, received

    async def test_an_encrypted_value_reaches_the_watcher_decrypted(self, store) -> None:
        watch_id, on_change, received = await self._watch(store, "/k")

        on_change(store.encryption_service.encrypt(json.dumps({"v": 1})))

        assert watch_id == 5
        assert received == [{"v": 1}]

    async def test_a_deletion_reaches_the_watcher_as_none(self, store) -> None:
        _, on_change, received = await self._watch(store, "/k")

        on_change(None)

        assert received == [None]

    async def test_an_excluded_value_reaches_the_watcher_unchanged(self, store) -> None:
        _, on_change, received = await self._watch(store, ENDPOINTS_KEY)

        on_change({"a": 1})

        assert received == [{"a": 1}]

    async def test_an_undecryptable_value_is_raised_not_delivered(self, store) -> None:
        """Raising is what routes it: the inner store passes a callback's
        exception to error_callback."""
        _, on_change, received = await self._watch(store, "/k", error_callback=lambda _e: None)

        with pytest.raises(DecryptionError):
            on_change("aa:bb:cc")

        assert received == []


class TestDirectoryBoundary:
    async def test_a_trailing_slash_keeps_neighbouring_paths_out(self, store, fake) -> None:
        """Callers pass "/services/mcp/credentials/{id}/" and delete every key
        listed; a key for instance "{id}-2", or the bare path itself, is not
        inside that directory."""
        fake.data = {
            "/services/mcp/credentials/i1/u1": b"x",
            "/services/mcp/credentials/i1": b"y",
            "/services/mcp/credentials/i1-2/u1": b"z",
        }

        listed = await store.list_keys_in_directory("/services/mcp/credentials/i1/")

        assert listed == ["/services/mcp/credentials/i1/u1"]
