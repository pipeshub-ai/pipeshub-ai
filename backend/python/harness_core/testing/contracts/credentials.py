"""Contract suites for the credential ports (P2.4a): ``CredentialService`` and ``SecretSource``.

``CredentialServiceContract``: provide a ``credential_harness`` fixture
(``CredentialHarness``): the service, the manual clock it reads, a scope kind
the service can write (``kind``), and ``forget(tenant, scope)``, which removes a
secret behind the service's back (a user disconnecting a connection).
``SecretSourceContract``: provide a ``source_harness`` fixture
(``SourceHarness``): the source, the clock it reads, ``own(tenant, scope, material)``
(what the secret's owner does to make ``material`` current: a writable source's
own ``write``, a read-only one's owner record), and whether the source keeps
superseded versions (``keeps_history``) and partitions by tenant
(``partitions_tenants``; a read-only view of deployment-wide platform records does not).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.credentials.errors import (
    CredentialExpired,
    CredentialKindUnknown,
    CredentialPurposeMismatch,
    CredentialReadOnly,
    CredentialRevoked,
    CredentialTenantMismatch,
    CredentialTtlInvalid,
    CredentialUnavailable,
    CredentialUnknown,
)
from harness_core.credentials.types import (
    MAX_TTL_S,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.credentials.ports import CredentialService, SecretSource
    from harness_core.credentials.types import Secret
    from harness_core.testing.clock import ManualClock

TENANT = "org-1"
MATERIAL = "contract-secret-0f5e2a9c71d4"
ROTATED = "contract-secret-rotated-88b1"
PURPOSE = CredentialPurpose(TENANT, "jira@inst-1", "run-1", "run-1-attempt-1")
PRINCIPAL = CredentialPrincipal(TENANT, "user-1")
# Names every kind of source can hold: ``{instance}/{owner}`` and an org model account's ``cloud/{id}``.
ABSENT = "cloud/absent"
NAME = "cloud/contract"


class CredentialHarness(Protocol):
    @property
    def service(self) -> CredentialService: ...

    @property
    def clock(self) -> ManualClock: ...

    @property
    def kind(self) -> str: ...

    async def forget(self, tenant: str, scope: CredentialScope) -> None: ...


class SourceHarness(Protocol):
    @property
    def source(self) -> SecretSource: ...

    @property
    def clock(self) -> ManualClock: ...

    @property
    def kind(self) -> str: ...

    @property
    def keeps_history(self) -> bool: ...

    @property
    def partitions_tenants(self) -> bool: ...

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None: ...


class CredentialServiceContract:
    @pytest.fixture
    def credential_harness(self) -> CredentialHarness:
        raise NotImplementedError("provide a credential_harness fixture")

    @staticmethod
    async def _seeded(h: CredentialHarness, name: str = "secret-a") -> CredentialScope:
        scope = CredentialScope(h.kind, name)
        _ = await h.service.rotate(TENANT, scope, MATERIAL)
        return scope

    async def test_cs1_issue_then_resolve_for_the_same_purpose(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        issued = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=300)
        secret = await h.service.resolve(issued.handle, PURPOSE)
        assert secret.material == MATERIAL
        assert secret.version == issued.version
        assert issued.purpose == PURPOSE and issued.scope == scope

    async def test_cs2_handles_and_reprs_carry_no_material(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        issued = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=300)
        secret = await h.service.resolve(issued.handle, PURPOSE)
        for text in (issued.handle, repr(issued), repr(secret), str(secret)):
            assert MATERIAL not in text

    @pytest.mark.parametrize(
        "other",
        [
            CredentialPurpose(TENANT, "jira@inst-1", "run-2", "run-1-attempt-1"),
            CredentialPurpose(TENANT, "jira@inst-1", "run-1", "run-1-attempt-2"),
            CredentialPurpose(TENANT, "slack@inst-2", "run-1", "run-1-attempt-1"),
            CredentialPurpose("org-2", "jira@inst-1", "run-1", "run-1-attempt-1"),
        ],
        ids=["other-run", "other-attempt", "other-tool", "other-tenant"],
    )
    async def test_cs3_another_purpose_is_refused(
        self, credential_harness: CredentialHarness, other: CredentialPurpose
    ) -> None:
        h = credential_harness
        issued = await h.service.issue(PURPOSE, PRINCIPAL, await self._seeded(h), ttl_s=300)
        with pytest.raises(CredentialPurposeMismatch):
            _ = await h.service.resolve(issued.handle, other)

    async def test_cs4_an_unknown_handle(self, credential_harness: CredentialHarness) -> None:
        with pytest.raises(CredentialUnknown):
            _ = await credential_harness.service.resolve("crh_never_issued", PURPOSE)

    async def test_cs5_expired_after_the_ttl(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        issued = await h.service.issue(PURPOSE, PRINCIPAL, await self._seeded(h), ttl_s=60)
        _ = h.clock.advance(59)
        _ = await h.service.resolve(issued.handle, PURPOSE)
        _ = h.clock.advance(1)
        with pytest.raises(CredentialExpired):
            _ = await h.service.resolve(issued.handle, PURPOSE)

    @pytest.mark.parametrize("ttl", [0, -1, MAX_TTL_S + 1])
    async def test_cs6_ttl_outside_the_bounds(self, credential_harness: CredentialHarness, ttl: int) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        with pytest.raises(CredentialTtlInvalid):
            _ = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=ttl)

    async def test_cs7_a_missing_or_vanished_secret(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        with pytest.raises(CredentialUnavailable):
            _ = await h.service.issue(PURPOSE, PRINCIPAL, CredentialScope(h.kind, "never-stored"), ttl_s=60)
        scope = await self._seeded(h)
        issued = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=60)
        await h.forget(TENANT, scope)
        with pytest.raises(CredentialUnavailable):
            _ = await h.service.resolve(issued.handle, PURPOSE)

    async def test_cs8_principal_of_another_tenant(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        with pytest.raises(CredentialTenantMismatch):
            _ = await h.service.issue(PURPOSE, CredentialPrincipal("org-2", "user-1"), scope, ttl_s=60)

    async def test_cs9_revoked_fails_at_once(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        issued = await h.service.issue(PURPOSE, PRINCIPAL, await self._seeded(h), ttl_s=300)
        _ = await h.service.resolve(issued.handle, PURPOSE)
        await h.service.revoke(issued.handle)
        with pytest.raises(CredentialRevoked):
            _ = await h.service.resolve(issued.handle, PURPOSE)
        await h.service.revoke(issued.handle)
        await h.service.revoke("crh_never_issued")
        with pytest.raises(CredentialRevoked):
            _ = await h.service.resolve(issued.handle, PURPOSE)

    async def test_cs10_rotation_keeps_in_flight_handles_until_their_ttl(
        self, credential_harness: CredentialHarness
    ) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        in_flight = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=120)
        new_version = await h.service.rotate(TENANT, scope, ROTATED)
        assert new_version != in_flight.version
        _ = h.clock.advance(60)
        assert (await h.service.resolve(in_flight.handle, PURPOSE)).material == MATERIAL
        fresh = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=120)
        resolved = await h.service.resolve(fresh.handle, PURPOSE)
        assert (resolved.material, resolved.version) == (ROTATED, new_version)
        _ = h.clock.advance(60)
        with pytest.raises(CredentialExpired):
            _ = await h.service.resolve(in_flight.handle, PURPOSE)

    async def test_cs11_a_kind_no_source_serves(self, credential_harness: CredentialHarness) -> None:
        with pytest.raises(CredentialKindUnknown):
            _ = await credential_harness.service.issue(
                PURPOSE, PRINCIPAL, CredentialScope("no-such-kind", "x"), ttl_s=60
            )

    async def test_cs13_two_handles_are_independent(self, credential_harness: CredentialHarness) -> None:
        h = credential_harness
        scope = await self._seeded(h)
        first = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=60)
        second = await h.service.issue(PURPOSE, PRINCIPAL, scope, ttl_s=60)
        assert first.handle != second.handle
        await h.service.revoke(first.handle)
        assert (await h.service.resolve(second.handle, PURPOSE)).material == MATERIAL


def _holds(secret: Secret | None, material: str) -> bool:
    return secret is not None and material in json.dumps(secret.material)


class SecretSourceContract:
    @pytest.fixture
    def source_harness(self) -> SourceHarness:
        raise NotImplementedError("provide a source_harness fixture")

    async def test_ss1_missing(self, source_harness: SourceHarness) -> None:
        assert await source_harness.source.read(TENANT, CredentialScope(source_harness.kind, ABSENT)) is None

    async def test_ss2_what_the_owner_wrote_reads_as_current(self, source_harness: SourceHarness) -> None:
        scope = CredentialScope(source_harness.kind, NAME)
        await source_harness.own(TENANT, scope, {"token": MATERIAL})
        assert _holds(await source_harness.source.read(TENANT, scope), MATERIAL)

    async def test_ss3_a_rewrite_is_a_new_current_version(self, source_harness: SourceHarness) -> None:
        source, scope = source_harness.source, CredentialScope(source_harness.kind, NAME)
        await source_harness.own(TENANT, scope, MATERIAL)
        first = await source.read(TENANT, scope)
        await source_harness.own(TENANT, scope, ROTATED)
        current = await source.read(TENANT, scope)
        assert first is not None and current is not None and first.version != current.version
        assert _holds(current, ROTATED)
        if source_harness.keeps_history:
            assert _holds(await source.read(TENANT, scope, first.version), MATERIAL)

    async def test_ss4_superseded_versions_go_after_the_retention(self, source_harness: SourceHarness) -> None:
        if not source_harness.keeps_history:
            pytest.skip("this source keeps no superseded versions")
        source, scope = source_harness.source, CredentialScope(source_harness.kind, NAME)
        await source_harness.own(TENANT, scope, MATERIAL)
        first = await source.read(TENANT, scope)
        await source_harness.own(TENANT, scope, ROTATED)
        _ = source_harness.clock.advance(MAX_TTL_S + 1)
        assert first is not None and await source.read(TENANT, scope, first.version) is None
        assert _holds(await source.read(TENANT, scope), ROTATED)

    async def test_ss5_tenants_are_separate(self, source_harness: SourceHarness) -> None:
        if not source_harness.partitions_tenants:
            pytest.skip("a view of deployment-wide records: no org in the record (QA19-O2: model keys)")
        source, scope = source_harness.source, CredentialScope(source_harness.kind, NAME)
        await source_harness.own(TENANT, scope, MATERIAL)
        assert await source.read("org-2", scope) is None

    async def test_ss6_write_versions_what_it_stores_or_refuses_as_read_only(self, source_harness: SourceHarness) -> None:
        source, scope = source_harness.source, CredentialScope(source_harness.kind, NAME)
        await source_harness.own(TENANT, scope, MATERIAL)
        try:
            version = await source.write(TENANT, scope, {"token": ROTATED})
        except CredentialReadOnly:
            assert _holds(await source.read(TENANT, scope), MATERIAL)
            return
        secret = await source.read(TENANT, scope)
        assert secret is not None and (secret.version, secret.material) == (version, {"token": ROTATED})


__all__ = [
    "CredentialHarness",
    "CredentialServiceContract",
    "SecretSourceContract",
    "SourceHarness",
]
