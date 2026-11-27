"""An attempt's leases on its items' pinned bindings (CPD-10, FR-31).

A ready item of a run's effective set names the binding admission pinned for it. The attempt leases every such
binding before a tool source creates a client (``lease_all``), and the toolset and MCP connectors lease their
item's when it loads (the same lease, held once): a revoked binding, or a version that is not the pinned
binding's, is a lost capability before the run starts, and a refreshed version of the same binding is accepted
(the broker's rule). With ``CONTROL_PLANE_CREDENTIALS`` on, the binding is the credential's record, so the tool client's record
is the lease's material. With the switch off nothing renews a binding (the legacy refreshers write the old path
only), so the record stays the owner path's, as today: the lease still holds the pin, never a stale copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.harness.credentials.read_through import credentials_switch_on
from harness_core.credentials.errors import (
    CredentialExpired,
    CredentialRefused,
    CredentialStoreUnavailable,
    CredentialUnavailable,
)
from harness_core.credentials.types import MAX_TTL_S, CredentialPurpose
from harness_core.driver import CapabilityLost

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.control_plane.contracts.generated.effective_set import (
        EffectiveItem,
        PinnedBinding,
    )
    from app.control_plane.runtime.ports import CredentialBroker
    from app.control_plane.runtime.types import RunContext
    from harness_core.credentials.types import Secret, ShortLivedCredential


class RunCredentialBroker(Protocol):
    """The broker one attempt's items lease from: ``CredentialBroker``, closed with the attempt."""

    async def lease(self, binding: PinnedBinding, purpose: CredentialPurpose, ttl_s: int) -> ShortLivedCredential: ...

    async def resolve(self, handle: ShortLivedCredential, purpose: CredentialPurpose) -> Secret: ...

    async def close(self) -> int: ...


@dataclass(slots=True)
class _Held:
    binding: PinnedBinding
    purpose: CredentialPurpose
    lease: ShortLivedCredential


def bound_installations(items: tuple[EffectiveItem, ...]) -> frozenset[str]:
    """The installations whose record is their pinned binding's: every bound item, while the switch is on."""
    if not credentials_switch_on():
        return frozenset()
    return frozenset(item.installation.root for item in items if item.state == "ready" and item.binding is not None)


class PinnedBindingLeases:
    """What the connectors leased, by installation; the attempt's ``ToolCredentials`` reads its records here."""

    def __init__(self, broker: RunCredentialBroker) -> None:
        self._broker = broker
        self._held: dict[str, _Held] = {}
        self._leased: set[str] = set()

    async def lease(self, item: EffectiveItem, ctx: RunContext, credentials: CredentialBroker) -> None:
        """Leases ``item``'s pinned binding from the broker its load was given; refused is a lost capability."""
        binding = item.binding
        if binding is None or item.kind is None:
            return
        installation = item.installation.root
        held = self._held.get(installation)
        if held is not None and held.binding == binding:
            return
        purpose = CredentialPurpose(ctx.org_id, f"{item.kind.root}:{installation}", ctx.run_id, ctx.attempt_id)
        try:
            lease = await credentials.lease(binding, purpose, MAX_TTL_S)
        except CredentialStoreUnavailable as down:
            # The vault may answer on the claim's next check of the same pin.
            raise CapabilityLost(missing=[f"credential:{installation}:{down.reason_code}"]) from None
        except CredentialRefused as refused:
            raise CapabilityLost(changed=[f"credential:{installation}:{refused.reason_code}"]) from None
        self._held[installation] = _Held(binding, purpose, lease)
        self._leased.add(installation)

    async def lease_all(self, items: tuple[EffectiveItem, ...], ctx: RunContext, credentials: CredentialBroker) -> None:
        """Every bound ready item's lease, with one refusal naming every credential that is lost."""
        missing: list[str] = []
        changed: list[str] = []
        for item in items:
            if item.state != "ready":
                continue
            try:
                await self.lease(item, ctx, credentials)
            except CapabilityLost as lost:
                missing.extend(lost.missing)
                changed.extend(lost.changed)
        if missing or changed:
            raise CapabilityLost(missing=missing, changed=changed)

    def holds(self, installation: str) -> bool:
        """Whether the installation's record is read here, not at the owner path. It stays so after ``close``:
        a bound installation never falls back to a record no pin names."""
        return installation in self._leased and credentials_switch_on()

    async def material(self, installation: str) -> JsonValue:
        held = self._held.get(installation)
        if held is None:
            raise CredentialUnavailable("the installation's pinned binding is not leased")
        try:
            secret = await self._broker.resolve(held.lease, held.purpose)
        except CredentialExpired:
            # A lease is shorter than an attempt may run; a revoked binding is refused here, never leased again.
            held.lease = await self._broker.lease(held.binding, held.purpose, MAX_TTL_S)
            secret = await self._broker.resolve(held.lease, held.purpose)
        return secret.material

    async def close(self) -> int:
        self._held.clear()
        return await self._broker.close()


__all__ = ["PinnedBindingLeases", "RunCredentialBroker", "bound_installations"]
