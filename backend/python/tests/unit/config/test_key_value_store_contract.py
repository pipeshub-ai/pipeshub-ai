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

from unittest.mock import MagicMock

import pytest

from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.providers.in_memory_store import InMemoryKeyValueStore


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
