"""P2.4b MS on the in-memory ``ModelAccountStore``; CS-14/CS-15: a source's refusals through the service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.access.accounts import InMemoryModelAccountStore
from harness_core.access.types import AccountKind, AccountRef
from harness_core.credentials import (
    CredentialPrincipal,
    CredentialPurpose,
    CredentialRevoked,
    CredentialScope,
    HandleCredentialService,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.credentials import SecretSourceContract
from harness_core.testing.contracts.model_accounts import (
    ALICE,
    KEY,
    ROTATED,
    TENANT,
    ModelAccountStoreContract,
)
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from pydantic import JsonValue

KIND = "account"
PURPOSE = CredentialPurpose(TENANT, f"model:{ALICE.account_id}", "run-1")
PRINCIPAL = CredentialPrincipal(TENANT, "user-a")
SCOPE = CredentialScope(KIND, ALICE.account_id)


@dataclass
class _Harness:
    clock: ManualClock = field(default_factory=ManualClock)
    kind: str = KIND

    def __post_init__(self) -> None:
        self.store = InMemoryModelAccountStore(self.clock)
        self.secrets = self.store


class TestInMemoryModelAccountStoreContract(ModelAccountStoreContract):
    @pytest.fixture
    def account_harness(self) -> _Harness:
        return _Harness()


async def _put(h: _Harness, key: str = KEY) -> None:
    _ = await h.store.put(TENANT, ALICE, "openAI", {"apiKey": key}, secret_hint=key, label=None, actor="user-a")


def _refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.harness["reason"]  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "credential.refused"
    ]


async def test_cs14_a_source_revocation_revokes_the_handle_and_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    h = _Harness()
    await _put(h)
    service = HandleCredentialService({KIND: h.store}, h.clock, ids=SeededIds())
    handle = (await service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=600)).handle
    assert (await service.resolve(handle, PURPOSE)).material == {"apiKey": KEY}
    _ = await h.store.remove(TENANT, ALICE, actor="user-a")
    caplog.set_level(logging.WARNING, logger="harness_core.credentials")
    with pytest.raises(CredentialRevoked):
        _ = await service.resolve(handle, PURPOSE)
    assert _refusals(caplog) == ["credential_revoked"]
    # The handle stays revoked when the account comes back under the same name.
    await _put(h, ROTATED)
    with pytest.raises(CredentialRevoked):
        _ = await service.resolve(handle, PURPOSE)


async def test_cs15_a_source_refusal_on_issue_is_that_refusal_and_no_handle(caplog: pytest.LogCaptureFixture) -> None:
    h = _Harness()
    await _put(h)
    _ = await h.store.remove(TENANT, ALICE, actor="user-a")
    service = HandleCredentialService({KIND: h.store}, h.clock, ids=SeededIds())
    caplog.set_level(logging.WARNING, logger="harness_core.credentials")
    with pytest.raises(CredentialRevoked):
        _ = await service.issue(PURPOSE, PRINCIPAL, SCOPE, ttl_s=600)
    assert _refusals(caplog) == ["credential_revoked"]


@dataclass
class _AccountSecrets:
    """The store as the ``SecretSource`` of its accounts; an account's owner writes it with ``put`` (QA19-9)."""

    clock: ManualClock = field(default_factory=ManualClock)
    kind: str = KIND
    keeps_history: bool = True
    partitions_tenants: bool = True

    def __post_init__(self) -> None:
        self.source = InMemoryModelAccountStore(self.clock)

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
        held = material if isinstance(material, dict) else {"apiKey": material}
        ref = AccountRef(AccountKind.ORG_CLOUD, scope.name)
        _ = await self.source.put(tenant, ref, "openAI", held, secret_hint="hint", label=None, actor="admin-1")


class TestInMemoryModelAccountStoreAsSecretSource(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _AccountSecrets:
        return _AccountSecrets()
