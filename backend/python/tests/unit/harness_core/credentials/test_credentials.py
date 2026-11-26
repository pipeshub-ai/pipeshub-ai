"""P2.4a CS, SS, LS: the credential contracts on the in-memory adapters, the leases, refusal logging."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

import pytest

from harness_core.credentials import (
    CredentialLeases,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialReadOnly,
    CredentialRevoked,
    CredentialScope,
    HandleCredentialService,
    InMemorySecretSource,
    Secret,
    ShortLivedCredential,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.credentials import (
    MATERIAL,
    PRINCIPAL,
    PURPOSE,
    TENANT,
    CredentialServiceContract,
    SecretSourceContract,
)
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from pydantic import JsonValue


@dataclass
class _Harness:
    clock: ManualClock = field(default_factory=ManualClock)
    kind: str = "secret"
    keeps_history: bool = True
    partitions_tenants: bool = True

    def __post_init__(self) -> None:
        self.memory = InMemorySecretSource(self.clock)
        self.service = HandleCredentialService({self.kind: self.memory}, self.clock, ids=SeededIds())
        self.source = self.memory

    async def forget(self, tenant: str, scope: CredentialScope) -> None:
        self.memory.delete(tenant, scope)

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
        _ = await self.source.write(tenant, scope, material)


class TestHandleCredentialServiceContract(CredentialServiceContract):
    @pytest.fixture
    def credential_harness(self) -> _Harness:
        return _Harness()


class TestInMemorySecretSourceContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _Harness:
        return _Harness()


class _ReadOnly:
    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        return Secret("v", MATERIAL)

    async def write(self, tenant: str, scope: CredentialScope, material: object) -> str:
        raise CredentialReadOnly("owned elsewhere")


async def test_cs12_rotating_a_read_only_kind_is_refused() -> None:
    service = HandleCredentialService({"toolset": _ReadOnly()}, ManualClock())
    with pytest.raises(CredentialReadOnly):
        _ = await service.rotate(TENANT, CredentialScope("toolset", "x"), "new")


class _Counting(HandleCredentialService):
    issued: list[str]

    @override
    async def issue(
        self, purpose: CredentialPurpose, principal: CredentialPrincipal, scope: CredentialScope, *, ttl_s: int
    ) -> ShortLivedCredential:
        credential = await super().issue(purpose, principal, scope, ttl_s=ttl_s)
        self.issued.append(credential.handle)
        return credential


async def _seeded() -> tuple[_Counting, ManualClock, CredentialScope]:
    clock = ManualClock()
    service = _Counting({"secret": InMemorySecretSource(clock)}, clock, ids=SeededIds())
    service.issued = []
    scope = CredentialScope("secret", "a")
    _ = await service.rotate(TENANT, scope, MATERIAL)
    return service, clock, scope


async def test_ls1_one_issuance_per_lifetime_then_reissued_before_expiry() -> None:
    service, clock, scope = await _seeded()
    leases = CredentialLeases(service, clock, ttl_s=300)
    for _ in range(5):
        assert (await leases.secret(PURPOSE, PRINCIPAL, scope)).material == MATERIAL
    assert len(service.issued) == 1
    _ = clock.advance(239)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    assert len(service.issued) == 1
    _ = clock.advance(1)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    assert len(service.issued) == 2


async def test_ls2_an_expired_handle_is_reissued() -> None:
    service, clock, scope = await _seeded()
    leases = CredentialLeases(service, clock, ttl_s=300, refresh_before_s=0)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    _ = clock.advance(400)
    assert (await leases.secret(PURPOSE, PRINCIPAL, scope)).material == MATERIAL
    assert len(service.issued) == 2


async def test_ls3_a_revoked_handle_is_never_replaced() -> None:
    service, clock, scope = await _seeded()
    leases = CredentialLeases(service, clock, ttl_s=300)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    await service.revoke(service.issued[0])
    with pytest.raises(CredentialRevoked):
        _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    assert len(service.issued) == 1


async def test_ls4_revoke_all() -> None:
    service, clock, scope = await _seeded()
    leases = CredentialLeases(service, clock, ttl_s=300)
    other = CredentialPurpose(TENANT, "slack@inst-2", "run-1", "run-1-attempt-1")
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    _ = await leases.secret(other, PRINCIPAL, scope)
    held = leases.held()
    assert await leases.revoke_all() == 2
    for credential in held:
        with pytest.raises(CredentialRevoked):
            _ = await service.resolve(credential.handle, credential.purpose)


async def test_refusals_are_logged_once_with_the_handle_and_never_the_material(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    h = _Harness()
    scope = CredentialScope(h.kind, "a")
    _ = await h.service.rotate(TENANT, scope, MATERIAL)
    issued = await h.service.issue(PURPOSE, CredentialPrincipal(TENANT, "user-1"), scope, ttl_s=60)
    _ = await h.service.resolve(issued.handle, PURPOSE)
    await h.service.revoke(issued.handle)
    with pytest.raises(CredentialRevoked):
        _ = await h.service.resolve(issued.handle, PURPOSE)
    refused = [r for r in caplog.records if getattr(r, "harness_event", None) == "credential.refused"]
    assert len(refused) == 1 and refused[0].levelno == logging.WARNING
    assert refused[0].harness["reason"] == "credential_revoked"  # type: ignore[attr-defined]
    assert refused[0].harness["handle"] == issued.handle  # type: ignore[attr-defined]
    events = {getattr(r, "harness_event", None) for r in caplog.records}
    assert {"credential.issued", "credential.resolved", "credential.revoked", "credential.rotated"} <= events
    assert all(MATERIAL not in f"{r.getMessage()} {getattr(r, 'harness', '')}" for r in caplog.records)
