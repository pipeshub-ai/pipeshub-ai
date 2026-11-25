"""P2.4a/P2.4b VA: the vault, and the credential and model-account contracts on the PipesHub adapters."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.credentials.accounts import VaultModelAccountStore
from app.harness.credentials.service import VAULT_KIND, pipeshub_credential_service
from app.harness.credentials.vault import (
    KEYRING_PATH,
    VaultSecretStore,
    vault_path,
)
from app.utils.encryption.encryption_service import DecryptionError, EncryptionService
from harness_core.credentials import (
    CredentialPrincipal,
    CredentialPurpose,
    CredentialRevoked,
    CredentialScope,
    CredentialUnavailable,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.credentials import (
    CredentialServiceContract,
    SecretSourceContract,
)
from harness_core.testing.contracts.model_accounts import ModelAccountStoreContract
from harness_core.testing.ids import SeededIds
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.store_outage import DownStore, outage_configuration_service

if TYPE_CHECKING:
    from pydantic import JsonValue

SECRET = "vault-secret-3c1e9a77b04f"
TENANT = "org-1"
SCOPE = CredentialScope(VAULT_KIND, "user-1/openai")
PURPOSE = CredentialPurpose(TENANT, "model:key-1", "run-1", "run-1-attempt-1")
PRINCIPAL = CredentialPrincipal(TENANT, "user-1")


@dataclass
class _Wired:
    config: DictConfig = field(default_factory=DictConfig)
    clock: ManualClock = field(default_factory=ManualClock)
    kind: str = VAULT_KIND
    keeps_history: bool = True
    partitions_tenants: bool = True

    def __post_init__(self) -> None:
        self.service = pipeshub_credential_service(self.config, clock=self.clock, ids=SeededIds())
        self.source = VaultSecretStore(self.config, self.clock)

    async def forget(self, tenant: str, scope: CredentialScope) -> None:
        await self.source.delete(tenant, scope)

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
        _ = await self.source.write(tenant, scope, material)


class TestPipesHubCredentialServiceContract(CredentialServiceContract):
    @pytest.fixture
    def credential_harness(self) -> _Wired:
        return _Wired()


class TestVaultSecretStoreContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _Wired:
        return _Wired()


def _cipher(key_hex: str) -> EncryptionService:
    return EncryptionService("aes-256-gcm", key_hex, logging.getLogger("t"))


async def test_va1_nothing_stored_is_plaintext_and_the_platform_cipher_opens_it() -> None:
    w = _Wired()
    version = await w.source.write(TENANT, SCOPE, {"apiKey": SECRET})
    assert SECRET not in json.dumps(w.config.values)
    document = w.config.values[vault_path(TENANT, SCOPE.name)]
    (entry,) = document["versions"]
    ring = w.config.values[KEYRING_PATH]
    assert entry["keyVersion"] == ring["active"] == "1" and entry["version"] == version
    assert json.loads(_cipher(ring["keys"]["1"]).decrypt(entry["ciphertext"])) == {"apiKey": SECRET}


async def test_va2_key_rotation_reencrypts_everything_and_drops_the_old_key() -> None:
    w = _Wired()
    other = CredentialScope(VAULT_KIND, "user-2/anthropic")
    _ = await w.service.rotate(TENANT, SCOPE, SECRET)
    _ = await w.service.rotate("org-2", other, "second-secret-77")
    in_flight = await w.service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=300)
    old_key = w.config.values[KEYRING_PATH]["keys"]["1"]

    assert await w.source.rotate_key() == 2

    ring = w.config.values[KEYRING_PATH]
    assert ring["active"] == "2" and set(ring["keys"]) == {"2"}
    for path in (vault_path(TENANT, SCOPE.name), vault_path("org-2", other.name)):
        (entry,) = w.config.values[path]["versions"]
        assert entry["keyVersion"] == "2"
        with pytest.raises(DecryptionError):
            _ = _cipher(old_key).decrypt(entry["ciphertext"])
    assert (await w.service.resolve(in_flight.handle, PURPOSE)).material == SECRET
    fresh = await w.service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=60)
    assert (await w.service.resolve(fresh.handle, PURPOSE)).material == SECRET


async def test_va3_the_keyring_is_provisioned_once_and_shared() -> None:
    config, clock = DictConfig(), ManualClock()
    _ = await VaultSecretStore(config, clock).write(TENANT, SCOPE, SECRET)
    _ = await VaultSecretStore(config, clock).write(TENANT, CredentialScope(VAULT_KIND, "u/b"), SECRET)
    assert config.writes.count(KEYRING_PATH) == 1
    found = await VaultSecretStore(config, clock).read(TENANT, SCOPE)
    assert found is not None and found.material == SECRET


async def test_va4_a_document_under_a_missing_key_is_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    w = _Wired()
    _ = await w.service.rotate(TENANT, SCOPE, SECRET)
    issued = await w.service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=300)
    w.config.values[vault_path(TENANT, SCOPE.name)]["versions"][0]["keyVersion"] = "9"
    with pytest.raises(CredentialUnavailable):
        _ = await w.service.resolve(issued.handle, PURPOSE)
    unreadable = [r for r in caplog.records if getattr(r, "harness_event", None) == "credential.vault_unreadable"]
    assert len(unreadable) == 1 and unreadable[0].harness["reason"] == "key_version_missing"  # type: ignore[attr-defined]


async def test_va5_logs_carry_handles_never_material(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = _Wired()
    _ = await w.service.rotate(TENANT, SCOPE, SECRET)
    issued = await w.service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=300)
    _ = await w.service.resolve(issued.handle, PURPOSE)
    await w.service.revoke(issued.handle)
    _ = await w.source.rotate_key()
    text = "\n".join(f"{r.getMessage()} {getattr(r, 'harness', '')}" for r in caplog.records)
    assert issued.handle in text
    assert SECRET not in text
    for key in w.config.values[KEYRING_PATH]["keys"].values():
        assert key not in text


@pytest.mark.parametrize("name", ["../x", "a/../b", "a b", ""])
def test_vault_names_are_path_safe(name: str) -> None:
    with pytest.raises(ValueError):
        _ = vault_path(TENANT, name)


@dataclass
class _Accounts:
    config: DictConfig = field(default_factory=DictConfig)
    clock: ManualClock = field(default_factory=ManualClock)
    kind: str = VAULT_KIND

    def __post_init__(self) -> None:
        self.secrets = VaultSecretStore(self.config, self.clock)
        self.store = VaultModelAccountStore(self.secrets, self.clock)


class TestVaultModelAccountStoreContract(ModelAccountStoreContract):
    @pytest.fixture
    def account_harness(self) -> _Accounts:
        return _Accounts()


class _ReadsDownWritesUp(DownStore):
    """A store whose reads fail while its writes land: the outage that strands a keyring."""

    def __init__(self) -> None:
        self.written: list[str] = []

    async def create_key(self, key: str, value: object, overwrite: bool = True, ttl: int | None = None) -> bool:
        self.written.append(key)
        return True

    async def publish_change(self, key: str) -> None:
        return None


async def test_va6_a_store_outage_fails_closed_and_never_provisions_or_overwrites(caplog: pytest.LogCaptureFixture) -> None:
    config = outage_configuration_service()
    store = _ReadsDownWritesUp()
    config.store = store  # pyright: ignore[reportAttributeAccessIssue]
    vault = VaultSecretStore(config, ManualClock())
    with pytest.raises(CredentialUnavailable):
        _ = await vault.read(TENANT, SCOPE)
    with pytest.raises(CredentialUnavailable):
        _ = await vault.write(TENANT, SCOPE, SECRET)
    with pytest.raises(CredentialUnavailable):
        _ = await vault.revoke(TENANT, SCOPE.name, by="user-1")
    assert store.written == []
    reasons = [
        r.harness["reason"]  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "credential.vault_unreadable"
    ]
    assert reasons == ["store_unavailable"] * 3


async def test_va6_the_wired_service_refuses_on_an_outage() -> None:
    service = pipeshub_credential_service(outage_configuration_service(), clock=ManualClock(), ids=SeededIds())
    with pytest.raises(CredentialUnavailable):
        _ = await service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=60)


async def test_va7_writes_and_key_rotation_keep_the_documents_other_fields() -> None:
    w = _Wired()
    meta = {"account": {"provider": "openAI", "last4": "WXYZ"}}
    _ = await w.source.write(TENANT, SCOPE, SECRET, fields=meta)
    _ = await w.source.write(TENANT, SCOPE, "second-secret-0000")
    revoked = CredentialScope(VAULT_KIND, "users/u2/openAI")
    _ = await w.source.write(TENANT, revoked, SECRET)
    assert await w.source.revoke(TENANT, revoked.name, by="u2") is True
    _ = await w.source.rotate_key()
    document = w.config.values[vault_path(TENANT, SCOPE.name)]
    assert document["account"] == meta["account"] and document["lastVersion"] == 2
    tombstone = w.config.values[vault_path(TENANT, revoked.name)]
    assert tombstone["revoked"]["by"] == "u2" and tombstone["versions"] == [] and tombstone["lastVersion"] == 1


async def test_va8_a_revoked_document_reads_revoked_and_numbering_continues() -> None:
    w = _Wired()
    first = await w.source.write(TENANT, SCOPE, SECRET)
    assert await w.source.revoke(TENANT, SCOPE.name, by="user-1") is True
    assert await w.source.revoke(TENANT, SCOPE.name, by="user-1") is False
    for version in (None, first):
        with pytest.raises(CredentialRevoked):
            _ = await w.source.read(TENANT, SCOPE, version)
    again = await w.source.write(TENANT, SCOPE, "second-secret-0000")
    assert int(again) == int(first) + 1
    assert await w.source.read(TENANT, SCOPE, first) is None
    current = await w.source.read(TENANT, SCOPE)
    assert current is not None and current.material == "second-secret-0000"


async def test_vault_erase_is_prefix_safe() -> None:
    w = _Wired()
    for name in ("users/u1/openAI", "users/u10/openAI", "cloud/acct-1"):
        _ = await w.source.write(TENANT, CredentialScope(VAULT_KIND, name), SECRET)
    _ = await w.source.write("org-2", CredentialScope(VAULT_KIND, "users/u1/openAI"), SECRET)
    assert await w.source.erase(TENANT, "users/u1") == 1
    assert await w.source.names(TENANT, "users") == ["users/u10/openAI"]
    assert await w.source.erase(TENANT) == 2
    assert await w.source.names("org-2", "users") == ["users/u1/openAI"]


@dataclass
class _DeleteRefused(DictConfig):
    """``delete_config`` answers False as ConfigurationService does, for a key already gone or a failed delete."""

    fails: bool = False
    raced: bool = False

    async def delete_config(self, key: str) -> bool:
        if self.raced:
            _ = await super().delete_config(key)
        return False


async def test_vault_erase_passes_a_secret_a_concurrent_erase_removed_and_fails_one_still_there() -> None:
    raced = _DeleteRefused(raced=True)
    w = _Wired(config=raced)
    _ = await w.source.write(TENANT, SCOPE, SECRET)
    assert await w.source.erase(TENANT) == 1
    assert await w.source.read(TENANT, SCOPE) is None

    refused = _DeleteRefused(fails=True)
    w = _Wired(config=refused)
    _ = await w.source.write(TENANT, SCOPE, SECRET)
    with pytest.raises(RuntimeError, match="could not erase"):
        _ = await w.source.erase(TENANT)


@dataclass
class _ReadsDown(DictConfig):
    """Reads fail as ConfigurationService reports them (``None`` unless asked to raise); writes still land."""

    down: bool = False

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **kwargs: Any) -> Any:  # noqa: ANN401
        if self.down:
            if kwargs.get("raise_on_error"):
                raise ConnectionError("kv down")
            return default
        return await super().get_config(key, default, use_cache)


async def test_fm_g1_a_store_outage_is_refused_never_read_as_an_empty_vault() -> None:
    source = VaultSecretStore(outage_configuration_service(), ManualClock())  # pyright: ignore[reportArgumentType]
    with pytest.raises(CredentialUnavailable):
        _ = await source.read(TENANT, SCOPE)
    with pytest.raises(CredentialUnavailable):
        _ = await source.write(TENANT, SCOPE, SECRET)


async def test_fm_g1_a_write_or_rotation_during_a_read_outage_keeps_the_history() -> None:
    config, clock = _ReadsDown(), ManualClock()
    source = VaultSecretStore(config, clock)
    assert await source.write(TENANT, SCOPE, SECRET) == "1"
    before = json.dumps(config.values, sort_keys=True)
    config.down = True
    with pytest.raises(CredentialUnavailable):
        _ = await source.write(TENANT, SCOPE, "second-secret")
    with pytest.raises(CredentialUnavailable):
        _ = await source.rotate_key()
    config.down = False
    assert json.dumps(config.values, sort_keys=True) == before
    found = await source.read(TENANT, SCOPE)
    assert found is not None and found.material == SECRET
