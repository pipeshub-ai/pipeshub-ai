"""In-memory adapters of the model-access ports (tests, Loom, a single-process host)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from harness_core.access.errors import (
    AccessPolicyUnavailable,
    GrantUnavailable,
    ModelAccessRefused,
    RouteNotConfigured,
)
from harness_core.access.types import Spend
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY, OrgAccessPolicy
from harness_core.gateway.types import RunAttemptBinding

if TYPE_CHECKING:
    from harness_core.access.ports import ResolvedRoute, SpendKey
    from harness_core.access.types import ModelNeed, PinnedRoute, RunModelGrant
    from harness_core.gateway.types import GatewayClaims


class InMemoryRunModelGrants:
    """Grants registered per run by the host that pinned them."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], RunModelGrant] = {}
        self.asked: list[str] = []

    def register(self, grant: RunModelGrant) -> None:
        self._grants[grant.tenant, grant.run_id] = grant

    def revoke(self, tenant: str, run_id: str) -> None:
        self._grants.pop((tenant, run_id), None)

    async def grant(self, claims: GatewayClaims, token: str, *, fresh: bool = False) -> RunModelGrant:
        binding = claims.binding
        if not isinstance(binding, RunAttemptBinding) or not token:
            raise GrantUnavailable("no model grant for this binding", http_status=401)
        self.asked.append(binding.run_id)
        found = self._grants.get((binding.tenant, binding.run_id))
        if found is None:
            raise GrantUnavailable("the run has no model grant", detail="run_not_found")
        return found


class StaticModelAccessResolver:
    """Routes a host configured up front, each with its transport."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str, str], ResolvedRoute | ModelAccessRefused] = {}

    def add(self, tenant: str, route: PinnedRoute, resolved: ResolvedRoute) -> None:
        self._routes[tenant, route.provider, route.model] = resolved

    def refuse(self, tenant: str, route: PinnedRoute, refusal: ModelAccessRefused) -> None:
        """A route the host knows but may not serve (no transport, an account kind not permitted)."""
        self._routes[tenant, route.provider, route.model] = refusal

    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        found = self._routes.get((tenant, route.provider, route.model))
        if found is None:
            raise RouteNotConfigured("the pinned model route is not configured")
        if isinstance(found, ModelAccessRefused):
            raise found
        return found


class InMemoryOrgAccessPolicies:
    """Org access policies held by the host; ``down`` simulates a store that cannot be read."""

    def __init__(self) -> None:
        self._policies: dict[str, OrgAccessPolicy] = {}
        self.down = False

    def set(self, tenant: str, policy: OrgAccessPolicy) -> None:
        self._policies[tenant] = policy

    async def policy(self, tenant: str) -> OrgAccessPolicy:
        if self.down:
            raise AccessPolicyUnavailable("the access policy store is down")
        return self._policies.get(tenant, DEFAULT_ORG_POLICY)


class InMemorySpendCounter:
    def __init__(self) -> None:
        self._spent: dict[SpendKey, Spend] = {}
        self._charged: set[tuple[SpendKey, str]] = set()
        self._lock = asyncio.Lock()

    async def spent(self, key: SpendKey) -> Spend:
        return self._spent.get(key, Spend())

    async def charge(self, key: SpendKey, charge_id: str, amount: Spend) -> Spend:
        async with self._lock:
            current = self._spent.get(key, Spend())
            if (key, charge_id) in self._charged:
                return current
            self._charged.add((key, charge_id))
            total = replace(
                current,
                usd_micros=current.usd_micros + amount.usd_micros,
                tokens=current.tokens + amount.tokens,
            )
            self._spent[key] = total
            return total

    async def stop(self, key: SpendKey) -> None:
        async with self._lock:
            self._spent[key] = replace(self._spent.get(key, Spend()), stopped=True)


__all__ = ["InMemoryOrgAccessPolicies", "InMemoryRunModelGrants", "InMemorySpendCounter", "StaticModelAccessResolver"]
