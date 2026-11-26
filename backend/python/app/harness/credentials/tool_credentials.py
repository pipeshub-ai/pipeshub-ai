"""Connector credentials at the effect boundary, for both loops.

The route and the durable assembler keep a *reference* per toolset or MCP
instance in the request's state (``credentialRef``: kind and
``{instanceId}/{owner}``) after checking the connection is authenticated. The
record itself is resolved here, through a handle bound to that toolset or
instance and to this run, when a client is created or an MCP session opened,
and is never written back to the state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from app.harness.credentials.sources import MCP_KIND, TOOLSET_KIND, owner_scoped
from harness_core.clock import SystemClock
from harness_core.credentials.errors import (
    CredentialMissing,
    CredentialOwnerMismatch,
    CredentialUnavailable,
)
from harness_core.credentials.leases import CredentialLeases
from harness_core.credentials.types import (
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
)
from harness_core.security.secrets import ResolvedSecrets

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.credentials.ports import CredentialService
    from harness_core.security.secrets import SecretSet

REF_KEY = "credentialRef"
UNAVAILABLE_KEY = "unavailable"
# An MCP instance in headers mode authenticates with a header of the admin's naming (``credentials.headerValue``).
SECRET_FIELDS = ("headerValue",)


class BoundCredentials(Protocol):
    """Installations whose record a run's pin names (CPD-10): read from the pinned binding's lease, never from
    the owner path, and closed with the attempt."""

    def holds(self, installation: str) -> bool: ...

    async def material(self, installation: str) -> JsonValue: ...

    async def close(self) -> int: ...


def credential_reference(kind: str, instance_id: str, owner: str) -> dict[str, Any]:
    return {REF_KEY: {"kind": kind, "name": f"{instance_id}/{owner}"}, "isAuthenticated": True}


def toolset_reference(instance_id: str, owner: str) -> dict[str, Any]:
    return credential_reference(TOOLSET_KIND, instance_id, owner)


def unavailable_reference(item: str) -> dict[str, Any]:
    """A toolset the run's owner has not connected (QA23-F1): the run keeps it, every call is refused
    ``credential_missing``."""
    return {UNAVAILABLE_KEY: CredentialMissing.reason_code, "item": item}


def mcp_reference(instance_id: str, owner: str) -> dict[str, Any]:
    return credential_reference(MCP_KIND, instance_id, owner)[REF_KEY]


def scope_of(reference: object) -> CredentialScope | None:
    """The scope a state entry refers to; ``None`` for a raw record (callers that pass one)."""
    if not isinstance(reference, dict):
        return None
    ref = cast("dict[str, Any]", reference).get(REF_KEY, reference)
    if not isinstance(ref, dict):
        return None
    kind, name = cast("dict[str, Any]", ref).get("kind"), cast("dict[str, Any]", ref).get("name")
    return CredentialScope(kind, name) if isinstance(kind, str) and isinstance(name, str) and "/" in name else None


class ToolCredentials:
    """One request's (or one attempt's) handles: issued lazily, revoked at teardown.

    Bound to the request's actor (SS2): a reference whose owner is neither the
    actor nor ``credential_owner`` (a service-account agent's own connection)
    is refused before any handle is issued, so a run started by one person
    never resolves another person's connection.

    It is the call's ``SecretSource`` (SEC4): every record it resolved is masked
    exactly in what the attempt's calls return, until ``close``.
    """

    def __init__(
        self,
        service: CredentialService,
        *,
        tenant: str,
        run_id: str,
        principal: str,
        attempt_id: str | None = None,
        clock: Clock | None = None,
        credential_owner: str | None = None,
    ) -> None:
        self._tenant = tenant
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._principal = CredentialPrincipal(tenant, principal)
        self._owners = frozenset({principal, *([credential_owner] if credential_owner else [])})
        # OWD-18 bot accounts the run's pin names and admission checked: (instance, service-account agent) pairs,
        # never an owner for every instance.
        self._bots: set[tuple[str, str]] = set()
        self._resolved = ResolvedSecrets(SECRET_FIELDS)
        self._bound: BoundCredentials | None = None
        self._leases = CredentialLeases(service, clock or SystemClock(), resolved=self._resolved)

    def admit_bot(self, instance: str, owner: str) -> None:
        """``owner``'s own connection of ``instance`` may be resolved: the org's bot account for it (OWD-18), which
        admission confirmed is a service-account agent of the run's org. Only that instance; never the pin's
        binding, which holds the actor's connection."""
        self._bots.add((instance, owner))

    def route(self, bound: BoundCredentials) -> None:
        """From now on an installation ``bound`` holds is read there: what the run's pin bound, whoever the
        reference names as owner (admission chose the binding for this actor)."""
        self._bound = bound

    async def material(self, scope: CredentialScope) -> JsonValue:
        instance, owner = owner_scoped(scope.name)
        bot = (instance, owner) in self._bots
        if not bot and self._bound is not None and self._bound.holds(instance):
            material = await self._bound.material(instance)
            # Masked like every record this attempt resolved (SEC4), until ``close``.
            self._resolved.add(material)
            return material
        if not bot and owner not in self._owners:
            raise CredentialOwnerMismatch("the connection belongs to someone other than the run's actor")
        purpose = CredentialPurpose(self._tenant, f"{scope.kind}:{instance}", self._run_id, self._attempt_id)
        return (await self._leases.secret(purpose, self._principal, scope)).material

    async def _record(self, reference: dict[str, Any], kind: str) -> dict[str, Any]:
        if reference.get(UNAVAILABLE_KEY) == CredentialMissing.reason_code:
            raise CredentialMissing(f"{reference.get('item') or 'the toolset'} is not connected for this user")
        scope = scope_of(reference)
        if scope is None:
            return reference
        if scope.kind != kind:
            raise CredentialUnavailable(f"expected a {kind} credential reference")
        material = await self.material(scope)
        if not isinstance(material, dict):
            raise CredentialUnavailable("the credential record has an unexpected shape")
        return cast("dict[str, Any]", material)

    async def toolset_config(self, reference: dict[str, Any]) -> dict[str, Any]:
        """The toolset's stored record for a reference; a raw record is returned as it is."""
        return await self._record(reference, TOOLSET_KIND)

    async def mcp_auth(self, reference: dict[str, Any]) -> dict[str, Any]:
        return await self._record(reference, MCP_KIND)

    def resolved_secrets(self) -> SecretSet:
        return self._resolved.resolved_secrets()

    def handles(self) -> tuple[str, ...]:
        return tuple(credential.handle for credential in self._leases.held())

    async def close(self) -> int:
        closed = await self._leases.revoke_all()
        return closed if self._bound is None else closed + await self._bound.close()


__all__ = [
    "REF_KEY",
    "SECRET_FIELDS",
    "BoundCredentials",
    "ToolCredentials",
    "credential_reference",
    "mcp_reference",
    "scope_of",
    "toolset_reference",
    "unavailable_reference",
]
