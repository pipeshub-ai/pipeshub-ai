"""Contract suites for the model-access ports (E3): ``RunModelGrants``, ``ModelAccessResolver``,
``SpendCounter``.

``RunModelGrantsContract``: provide a ``grants_harness`` fixture (``GrantsHarness``):
the source under test and ``pin(binding, routes, budget)``, which is what the
host does when it pins a run (the in-memory source registers it; a Node-backed
one makes its fake Node answer for it), plus ``token(binding)``.
``ModelAccessResolverContract``: provide a ``resolver_harness`` fixture
(``ResolverHarness``): the resolver and the routes the harness configured.
``SpendCounterContract``: provide a ``spend_counter`` fixture.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.access.errors import (
    AccountNotPermitted,
    GrantUnavailable,
    ProviderUnsupported,
    RouteNotConfigured,
)
from harness_core.access.ports import SpendKey
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    ROLE_MODEL_IDS,
    AccountKind,
    ModelApi,
    ModelNeed,
    PinnedRoute,
    RouteKind,
    RunBudget,
    Spend,
    route_digest,
)
from harness_core.gateway.types import GatewayClaims, RunAttemptBinding

if TYPE_CHECKING:
    from harness_core.access.ports import (
        ModelAccessResolver,
        RunModelGrants,
        SpendCounter,
    )

ROUTE = PinnedRoute("openai", "gpt-x", config_key="key-1", config_model="gpt-x")
# Who calls, from which engine, on which API: a resolver that enforces vendor rules needs it (A1).
NEED = ModelNeed(actor="user-1", engine="opencode", api=ModelApi.CHAT_COMPLETIONS)
FALLBACK = PinnedRoute("anthropic", "claude-x", config_key="key-2", config_model="claude-x")
BUDGET = RunBudget(max_cost_usd=2.0, max_tokens=400_000)


def run_binding(run_id: str = "run-1", *, tenant: str = "org-1", routes: tuple[PinnedRoute, ...] = (ROUTE,)) -> RunAttemptBinding:
    return RunAttemptBinding(
        tenant=tenant,
        scope="session-1",
        run_id=run_id,
        parent_run_id=None,
        attempt_id=f"{run_id}-attempt-1",
        fence=1,
        tool_ids_digest="tools",
        routes_digest=route_digest(routes),
    )


class GrantsHarness(Protocol):
    @property
    def grants(self) -> RunModelGrants: ...

    def pin(self, binding: RunAttemptBinding, routes: tuple[PinnedRoute, ...], budget: RunBudget) -> None: ...

    def token(self, binding: RunAttemptBinding) -> str: ...


class RunModelGrantsContract:
    @pytest.fixture
    def grants_harness(self) -> GrantsHarness:
        raise NotImplementedError("provide a grants_harness fixture")

    @staticmethod
    def _claims(binding: RunAttemptBinding) -> GatewayClaims:
        return GatewayClaims(binding, "hgk_test", 4_102_444_800)

    async def test_rg1_a_pinned_run_has_its_grant(self, grants_harness: GrantsHarness) -> None:
        binding = run_binding()
        grants_harness.pin(binding, (ROUTE,), BUDGET)
        grant = await grants_harness.grants.grant(self._claims(binding), grants_harness.token(binding))
        assert (grant.tenant, grant.run_id) == ("org-1", "run-1")
        assert grant.routes == (ROUTE,)
        assert grant.budget == BUDGET

    async def test_rg2_the_first_route_is_the_engine_model_and_fallbacks_get_no_id(
        self, grants_harness: GrantsHarness
    ) -> None:
        binding = run_binding(routes=(ROUTE, FALLBACK))
        grants_harness.pin(binding, (ROUTE, FALLBACK), BUDGET)
        grant = await grants_harness.grants.grant(self._claims(binding), grants_harness.token(binding))
        # GA-15: one id per role; with no role routes every role is the first route, and no fallback has an id.
        assert [m.model_id for m in grant.models] == list(ROLE_MODEL_IDS.values())
        assert {m.route for m in grant.models} == {ROUTE}
        assert grant.model(ENGINE_MODEL_ID) is not None
        assert grant.model(ENGINE_MODEL_ID).route == ROUTE  # type: ignore[union-attr]
        assert grant.model("gpt-x") is None

    async def test_rg3_a_run_without_a_pin_is_refused(self, grants_harness: GrantsHarness) -> None:
        pinned, other = run_binding("run-1"), run_binding("run-2")
        grants_harness.pin(pinned, (ROUTE,), BUDGET)
        with pytest.raises(GrantUnavailable):
            await grants_harness.grants.grant(self._claims(other), grants_harness.token(other))

    async def test_rg4_the_grant_digest_matches_the_pinned_routes(self, grants_harness: GrantsHarness) -> None:
        binding = run_binding()
        grants_harness.pin(binding, (ROUTE,), BUDGET)
        grant = await grants_harness.grants.grant(self._claims(binding), grants_harness.token(binding))
        assert route_digest(grant.routes) == binding.routes_digest


class ResolverHarness(Protocol):
    @property
    def resolver(self) -> ModelAccessResolver: ...

    @property
    def tenant(self) -> str: ...

    @property
    def configured(self) -> PinnedRoute:
        """A route the org configured with an API key the gateway can call."""
        ...

    @property
    def unsupported(self) -> PinnedRoute:
        """A configured route whose provider has no transport."""
        ...

    @property
    def not_permitted(self) -> PinnedRoute:
        """A configured route reached through an account kind the gateway route may not use."""
        ...


class ModelAccessResolverContract:
    @pytest.fixture
    def resolver_harness(self) -> ResolverHarness:
        raise NotImplementedError("provide a resolver_harness fixture")

    async def test_mr1_a_configured_route_is_an_org_key_gateway_route(self, resolver_harness: ResolverHarness) -> None:
        resolved = await resolver_harness.resolver.resolve(resolver_harness.tenant, resolver_harness.configured, need=NEED)
        access = resolved.access
        assert access.kind is RouteKind.GATEWAY
        assert access.api is ModelApi.CHAT_COMPLETIONS
        assert access.account is not None and access.account.kind is AccountKind.ORG_API_KEY
        assert access.pays == "org" and access.charges_usd
        assert resolved.pricing_model
        assert resolved.transport is not None

    async def test_mr2_an_unknown_route_is_refused_never_replaced(self, resolver_harness: ResolverHarness) -> None:
        missing = PinnedRoute("openai", "no-such-model", config_key="no-such-key", config_model="no-such-model")
        with pytest.raises(RouteNotConfigured) as refused:
            await resolver_harness.resolver.resolve(resolver_harness.tenant, missing, need=NEED)
        assert refused.value.reason_code == "route_not_configured"

    async def test_mr3_a_provider_without_a_transport_is_refused(self, resolver_harness: ResolverHarness) -> None:
        with pytest.raises(ProviderUnsupported):
            await resolver_harness.resolver.resolve(resolver_harness.tenant, resolver_harness.unsupported, need=NEED)

    async def test_mr4_an_account_kind_the_route_may_not_use_is_refused(self, resolver_harness: ResolverHarness) -> None:
        with pytest.raises(AccountNotPermitted):
            await resolver_harness.resolver.resolve(resolver_harness.tenant, resolver_harness.not_permitted, need=NEED)


class SpendCounterContract:
    @pytest.fixture
    def spend_counter(self) -> SpendCounter:
        raise NotImplementedError("provide a spend_counter fixture")

    @pytest.fixture
    def spend_key(self) -> SpendKey:
        """Override for a shared store, so parallel test runs do not collide."""
        return SpendKey("org-1", "run-spend-1")

    async def test_sc1_a_new_run_has_spent_nothing(self, spend_counter: SpendCounter, spend_key: SpendKey) -> None:
        assert await spend_counter.spent(spend_key) == Spend()

    async def test_sc2_charges_add_up(self, spend_counter: SpendCounter, spend_key: SpendKey) -> None:
        await spend_counter.charge(spend_key, "c-1", Spend(1_500, 100))
        total = await spend_counter.charge(spend_key, "c-2", Spend(2_500, 50))
        assert total == Spend(4_000, 150)
        assert await spend_counter.spent(spend_key) == total

    async def test_sc3_a_charge_counts_once(self, spend_counter: SpendCounter, spend_key: SpendKey) -> None:
        await spend_counter.charge(spend_key, "c-1", Spend(1_000, 10))
        again = await spend_counter.charge(spend_key, "c-1", Spend(1_000, 10))
        concurrent = await asyncio.gather(
            *(spend_counter.charge(spend_key, "c-2", Spend(7, 1)) for _ in range(5))
        )
        assert again == Spend(1_000, 10)
        assert await spend_counter.spent(spend_key) == Spend(1_007, 11)
        assert max(t.usd_micros for t in concurrent) == 1_007

    async def test_sc4_runs_are_separate(self, spend_counter: SpendCounter, spend_key: SpendKey) -> None:
        other = SpendKey(spend_key.tenant, spend_key.run_id + "-other")
        await spend_counter.charge(spend_key, "c-1", Spend(5, 5))
        assert await spend_counter.spent(other) == Spend()
        other_tenant = SpendKey(spend_key.tenant + "-other", spend_key.run_id)
        assert await spend_counter.spent(other_tenant) == Spend()

    async def test_sc5_a_stopped_run_stays_stopped(self, spend_counter: SpendCounter, spend_key: SpendKey) -> None:
        await spend_counter.charge(spend_key, "c-1", Spend(5, 5))
        await spend_counter.stop(spend_key)
        after = await spend_counter.charge(spend_key, "c-2", Spend(1, 1))
        assert after == Spend(6, 6, stopped=True)
        assert (await spend_counter.spent(spend_key)).stopped




__all__ = [
    "BUDGET",
    "FALLBACK",
    "ROUTE",
    "NEED",
    "GrantsHarness",
    "ModelAccessResolverContract",
    "ResolverHarness",
    "RunModelGrantsContract",
    "SpendCounterContract",
    "run_binding",
]
