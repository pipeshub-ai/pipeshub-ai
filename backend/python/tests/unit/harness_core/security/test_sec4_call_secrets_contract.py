"""SEC4: the ``CallSecrets`` contract on the in-memory holder and on the leases that fill it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.credentials import (
    CredentialLeases,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
    HandleCredentialService,
    InMemorySecretSource,
)
from harness_core.security.secrets import ResolvedSecrets
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.call_secrets import CallSecretsContract
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from pydantic import JsonValue

TENANT = "org-1"


class _Held:
    def __init__(self) -> None:
        self.secrets = ResolvedSecrets()

    async def resolve(self, material: JsonValue) -> None:
        self.secrets.add(material)

    async def close(self) -> None:
        self.secrets.clear()


class _Leased:
    """Records resolved through ``CredentialLeases``, one scope each, as a client factory resolves them."""

    def __init__(self) -> None:
        clock = ManualClock()
        self._service = HandleCredentialService({"toolset": InMemorySecretSource(clock)}, clock, ids=SeededIds())
        self.secrets = ResolvedSecrets()
        self._leases = CredentialLeases(self._service, clock, resolved=self.secrets)
        self._resolved = 0

    async def resolve(self, material: JsonValue) -> None:
        self._resolved += 1
        scope = CredentialScope("toolset", f"inst-{self._resolved}/user-1")
        _ = await self._service.rotate(TENANT, scope, material)
        purpose = CredentialPurpose(TENANT, f"toolset:inst-{self._resolved}", "run-1", "run-1-attempt-1")
        _ = await self._leases.secret(purpose, CredentialPrincipal(TENANT, "user-1"), scope)

    async def close(self) -> None:
        _ = await self._leases.revoke_all()


class TestResolvedSecretsContract(CallSecretsContract):
    @pytest.fixture
    def call_secrets_harness(self) -> _Held:
        return _Held()


class TestLeasedResolvedSecretsContract(CallSecretsContract):
    @pytest.fixture
    def call_secrets_harness(self) -> _Leased:
        return _Leased()
