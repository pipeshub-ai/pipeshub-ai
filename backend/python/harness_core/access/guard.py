"""``GuardedModelAccess``: a ``ModelAccessResolver`` that enforces the vendor rules around another (A1).

Before the inner resolver reads any credential: the call names who acts and from
which engine (``access_need_missing``), the engine speaks the API the call came in
on (``model_api_not_permitted``), a pinned user key is the acting user's own
(``account_not_owned``), and a pinned account's route passes the vendor rules,
the install's offering and the org's policy. After it resolves, the same rules
are checked again on what it resolved (an org-key route's account kind is known
only from the host's configuration). Nothing here builds a transport or calls a
provider; a refusal is raised for the caller to log once (the gateway does).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.access.errors import (
    AccessNeedMissing,
    AccountNotOwned,
    ModelApiNotPermitted,
    VendorRuleMissing,
)
from harness_core.access.types import AccountKind, RouteKind, account_owner
from harness_core.access.vendor_rules import AccessCandidate, decide
from harness_core.models.placement import NO_MODEL_REGIONS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.access.ports import (
        ModelAccessResolver,
        OrgAccessPolicies,
        ResolvedRoute,
    )
    from harness_core.access.types import ModelApi, ModelNeed, PinnedRoute
    from harness_core.access.vendor_rules import Offering, VendorRules
    from harness_core.models.placement import ModelRegions


class GuardedModelAccess:
    def __init__(
        self,
        inner: ModelAccessResolver,
        *,
        rules: Callable[[], VendorRules],
        policies: OrgAccessPolicies,
        offering: Offering,
        engine_apis: Mapping[str, frozenset[ModelApi]],
        declared_region: Callable[[str, str, str], Awaitable[str | None]] | None = None,
        regions: ModelRegions = NO_MODEL_REGIONS,
    ) -> None:
        """``rules`` answers the verified release rules or raises ``VendorRulesInvalid``;
        ``engine_apis`` is each engine kind's model APIs (an engine it lacks speaks none);
        ``declared_region(tenant, model, provider)`` the region the org declared for a model, read only for a
        route whose configuration names none; ``regions`` decides whether an org's pins allow a route."""
        self._inner = inner
        self._regions = regions
        self._declared_region = declared_region
        self._rules = rules
        self._policies = policies
        self._offering: Offering = offering
        self._engine_apis = engine_apis

    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        if need is None or not need.actor or not need.engine:
            raise AccessNeedMissing("the grant names no acting user or engine")
        if need.api not in self._engine_apis.get(need.engine, frozenset()):
            raise ModelApiNotPermitted("the run's engine does not speak this model API")
        account = route.account
        if account is not None and account.kind is AccountKind.USER_API_KEY and account_owner(account) != need.actor:
            raise AccountNotOwned("the pinned key belongs to another user")
        rules = self._rules()
        policy = await self._policies.policy(tenant)
        if account is not None:
            decide(rules, AccessCandidate(route.provider, account.kind, RouteKind.GATEWAY, need.engine), self._offering, policy)
        resolved = await self._inner.resolve(tenant, route, run_id=run_id, need=need)
        access = resolved.access
        if access.account is None:
            raise VendorRuleMissing("the resolved route names no account")
        decide(
            rules,
            AccessCandidate(access.provider, access.account.kind, access.kind, need.engine),
            self._offering,
            policy,
        )
        if policy.region_pins:
            declared = None
            if access.region is None and self._declared_region is not None:
                declared = await self._declared_region(tenant, access.model, access.provider)
            why = self._regions.refusal(policy.region_pins, access.region, declared=declared)
            if why is not None:
                raise self._regions.refused(why)
        return resolved


__all__ = ["GuardedModelAccess"]
