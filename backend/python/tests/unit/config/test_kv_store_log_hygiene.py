"""No KV store or ConfigurationService path writes a stored value to a log.

Every store runs at DEBUG through its success and failure paths with a value
that carries a sentinel secret. The loggers involved come from create_logger
(propagate=False), so a handler is attached to each of them directly; caplog
on the root logger would see nothing and pass vacuously.
"""

import contextlib
import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService
from tests.unit.config.test_configuration_service import _build_service
from tests.unit.config.test_encrypted_store_behaviour import (
    ETCD3_CLIENT,
    REDIS_PREFIX,
    REDIS_PROVIDER,
    SECRET_KEY,
    FakeEtcdClient,
    FakeRedisProvider,
    FakeRedisServer,
)

SENTINEL = "SENTINEL-SECRET-7f3a"
OTHER_HEX_KEY = "33" * 32
SECRET_PATH = "/services/connectors/c-1/config"
PLAIN_PATH = config_node_constants.ENDPOINTS.value
VALUE = {"auth": {"password": SENTINEL, "apiKey": f"{SENTINEL}-key"}, "note": SENTINEL}

STORE_LOGGER = logging.getLogger("kv-hygiene-test")
# Module loggers of the stores and what they build on, all from create_logger.
MODULE_LOGGERS = ("etcd", "redis_store", "Encryption Service", "KV Store:redis", "KV Store:etcd")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self._formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        # Formatted with tracebacks: logger.exception() output counts too.
        return "\n".join(self._formatter.format(r) for r in self.records)


@pytest.fixture
def captured() -> Iterator[_Capture]:
    handler = _Capture()
    loggers = [STORE_LOGGER, *(logging.getLogger(name) for name in MODULE_LOGGERS)]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
    yield handler
    for lg, level in zip(loggers, levels):
        lg.removeHandler(handler)
        lg.setLevel(level)


@pytest.fixture(autouse=True)
def _env(monkeypatch) -> None:
    monkeypatch.setattr(EncryptionService, "_instance", None)
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("REDIS_HOST", "redis.test")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_DB", "0")
    monkeypatch.setenv("REDIS_KV_PREFIX", REDIS_PREFIX)
    monkeypatch.setenv("ETCD_URL", "http://etcd.test:2379")
    for name in ("REDIS_PASSWORD", "REDIS_TIMEOUT", "ETCD_TIMEOUT", "ETCD_USERNAME", "ETCD_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def _assert_clean(captured: _Capture, *keys: str) -> None:
    text = captured.text
    assert captured.records, "nothing was logged; the test would pass vacuously"
    assert SENTINEL not in text
    for key in keys:
        assert key in text


async def _exercise_encrypting_store(store, backend, raw_key) -> None:
    """Success and failure paths of a store that encrypts (Encrypted*Store)."""
    for path in (SECRET_PATH, PLAIN_PATH):
        assert await store.create_key(path, VALUE) is True
        await store.update_value(path, {**VALUE, "v": 2})
        assert (await store.get_key(path))["note"] == SENTINEL

    # Not JSON-serializable: json.dumps fails inside create_key, which one
    # store raises and the other answers with False.
    with contextlib.suppress(TypeError):
        assert await store.create_key("/services/bad", {"password": SENTINEL, "s": {SENTINEL}}) is False

    # The stored copy reads back different: verification fails.
    with patch.object(store.store, "get_key", AsyncMock(return_value=json.dumps({"x": SENTINEL}))):
        await store.create_key(PLAIN_PATH, VALUE)

    # Plaintext that decrypts to something that is not JSON.
    backend.data[raw_key(SECRET_PATH)] = json.dumps(store.encryption_service.encrypt(f"{SENTINEL} not json{{")).encode()
    assert await store.get_key(SECRET_PATH) is None

    # Encrypted under another secret: decryption fails.
    await store.create_key(SECRET_PATH, VALUE)
    real = store.encryption_service
    store.encryption_service = EncryptionService("aes-256-gcm", OTHER_HEX_KEY, STORE_LOGGER)
    assert await store.get_key(SECRET_PATH) is None
    with pytest.raises(DecryptionError):
        await store.get_key(SECRET_PATH, raise_on_error=True)
    store.encryption_service = real

    # The backend is down.
    backend.down = True
    assert await store.get_key(SECRET_PATH) is None
    with contextlib.suppress(ConnectionError):
        await store.create_key(SECRET_PATH, VALUE)
    backend.down = False


@pytest.mark.parametrize("kind", ["redis", "etcd"])
async def test_encrypted_store(kind, captured, monkeypatch) -> None:
    monkeypatch.setenv("KV_STORE_TYPE", kind)
    if kind == "redis":
        backend = FakeRedisServer()
        with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(backend)):
            store = EncryptedKeyValueStore(STORE_LOGGER)
        raw_key = lambda k: f"{REDIS_PREFIX}{k}"  # noqa: E731
    else:
        backend = FakeEtcdClient()
        etcd_patch = patch(ETCD3_CLIENT, return_value=backend)
        etcd_patch.start()
        store = EncryptedKeyValueStore(STORE_LOGGER)
        raw_key = lambda k: k  # noqa: E731
    try:
        await _exercise_encrypting_store(store, backend, raw_key)
    finally:
        if kind == "etcd":
            etcd_patch.stop()
    _assert_clean(captured, SECRET_PATH, PLAIN_PATH)


