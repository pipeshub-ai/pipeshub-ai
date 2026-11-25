"""E3 RG, MR, SC: the model-access contracts against the in-memory adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.access.errors import AccountNotPermitted, ProviderUnsupported
from harness_core.access.memory import (
    InMemoryRunModelGrants,
    InMemorySpendCounter,
    StaticModelAccessResolver,
)
from harness_core.access.types import (
    AccountKind,
    AccountRef,
    PinnedRoute,
    RunBudget,
    RunModelGrant,
    neutral_models,
    route_digest,
)
from harness_core.gateway.types import grant_digest
from harness_core.testing.contracts.model_access import (
    ROUTE,
    ModelAccessResolverContract,
    RunModelGrantsContract,
    SpendCounterContract,
)
from harness_core.testing.model_gateway import RecordedTransport, org_key_route

if TYPE_CHECKING:
    from harness_core.gateway.types import RunAttemptBinding


@dataclass
class _Grants:
    grants: InMemoryRunModelGrants = field(default_factory=InMemoryRunModelGrants)

    def pin(self, binding: RunAttemptBinding, routes: tuple[PinnedRoute, ...], budget: RunBudget) -> None:
        self.grants.register(
            RunModelGrant(binding.tenant, binding.run_id, routes, neutral_models(routes), budget)
        )

    def token(self, binding: RunAttemptBinding) -> str:
        return f"token-{binding.run_id}"


class TestInMemoryRunModelGrants(RunModelGrantsContract):
    @pytest.fixture
    def grants_harness(self) -> _Grants:
        return _Grants()


@dataclass
class _Resolver:
    resolver: StaticModelAccessResolver = field(default_factory=StaticModelAccessResolver)
    tenant: str = "org-1"
    configured: PinnedRoute = ROUTE
    unsupported: PinnedRoute = PinnedRoute("cohere", "command", config_key="k-c")
    not_permitted: PinnedRoute = PinnedRoute("bedrock", "claude", config_key="k-b")

    def __post_init__(self) -> None:
        self.resolver.add(self.tenant, self.configured, org_key_route(self.configured, RecordedTransport()))
        self.resolver.refuse(self.tenant, self.unsupported, ProviderUnsupported("no transport"))
        self.resolver.refuse(self.tenant, self.not_permitted, AccountNotPermitted("cloud account"))


class TestStaticModelAccessResolver(ModelAccessResolverContract):
    @pytest.fixture
    def resolver_harness(self) -> _Resolver:
        return _Resolver()


class TestInMemorySpendCounter(SpendCounterContract):
    @pytest.fixture
    def spend_counter(self) -> InMemorySpendCounter:
        return InMemorySpendCounter()


def test_ng1_route_digest_equals_nodes() -> None:
    vector = json.loads(
        (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "model-routes.json").read_text()
    )
    routes = tuple(PinnedRoute(r["provider"], r["model"]) for r in vector["routes"])
    assert route_digest(routes) == vector["routesDigest"]
    assert route_digest(()) == vector["emptyDigest"] == grant_digest(())
    assert route_digest(routes) == grant_digest(r.route_id for r in routes)


def test_ad10_a_pinned_account_is_part_of_the_route_id_as_in_node() -> None:
    vector = json.loads(
        (Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "model-routes.json").read_text()
    )
    routes = tuple(
        PinnedRoute(
            r["provider"],
            r["model"],
            account=(
                AccountRef(AccountKind(r["access"]["accountKind"]), r["access"]["accountId"])
                if "accountId" in r["access"]
                else None
            ),
        )
        for r in vector["accountRoutes"]
    )
    assert [r.route_id for r in routes] == vector["accountRouteIds"]
    assert route_digest(routes) == vector["accountRoutesDigest"]
