"""A-11, cross-language half: the worker reads the credential document Node wrote.

``contracts/harness/vectors/worker-credential.json`` holds the raw Redis value
Node's ``HarnessAuthority.provision`` stored (Node's ``EncryptionService``
through ``KeyValueStoreService``). The Node test of the same vector checks it
is Node's format at Node's path; this one reads it through the production
Python chain: Redis bytes -> ``EncryptedKeyValueStore`` -> ``ConfigurationService``
-> ``ConfiguredWorkerCredential``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.harness.journal_node.credentials import (
    STALE_LIMIT_S,
    ConfiguredWorkerCredential,
    WorkerCredentialUnavailable,
)
from app.utils.encryption.encryption_service import EncryptionService
from tests.unit.config.test_encrypted_store_behaviour import (
    REDIS_PROVIDER,
    FakeRedisProvider,
    FakeRedisServer,
)

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "worker-credential.json").read_text()
)
if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOGGER = logging.getLogger("worker-credential-vector")


@pytest.fixture
def server() -> FakeRedisServer:
    return FakeRedisServer()


@pytest.fixture
async def config_service(monkeypatch, server: FakeRedisServer) -> AsyncIterator[ConfigurationService]:
    monkeypatch.setattr(EncryptionService, "_instance", None)
    monkeypatch.setenv("SECRET_KEY", VECTOR["secretKey"])
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    monkeypatch.setenv("REDIS_KV_PREFIX", VECTOR["redisKeyPrefix"])
    for name in ("REDIS_PASSWORD", "REDIS_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    server.data[VECTOR["redisKeyPrefix"] + VECTOR["path"]] = VECTOR["stored"].encode("utf-8")
    with patch(REDIS_PROVIDER, return_value=FakeRedisProvider(server)):
        store = EncryptedKeyValueStore(LOGGER)
    service = ConfigurationService(LOGGER, store)
    yield service
    await service.close()


def test_python_reads_the_credential_at_the_path_node_writes() -> None:
    assert config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value == VECTOR["path"]


async def test_configuration_service_decrypts_the_document_node_wrote(config_service) -> None:
    document = await config_service.get_config(VECTOR["path"])

    assert document == VECTOR["document"]


async def test_the_worker_presents_and_accepts_node_s_credential(config_service) -> None:
    credential = ConfiguredWorkerCredential(config_service)

    assert await credential.worker_credential() == VECTOR["document"]["current"]
    assert await credential.accepts(VECTOR["document"]["current"]) is True
    assert await credential.accepts("phw_not-the-credential") is False


async def test_a_different_secret_does_not_yield_a_credential(monkeypatch, config_service) -> None:
    config_service.store.encryption_service = EncryptionService("aes-256-gcm", "22" * 32, LOGGER)

    assert await config_service.get_config(VECTOR["path"]) is None


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_qa17_w1_a_redis_outage_serves_the_credential_last_read(config_service, server) -> None:
    clock = _Clock()
    credential = ConfiguredWorkerCredential(config_service, clock=clock)
    current = VECTOR["document"]["current"]
    assert await credential.worker_credential() == current

    server.down = True
    clock.now = STALE_LIMIT_S

    assert await credential.worker_credential() == current
    assert await credential.accepts(current) is True


async def test_qa17_w2_past_the_stale_limit_the_outage_refuses(config_service, server) -> None:
    clock = _Clock()
    credential = ConfiguredWorkerCredential(config_service, clock=clock)
    _ = await credential.worker_credential()

    server.down = True
    clock.now = STALE_LIMIT_S + 1

    with pytest.raises(WorkerCredentialUnavailable):
        _ = await credential.worker_credential()
    assert await credential.accepts(VECTOR["document"]["current"]) is False


async def test_qa17_w3_a_credential_removed_is_refused_at_once(config_service, server) -> None:
    credential = ConfiguredWorkerCredential(config_service, clock=_Clock())
    _ = await credential.worker_credential()

    server.data.clear()

    with pytest.raises(WorkerCredentialUnavailable):
        _ = await credential.worker_credential()


async def test_qa17_w4_never_read_and_unreachable_refuses(config_service, server) -> None:
    server.down = True

    with pytest.raises(WorkerCredentialUnavailable):
        _ = await ConfiguredWorkerCredential(config_service, clock=_Clock()).worker_credential()
