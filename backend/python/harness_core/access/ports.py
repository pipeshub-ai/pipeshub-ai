"""Ports of model access (E3): the run's pinned grant, route resolution, spend.

Contract suites: ``harness_core.testing.contracts.model_access``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harness_core.security.secrets import EMPTY_SECRETS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.access.errors import ModelAccessRefused
    from harness_core.access.types import (
        AccessRoute,
        AccountRef,
        ModelAccount,
        ModelNeed,
        PinnedRoute,
        RunModelGrant,
        Spend,
    )
    from harness_core.access.vendor_rules import OrgAccessPolicy
    from harness_core.gateway.types import GatewayClaims, RunAttemptBinding
    from harness_core.models.transports.base import LLMTransport
    from harness_core.security.secrets import SecretSet


@dataclass(frozen=True, slots=True)
class SpendKey:
    tenant: str
    run_id: str


@dataclass(frozen=True, slots=True)
class ResolvedRoute:
    """A route the gateway may call now: what it is, and the transport that reaches it.
    The transport holds the credential; it is never logged or returned to a caller."""

    access: AccessRoute
    transport: LLMTransport
    # The model name prices are looked up by (a deployment name is not one).
    pricing_model: str
    # The credential the transport holds, masked exactly in what the gateway records of a call (SEC4).
    secrets: SecretSet = field(default=EMPTY_SECRETS, repr=False)
    # G-45: the provider limit this route's calls count against (``upstream_key``: provider, account, endpoint);
    # empty, the provider's own (``provider_key(access.provider)``).
    admission_key: str = ""


class RunModelGrants(Protocol):
    async def grant(self, claims: GatewayClaims, token: str, *, fresh: bool = False) -> RunModelGrant:
        """The run's pinned model grant for a verified run token; ``token`` is the
        presented one, forwarded only to the authority that issued it. ``fresh``: read
        past any cache (what changes while the run runs: its classification level).
        Raises ``GrantUnavailable``."""
        ...


class ModelAccessResolver(Protocol):
    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        """Exactly the pinned route, or a ``ModelAccessRefused``; never another model.
        ``run_id`` is the run the call serves: the route's credential is bound to it.
        ``need`` is who calls from which engine on which API (A1): an adapter that
        enforces vendor rules refuses without it; one that only builds transports ignores it."""
        ...


class OrgAccessPolicies(Protocol):
    """Each org's narrowing of the vendor rules (A1). An org without one has the default
    policy (nothing switched off, consumer plans off); a store that cannot be read is
    ``AccessPolicyUnavailable``, never the default."""

    async def policy(self, tenant: str) -> OrgAccessPolicy: ...


class ModelAccountStore(Protocol):
    """Model accounts: users' own API keys and org cloud accounts (P2.4b).

    Reads answer metadata only. The secret is read at the effect boundary
    through the credential service, from a ``SecretSource`` over the same store
    with the scope name ``ref.account_id``; a removed account reads as
    ``credential_revoked`` there, every version at once.
    """

    async def put(
        self,
        tenant: str,
        ref: AccountRef,
        provider: str,
        material: Mapping[str, JsonValue],
        *,
        secret_hint: str,
        label: str | None,
        actor: str,
    ) -> ModelAccount:
        """Adds the account, or rotates it: a new version, the previous one readable until
        the retention so handles issued before keep working. ``secret_hint`` is the
        secret whose last four characters reads may show."""
        ...

    async def remove(self, tenant: str, ref: AccountRef, *, actor: str) -> bool:
        """Revokes the account: every version unreadable at once. False when there was none."""
        ...

    async def account(self, tenant: str, ref: AccountRef) -> ModelAccount | None: ...

    async def accounts(self, tenant: str, owner: str) -> tuple[ModelAccount, ...]:
        """The owner's accounts (a user id, or ``ORG_OWNER``), oldest first."""
        ...

    async def erase(self, tenant: str, owner: str | None = None) -> int:
        """Deletes an owner's accounts (``None``: the tenant's) with every version and
        their metadata, leaving no tombstone; returns how many."""
        ...


class SpendCounter(Protocol):
    async def spent(self, key: SpendKey) -> Spend: ...

    async def charge(self, key: SpendKey, charge_id: str, amount: Spend) -> Spend:
        """Adds ``amount`` once per ``charge_id``; returns the run's total after it."""
        ...

    async def stop(self, key: SpendKey) -> None:
        """Marks the run stopped: it crossed its budget; later spends report ``stopped``."""
        ...


class ConversationClassification(Protocol):
    """What a conversation's data lets a model read (OWD-15, GA-16): the model gateway asks before every call."""

    async def model_refusal(
        self,
        binding: RunAttemptBinding,
        grant: Callable[[], Awaitable[RunModelGrant]],
        model_id: str,
        provider: str,
    ) -> ModelAccessRefused | None:
        """The refusal (its own reason code, in words a person reads) when the model may not read what the binding's
        conversation has read; ``None``: it may. ``grant`` reads the run's grant as it stands now (its ``carried`` fields: what the run recorded),
        past the grant cache; the host reads the org's classification and the model's data handling."""
        ...


__all__ = [
    "ConversationClassification",
    "ModelAccessResolver",
    "ModelAccountStore",
    "OrgAccessPolicies",
    "ResolvedRoute",
    "RunModelGrants",
    "SpendCounter",
    "SpendKey",
]
