"""One contract, every KeyValueStore implementation.

`create_key(..., overwrite=False)` is the store's only atomic
"claim this key if nobody has" primitive, and `ConfigurationService.
create_config_if_absent` turns its return value into a decision that cannot be
retried: True means *this* process owns the value, False means read back
whoever does. `strategy_resolver` then either persists its candidate or defers
to the persisted collection layout.

A store that reports True for an existing key makes a node adopt its own
candidate strategy while the deployment runs another — reads and deletes then
target collections that hold no data. A store that raises instead of returning
False turns every restart after the first into a startup failure.

Two implementations had each drifted one of those ways, so the assertions live
here once and run against all of them rather than per-store where a new backend
can quietly skip them.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.key_value_store import KeyValueStore
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService


class _FakeInnerStore:
    """The plaintext store the encrypted wrappers delegate to.

    Honours the same contract, so a wrapper that returns True for an existing
    key is failing on its own account rather than inheriting a broken inner.
    """

    def __init__(self) -> None:
        self._data: dict = {}

    async def get_key(self, key: str):
        return self._data.get(key)

    async def create_key(self, key, value, overwrite=True, ttl=None) -> bool:
        if key in self._data and not overwrite:
            return False
        self._data[key] = value
        return True


class _IdentityEncryption:
    def encrypt(self, value: str) -> str:
        return value

    def decrypt(self, value: str) -> str:
        return value


def _in_memory() -> InMemoryKeyValueStore:
    return InMemoryKeyValueStore(MagicMock())


def _encrypted() -> EncryptedKeyValueStore:
    store = EncryptedKeyValueStore.__new__(EncryptedKeyValueStore)
    store.logger = MagicMock()
    store.store = _FakeInnerStore()
    store.encryption_service = _IdentityEncryption()
    return store


def _etcd3_encrypted() -> Etcd3EncryptedKeyValueStore:
    # Built past __init__: the real one demands SECRET_KEY and ETCD_URL and
    # opens a client. create_key touches only these three attributes.
    store = Etcd3EncryptedKeyValueStore.__new__(Etcd3EncryptedKeyValueStore)
    store.logger = MagicMock()
    store.store = _FakeInnerStore()
    store.encryption_service = _IdentityEncryption()
    return store


ALL_STORES = [
    pytest.param(_in_memory, id="in_memory"),
    pytest.param(_encrypted, id="encrypted"),
    pytest.param(_etcd3_encrypted, id="etcd3_encrypted"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_store", ALL_STORES)
class TestCreateKeyContract:
    async def test_creating_an_absent_key_reports_true(self, make_store) -> None:
        store = make_store()

        assert await store.create_key("/k", "v1", overwrite=False) is True

    async def test_an_existing_key_reports_false(self, make_store) -> None:
        """Not True, and not an exception — the caller distinguishes "I own
        this" from "someone else does" on this value alone."""
        store = make_store()
        await store.create_key("/k", "v1", overwrite=False)

        assert await store.create_key("/k", "v2", overwrite=False) is False

    async def test_an_existing_key_keeps_its_value(self, make_store) -> None:
        """The half that matters on a live deployment: a losing claim must not
        overwrite the persisted collection strategy with its own candidate."""
        store = make_store()
        await store.create_key("/k", "v1", overwrite=False)

        await store.create_key("/k", "v2", overwrite=False)

        assert (await store.get_key("/k")) == "v1"

    async def test_overwrite_true_replaces_and_reports_true(self, make_store) -> None:
        store = make_store()
        await store.create_key("/k", "v1")

        assert await store.create_key("/k", "v2", overwrite=True) is True
        assert (await store.get_key("/k")) == "v2"

    async def test_a_dict_value_round_trips(self, make_store) -> None:
        """Config values are dicts; the encrypted wrappers serialise them, so a
        contract asserted only on strings would miss a broken round trip."""
        store = make_store()
        payload = {"strategy": "per_connector_type", "n": 1}

        assert await store.create_key("/d", payload, overwrite=False) is True
        assert (await store.get_key("/d")) == payload


# compare_and_set: the read-modify-write primitive for values several processes update.

