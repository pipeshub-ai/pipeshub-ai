"""The encrypted key-value store asks the edition's cipher (app.edition_services.kv_cipher) for every value, with the value's key;
the default one is the platform cipher, so what it writes is what the store always wrote."""
import logging
from unittest.mock import patch

import pytest

from app import edition_services
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.utils.encryption.encryption_service import EncryptionService

from .test_encrypted_store_behaviour import (  # noqa: F401
    REDIS_PROVIDER,
    FakeRedisProvider,
    FakeRedisServer,
    env,
)

KEY = "/services/connectors/x/config"


class Tagging:
    """A cipher of another edition: tags each value with its key."""

    def __init__(self, platform) -> None:
        self.platform, self.seen = platform, []

    def encrypt(self, key: str, text: str) -> str:
        self.seen.append(("encrypt", key))
        return "t1:" + self.platform().encrypt(key + "|" + text)

    def decrypt(self, key: str, stored: str) -> str:
        self.seen.append(("decrypt", key))
        assert stored.startswith("t1:")
        k, _, text = self.platform().decrypt(stored[3:]).partition("|")
        assert k == key
        return text


@pytest.fixture(autouse=True)
def _fresh(monkeypatch) -> None:
    monkeypatch.setattr(EncryptionService, "_instance", None)


def _store(monkeypatch, server) -> EncryptedKeyValueStore:
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(server)):
        return EncryptedKeyValueStore(logging.getLogger("kv-cipher-test"))


async def test_the_default_cipher_is_the_platform_cipher(env, monkeypatch) -> None:  # noqa: F811
    server = FakeRedisServer()
    store = _store(monkeypatch, server)
    assert await store.create_key(KEY, {"secret": "s"}) is True
    stored = store.store  # the raw value is the platform cipher's iv:ciphertext:tag, as before
    raw = await stored.get_key(KEY)
    assert raw.count(":") == 2 and store.encryption_service.decrypt(raw) == '{"secret": "s"}'
    assert await store.get_key(KEY) == {"secret": "s"}


async def test_an_editions_cipher_is_given_each_values_key_both_ways(env, monkeypatch) -> None:  # noqa: F811
    made = []
    monkeypatch.setattr(edition_services, "kv_cipher", lambda platform: made.append(Tagging(platform)) or made[-1])
    store = _store(monkeypatch, FakeRedisServer())
    assert await store.create_key(KEY, {"secret": "s"}) is True
    assert (await store.store.get_key(KEY)).startswith("t1:")
    assert await store.get_key(KEY) == {"secret": "s"}
    assert ("encrypt", KEY) in made[0].seen and ("decrypt", KEY) in made[0].seen