async def test_etcd3_encrypted_store(captured) -> None:
    backend = FakeEtcdClient()
    with patch(ETCD3_CLIENT, return_value=backend):
        store = Etcd3EncryptedKeyValueStore(STORE_LOGGER)
        await _exercise_encrypting_store(store, backend, lambda k: k)
    _assert_clean(captured, SECRET_PATH, PLAIN_PATH)


async def test_redis_store(captured) -> None:
    server = FakeRedisServer()
    with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(server)):
        store = RedisDistributedKeyValueStore(
            serializer=lambda v: json.dumps(v).encode("utf-8"),
            deserializer=lambda b: json.loads(b.decode("utf-8")),
            host="redis.test",
            port=6379,
            password=None,
            db=0,
            key_prefix=REDIS_PREFIX,
        )
        assert await store.create_key(SECRET_PATH, VALUE) is True
        await store.update_value(SECRET_PATH, {**VALUE, "v": 2})
        assert (await store.get_key(SECRET_PATH))["note"] == SENTINEL
        server.data[f"{REDIS_PREFIX}{SECRET_PATH}"] = f"{SENTINEL} not json{{".encode()
        assert await store.get_key(SECRET_PATH) is None
        server.down = True
        for call in (store.get_key(SECRET_PATH), store.create_key(SECRET_PATH, VALUE)):
            with contextlib.suppress(ConnectionError):
                await call
    _assert_clean(captured, SECRET_PATH)


async def test_in_memory_store(captured) -> None:
    store = InMemoryKeyValueStore(STORE_LOGGER)
    seen = []
    await store.watch_key(SECRET_PATH, seen.append)
    assert await store.create_key(SECRET_PATH, VALUE, ttl=60) is True
    await store.update_value(SECRET_PATH, {**VALUE, "v": 2})
    assert (await store.get_key(SECRET_PATH))["note"] == SENTINEL
    with pytest.raises(KeyError):
        await store.update_value("/services/absent", VALUE)
    assert seen
    _assert_clean(captured, SECRET_PATH)


async def test_configuration_service(captured, monkeypatch) -> None:
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    server = FakeRedisServer()
    with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(server)):
        store = EncryptedKeyValueStore(STORE_LOGGER)
    svc = _build_service(store=store)
    svc.logger = STORE_LOGGER

    assert await svc.set_config(SECRET_PATH, VALUE) is True
    assert await svc.update_config(SECRET_PATH, {**VALUE, "v": 2}) is True
    assert (await svc.get_config(SECRET_PATH, use_cache=False))["note"] == SENTINEL
    assert (await svc.get_config(SECRET_PATH))["note"] == SENTINEL
    await svc.create_config_if_absent(SECRET_PATH, VALUE)

    server.down = True
    assert await svc.get_config(SECRET_PATH, use_cache=False) is None
    assert await svc.set_config(SECRET_PATH, VALUE) is False
    assert await svc.update_config(SECRET_PATH, VALUE) is False
    with patch.object(store, "create_key", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await svc.set_config(SECRET_PATH, VALUE) is False
    _assert_clean(captured, SECRET_PATH)