class _FakeEtcdClient:
    """The three etcd3 calls the store's compare_and_set makes, over a dict of raw bytes."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def get(self, key) -> tuple[bytes | None, None]:
        return self.data.get(key), None

    def put_if_not_exists(self, key, value, lease=None) -> bool:
        if key in self.data:
            return False
        self.data[key] = value
        return True

    def replace(self, key, initial_value, new_value) -> bool:
        if self.data.get(key) != initial_value:
            return False
        self.data[key] = new_value
        return True

    def put(self, key, value, lease=None) -> bool:
        self.data[key] = value
        return True


def _redis() -> KeyValueStore:
    from fakeredis import FakeServer

    from app.config.providers.redis import redis_store as redis_store_module
    from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.inprocess_provider import InProcessRedisProvider

    provider = InProcessRedisProvider(RedisConnectionConfig(), server=FakeServer())
    with patch.object(redis_store_module, "get_redis_provider", return_value=provider):
        return RedisDistributedKeyValueStore(
            lambda v: json.dumps(v).encode(), lambda b: json.loads(b), host="x", port=1
        )


def _etcd() -> KeyValueStore:
    from app.config.providers.etcd.etcd3_store import Etcd3DistributedKeyValueStore

    manager = MagicMock()
    manager.get_client = AsyncMock(return_value=_FakeEtcdClient())
    with patch("app.config.providers.etcd.etcd3_store.Etcd3ConnectionManager", return_value=manager):
        store = Etcd3DistributedKeyValueStore(
            lambda v: json.dumps(v).encode(), lambda b: json.loads(b), host="x", port=1
        )
    store.connection_manager.client = manager.get_client.return_value
    return store


def _real_encryption() -> EncryptionService:
    return EncryptionService("aes-256-gcm", hashlib.sha256(b"cas-contract").hexdigest(), MagicMock())


def _encrypted_over(cls) -> Callable[[], KeyValueStore]:
    def make() -> KeyValueStore:
        store = cls.__new__(cls)
        store.logger = MagicMock()
        store.store = _in_memory()
        store.encryption_service = _real_encryption()
        return store
    return make


CAS_STORES = [
    pytest.param(_in_memory, id="in_memory"),
    pytest.param(_redis, id="redis"),
    pytest.param(_etcd, id="etcd"),
    pytest.param(_encrypted_over(EncryptedKeyValueStore), id="encrypted"),
    pytest.param(_encrypted_over(Etcd3EncryptedKeyValueStore), id="etcd3_encrypted"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_store", CAS_STORES)
class TestCompareAndSetContract:
    async def test_none_creates_an_absent_key(self, make_store) -> None:
        store = make_store()

        assert await store.compare_and_set("/k", None, {"n": 1}) is True
        assert await store.get_key("/k") == {"n": 1}

    async def test_none_does_not_overwrite_a_present_key(self, make_store) -> None:
        store = make_store()
        await store.create_key("/k", {"n": 1})

        assert await store.compare_and_set("/k", None, {"n": 2}) is False
        assert await store.get_key("/k") == {"n": 1}

    async def test_the_expected_value_is_replaced(self, make_store) -> None:
        store = make_store()
        await store.create_key("/k", {"n": 1, "accounts": ["a"]})

        assert await store.compare_and_set("/k", {"n": 1, "accounts": ["a"]}, {"n": 2}) is True
        assert await store.get_key("/k") == {"n": 2}

    async def test_a_stale_expected_value_is_refused(self, make_store) -> None:
        store = make_store()
        await store.create_key("/k", {"n": 1})
        await store.create_key("/k", {"n": 2})

        assert await store.compare_and_set("/k", {"n": 1}, {"n": 3}) is False
        assert await store.get_key("/k") == {"n": 2}

    async def test_a_present_expected_value_on_an_absent_key_is_refused(self, make_store) -> None:
        store = make_store()

        assert await store.compare_and_set("/k", {"n": 1}, {"n": 2}) is False
        assert await store.get_key("/k") is None

    async def test_two_racing_writers_from_one_read_have_one_winner(self, make_store) -> None:
        """Both read {"n": 1}, both try to write their own update: one wins, the other must re-read."""
        store = make_store()
        await store.create_key("/k", {"n": 1})

        results = await asyncio.gather(
            store.compare_and_set("/k", {"n": 1}, {"n": "a"}),
            store.compare_and_set("/k", {"n": 1}, {"n": "b"}),
        )

        assert sorted(results) == [False, True]
        assert await store.get_key("/k") == {"n": "a" if results[0] else "b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [EncryptedKeyValueStore, Etcd3EncryptedKeyValueStore])
async def test_the_encrypted_stores_compare_values_not_ciphertexts(cls) -> None:
    """Encrypting the expected value again gives other bytes (a random IV), so a byte compare never matches."""
    store = _encrypted_over(cls)()
    await store.create_key("/k", {"n": 1})
    first = await store.store.get_key("/k")

    assert await store.compare_and_set("/k", {"n": 1}, {"n": 2}) is True
    assert await store.get_key("/k") == {"n": 2}
    assert await store.store.get_key("/k") != first
    assert "n" not in await store.store.get_key("/k")  # still encrypted at rest


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [EncryptedKeyValueStore, Etcd3EncryptedKeyValueStore])
async def test_the_encrypted_stores_raise_on_a_failed_read(cls) -> None:
    store = _encrypted_over(cls)()
    store.store.get_key = AsyncMock(side_effect=ConnectionError("down"))

    with pytest.raises(ConnectionError):
        await store.compare_and_set("/k", None, {"n": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [EncryptedKeyValueStore, Etcd3EncryptedKeyValueStore])
async def test_the_encrypted_stores_raise_on_a_value_they_cannot_decrypt(cls) -> None:
    """Not False and not None-equals-absent: a retry loop would spin, a None compare would overwrite it."""
    store = _encrypted_over(cls)()
    await store.store.create_key("/k", "not-a-ciphertext")

    with pytest.raises(DecryptionError):
        await store.compare_and_set("/k", None, {"n": 1})
    assert await store.store.get_key("/k") == "not-a-ciphertext"


@pytest.mark.asyncio
async def test_redis_writers_that_both_read_before_either_writes_have_one_winner() -> None:
    """The fake server answers each call before the next starts, so the race is forced: both reads, then both swaps."""
    store = _redis()
    await store.create_key("/k", {"n": 1})
    client = store._get_client()
    read, both_read = client.get, asyncio.Barrier(2)

    async def read_then_wait(name) -> bytes | None:
        raw = await read(name)
        await both_read.wait()
        return raw

    client.get = read_then_wait
    results = await asyncio.gather(
        store.compare_and_set("/k", {"n": 1}, {"n": "a"}),
        store.compare_and_set("/k", {"n": 1}, {"n": "b"}),
    )
    client.get = read

    assert sorted(results) == [False, True]
    assert await store.get_key("/k") == {"n": "a" if results[0] else "b"}


@pytest.mark.asyncio
async def test_redis_raises_when_the_server_fails() -> None:
    store = _redis()
    client = AsyncMock()
    client.get.return_value = b'{"n": 1}'
    client.eval.side_effect = OSError("connection reset")
    store._get_client = MagicMock(return_value=client)

    with pytest.raises(ConnectionError):
        await store.compare_and_set("/k", {"n": 1}, {"n": 2})


@pytest.mark.asyncio
async def test_redis_raises_on_bytes_it_cannot_decode() -> None:
    store = _redis()
    await store._get_client().set(store._build_key("/k"), b"\xff\xfe")
    store.deserializer = lambda b: None  # the factory deserializer's answer for bytes that are not UTF-8

    with pytest.raises(ValueError, match="could not be decoded"):
        await store.compare_and_set("/k", None, {"n": 1})


@pytest.mark.asyncio
async def test_etcd_raises_when_the_server_fails() -> None:
    store = _etcd()
    store.connection_manager.client.get = MagicMock(side_effect=OSError("unavailable"))

    with pytest.raises(ConnectionError):
        await store.compare_and_set("/k", None, {"n": 1})


@pytest.mark.asyncio
async def test_the_in_memory_config_stand_in_follows_the_same_contract() -> None:
    """Processor tests run on it; a stand-in that always wins would hide a lost update."""
    from tests.support.in_memory_config import InMemoryConfigService

    svc = InMemoryConfigService()

    assert await svc.compare_and_set_config("/k", None, {"n": 1}) is True
    assert await svc.compare_and_set_config("/k", None, {"n": 2}) is False
    assert await svc.compare_and_set_config("/k", {"n": 0}, {"n": 2}) is False
    assert await svc.compare_and_set_config("/k", {"n": 1}, {"n": 2}) is True
    assert await svc.get_config("/k") == {"n": 2}
