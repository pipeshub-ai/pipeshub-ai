"""Control-plane bindings at the effect boundary (CPD-8): the secret source and the broker over it.

Node's ``VaultCredentialBinder`` is the only writer: each binding's secret is a document in the
P2.4b versioned envelope at ``/services/controlPlane/credentials/{org}/{installation}/{kind}/{id}``,
and a pointer ``/services/controlPlane/bindings/{org}/{binding}`` names it. A run pins the binding
reference and a version digest; the broker leases the binding's *current* version for that pin, so a
token refresh does not break a pin, and the vault's tombstone refuses a revoked binding at the
next lease or resolve whatever the pin says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.control_plane.runtime.bindings import (
    BINDING_KIND,
    binding_pointer_path,
    binding_vault_path,
    is_version_of,
)
from harness_core.credentials.errors import (
    CredentialReadOnly,
    CredentialRevoked,
    CredentialUnavailable,
    CredentialUnknown,
)
from harness_core.credentials.types import CredentialPrincipal, CredentialScope
from harness_core.security.secrets import ResolvedSecrets

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.control_plane.contracts.generated.effective_set import PinnedBinding
    from app.harness.credentials.vault import VaultSecretStore
    from harness_core.credentials.ports import CredentialService
    from harness_core.credentials.types import (
        CredentialPurpose,
        Secret,
        ShortLivedCredential,
    )
    from harness_core.security.secrets import SecretSet


def last_version(document: dict[str, Any]) -> int:
    versions = document.get("versions")
    numbers = [
        int(str(cast("dict[str, Any]", v).get("version"))) for v in cast("list[object]", versions or []) if isinstance(v, dict)
    ]
    return max([*numbers, int(str(document.get("lastVersion") or 0))])


class BindingDocuments:
    """Reads binding documents through the vault (uncached, ``raise_on_error``): by reference or by owner."""

    def __init__(self, vault: VaultSecretStore) -> None:
        self.vault = vault

    async def path_of(self, tenant: str, binding: str) -> str | None:
        pointer = await self.vault.document_at(binding_pointer_path(tenant, binding), tenant)
        path = pointer.get("path")
        return path if isinstance(path, str) and path else None

    async def document(self, tenant: str, binding: str) -> dict[str, Any]:
        path = await self.path_of(tenant, binding)
        return {} if path is None else await self.vault.document_at(path, tenant)

    async def owner_document(self, tenant: str, installation: str, owner: str) -> tuple[str, dict[str, Any]]:
        path = binding_vault_path(tenant, installation, owner)
        return path, await self.vault.document_at(path, tenant)


class BindingSecretSource:
    """``SecretSource`` of kind ``binding``: ``scope.name`` is the binding reference, read in the tenant only."""

    def __init__(self, documents: BindingDocuments) -> None:
        self._documents = documents

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        path = await self._documents.path_of(tenant, scope.name)
        if path is None:
            return None
        return await self._documents.vault.read_at(path, tenant, scope, version)

    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        raise CredentialReadOnly("bindings are written by the control plane's binder, not by the service")


class BindingCredentialBroker:
    """``CredentialBroker`` (03-interfaces §5.2) over ``HandleCredentialService`` and ``BindingSecretSource``.

    It is the call's ``CallSecrets`` (SEC4): every material it resolves is masked exactly in what the
    call returns, until ``close``.
    """

    def __init__(
        self,
        service: CredentialService,
        documents: BindingDocuments,
        *,
        resolved: ResolvedSecrets | None = None,
    ) -> None:
        self._service = service
        self._documents = documents
        self._resolved = resolved if resolved is not None else ResolvedSecrets()
        self._leases: dict[str, ShortLivedCredential] = {}

    async def lease(self, binding: PinnedBinding, purpose: CredentialPurpose, ttl_s: int) -> ShortLivedCredential:
        document = await self._documents.document(purpose.tenant, binding.ref)
        if document.get("revoked"):
            raise CredentialRevoked("the binding was revoked")
        if not document:
            raise CredentialUnavailable("no secret is bound")
        if not is_version_of(binding.ref, binding.version_digest.root, last_version(document)):
            raise CredentialUnavailable("the pinned version is not a version of this binding")
        principal = CredentialPrincipal(purpose.tenant, binding.owner)
        lease = await self._service.issue(purpose, principal, CredentialScope(BINDING_KIND, binding.ref), ttl_s=ttl_s)
        self._leases[lease.handle] = lease
        return lease

    async def resolve(self, handle: ShortLivedCredential, purpose: CredentialPurpose) -> Secret:
        if handle.handle not in self._leases:
            raise CredentialUnknown("no such lease")
        secret = await self._service.resolve(handle.handle, purpose)
        self._resolved.add(secret.material)
        return secret

    async def release(self, handle: ShortLivedCredential) -> None:
        if self._leases.pop(handle.handle, None) is not None:
            await self._service.revoke(handle.handle)

    def resolved_secrets(self) -> SecretSet:
        return self._resolved.resolved_secrets()

    async def close(self) -> int:
        leases, self._leases = list(self._leases), {}
        self._resolved.clear()
        for handle in leases:
            await self._service.revoke(handle)
        return len(leases)


__all__ = ["BindingCredentialBroker", "BindingDocuments", "BindingSecretSource", "last_version"]
