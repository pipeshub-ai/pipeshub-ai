"""A1 RS: ``GuardedModelAccess`` enforces the vendor rules, the org policy, the engine's API and key ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.access.errors import (
    AccessNeedMissing,
    AccessPolicyUnavailable,
    AccountNotOwned,
    AccountNotPermitted,
    ModelApiNotPermitted,
    OfferingNotPermitted,
    OrgPolicyRefused,
    ProviderUnsupported,
    VendorRuleMissing,
    VendorRulesInvalid,
)
from harness_core.access.guard import GuardedModelAccess
from harness_core.access.memory import (
    InMemoryOrgAccessPolicies,
    StaticModelAccessResolver,
)
from harness_core.access.ports import ResolvedRoute
from harness_core.access.types import (
    AccessRoute,
    AccountKind,
    AccountRef,
    ModelApi,
    ModelNeed,
    PinnedRoute,
    RouteKind,
    user_account_ref,
)
from harness_core.access.vendor_rules import OrgAccessPolicy
from harness_core.testing.contracts.access_policies import OrgAccessPoliciesContract
from harness_core.testing.contracts.model_access import (
    ROUTE,
    ModelAccessResolverContract,
)
from harness_core.testing.model_gateway import RecordedTransport, org_key_route
from harness_engines.vendor_rules import load_vendor_rules, release_vendor_rules

if TYPE_CHECKING:
    from harness_core.access.types import Payer
    from harness_core.access.vendor_rules import Offering, VendorRules

TENANT = "org-1"
ENGINE_APIS = {
    "opencode": frozenset({ModelApi.CHAT_COMPLETIONS}),
    "codex": frozenset({ModelApi.RESPONSES}),
    "claude_code": frozenset({ModelApi.ANTHROPIC_MESSAGES}),
}
NEED = ModelNeed(actor="user-1", engine="opencode", api=ModelApi.CHAT_COMPLETIONS)


class CountingResolver(StaticModelAccessResolver):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def resolve(
        self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        self.calls += 1
        return await super().resolve(tenant, route, run_id=run_id, need=need)


def _resolved(route: PinnedRoute, kind: AccountKind, account_id: str, pays: Payer = "org") -> ResolvedRoute:
    return ResolvedRoute(
        access=AccessRoute(RouteKind.GATEWAY, ModelApi.CHAT_COMPLETIONS, route.provider, route.model, AccountRef(kind, account_id), pays),
        transport=RecordedTransport(),
        pricing_model=route.model,
    )


@dataclass
class Rig:
    inner: CountingResolver = field(default_factory=CountingResolver)
    policies: InMemoryOrgAccessPolicies = field(default_factory=InMemoryOrgAccessPolicies)
    offering: Offering = "oss"
    rules: VendorRules | None = None

    def guard(self) -> GuardedModelAccess:
        def rules() -> VendorRules:
            return self.rules if self.rules is not None else release_vendor_rules()

        return GuardedModelAccess(
            self.inner, rules=rules, policies=self.policies, offering=self.offering, engine_apis=ENGINE_APIS
        )

    def add(self, route: PinnedRoute, resolved: ResolvedRoute | None = None) -> PinnedRoute:
        self.inner.add(TENANT, route, resolved or org_key_route(route, RecordedTransport()))
        return route


OWN_KEY = PinnedRoute("openai", "gpt-x", account=user_account_ref("user-1", "openAI"))
OTHERS_KEY = PinnedRoute("openai", "gpt-x", account=user_account_ref("user-2", "openAI"))


async def test_rs1_an_org_key_route_resolves_through_the_inner_resolver_once() -> None:
    rig = Rig()
    rig.add(ROUTE)
    resolved = await rig.guard().resolve(TENANT, ROUTE, run_id="run-1", need=NEED)
    assert resolved.access.provider == "openai" and rig.inner.calls == 1


@pytest.mark.parametrize(
    ("route", "need", "refusal"),
    [
        (PinnedRoute("openai", "gpt-x", account=AccountRef(AccountKind.ORG_CLOUD, "cloud/a-1")), NEED, VendorRuleMissing),
        (
            PinnedRoute("anthropic", "claude-x", account=AccountRef(AccountKind.USER_PLAN_INJECTED, "users/user-1/anthropic")),
            ModelNeed("user-1", "claude_code", ModelApi.ANTHROPIC_MESSAGES),
            AccountNotPermitted,
        ),
    ],
    ids=["no-row", "prohibited-plan"],
)
async def test_rs2_a_refused_pinned_account_never_reaches_the_inner_resolver(
    route: PinnedRoute, need: ModelNeed, refusal: type[Exception]
) -> None:
    rig = Rig()
    rig.add(route)
    with pytest.raises(refusal) as refused:
        await rig.guard().resolve(TENANT, route, need=need)
    assert rig.inner.calls == 0
    assert refused.value.reason_code in {"vendor_rule_missing", "vendor_prohibited"}  # type: ignore[attr-defined]


async def test_rs3_another_users_key_is_refused_before_any_secret_is_read() -> None:
    rig = Rig()
    rig.add(OTHERS_KEY, _resolved(OTHERS_KEY, AccountKind.USER_API_KEY, OTHERS_KEY.account.account_id, "user"))  # type: ignore[union-attr]
    with pytest.raises(AccountNotOwned) as refused:
        await rig.guard().resolve(TENANT, OTHERS_KEY, need=NEED)
    assert refused.value.reason_code == "account_not_owned" and rig.inner.calls == 0
    rig.add(OWN_KEY, _resolved(OWN_KEY, AccountKind.USER_API_KEY, OWN_KEY.account.account_id, "user"))  # type: ignore[union-attr]
    assert (await rig.guard().resolve(TENANT, OWN_KEY, need=NEED)).access.pays == "user"


@pytest.mark.parametrize(
    "need",
    [
        ModelNeed("user-1", "codex", ModelApi.CHAT_COMPLETIONS),
        ModelNeed("user-1", "opencode", ModelApi.RESPONSES),
        ModelNeed("user-1", "jcode", ModelApi.CHAT_COMPLETIONS),
        ModelNeed("user-1", "loop", ModelApi.CHAT_COMPLETIONS),
    ],
    ids=["codex-on-chat", "opencode-on-responses", "unknown-engine", "loop"],
)
async def test_rs4_the_engine_must_speak_the_api(need: ModelNeed) -> None:
    rig = Rig()
    rig.add(ROUTE)
    with pytest.raises(ModelApiNotPermitted):
        await rig.guard().resolve(TENANT, ROUTE, need=need)
    assert rig.inner.calls == 0


@pytest.mark.parametrize(
    "need",
    [None, ModelNeed(None, "opencode", ModelApi.CHAT_COMPLETIONS), ModelNeed("user-1", None, ModelApi.CHAT_COMPLETIONS)],
    ids=["no-need", "no-actor", "no-engine"],
)
async def test_rs5_a_grant_that_does_not_say_who_or_which_engine_is_refused(need: ModelNeed | None) -> None:
    rig = Rig()
    rig.add(ROUTE)
    with pytest.raises(AccessNeedMissing):
        await rig.guard().resolve(TENANT, ROUTE, need=need)


async def test_rs6_the_rules_apply_to_what_the_inner_resolver_resolved() -> None:
    rig = Rig()
    route = PinnedRoute("ollama", "llama", config_key="k-o")
    rig.add(route)
    with pytest.raises(VendorRuleMissing):
        await rig.guard().resolve(TENANT, route, need=NEED)
    cloud = PinnedRoute("bedrock", "claude", config_key="k-b")
    rig.add(cloud, _resolved(cloud, AccountKind.ORG_CLOUD, "k-b"))
    assert (await rig.guard().resolve(TENANT, cloud, need=NEED)).access.account.kind is AccountKind.ORG_CLOUD  # type: ignore[union-attr]
    wrong_kind = PinnedRoute("openai", "gpt-x", config_key="k-oc")
    rig.add(wrong_kind, _resolved(wrong_kind, AccountKind.ORG_CLOUD, "k-oc"))
    with pytest.raises(VendorRuleMissing):
        await rig.guard().resolve(TENANT, wrong_kind, need=NEED)


async def test_rs7_the_org_policy_narrows_at_the_next_call() -> None:
    rig = Rig()
    rig.add(OWN_KEY, _resolved(OWN_KEY, AccountKind.USER_API_KEY, OWN_KEY.account.account_id, "user"))  # type: ignore[union-attr]
    guard = rig.guard()
    await guard.resolve(TENANT, OWN_KEY, need=NEED)
    rig.policies.set(TENANT, OrgAccessPolicy(frozenset({"openai-api-key-user"})))
    with pytest.raises(OrgPolicyRefused) as refused:
        await guard.resolve(TENANT, OWN_KEY, need=NEED)
    assert refused.value.detail == "openai-api-key-user"
    assert rig.inner.calls == 1


async def test_rs8_the_offering_is_checked() -> None:
    rig = Rig(offering="managed")
    route = PinnedRoute("anthropic", "claude-x", config_key="k-a")
    rig.add(route)
    need = ModelNeed("user-1", "claude_code", ModelApi.ANTHROPIC_MESSAGES)
    with pytest.raises(OfferingNotPermitted):
        await rig.guard().resolve(TENANT, route, need=need)
    rig.offering = "oss"
    assert (await rig.guard().resolve(TENANT, route, need=need)).access.provider == "anthropic"


async def test_rs9_a_policy_store_that_cannot_be_read_refuses() -> None:
    rig = Rig()
    rig.add(ROUTE)
    rig.policies.down = True
    with pytest.raises(AccessPolicyUnavailable):
        await rig.guard().resolve(TENANT, ROUTE, need=NEED)
    assert rig.inner.calls == 0


async def test_sg9_rules_that_fail_to_load_refuse_every_route() -> None:
    rig = Rig()
    rig.add(ROUTE)

    def broken() -> VendorRules:
        return load_vendor_rules(b'{"schema": "pipeshub.vendor-rules/1", "version": "x", "rules": []}', None)

    guard = GuardedModelAccess(rig.inner, rules=broken, policies=rig.policies, offering="oss", engine_apis=ENGINE_APIS)
    with pytest.raises(VendorRulesInvalid) as refused:
        await guard.resolve(TENANT, ROUTE, need=NEED)
    assert (refused.value.detail, refused.value.http_status, rig.inner.calls) == ("unsigned", 503, 0)


@dataclass
class _GuardedResolver:
    rig: Rig = field(default_factory=Rig)
    tenant: str = TENANT
    configured: PinnedRoute = ROUTE
    unsupported: PinnedRoute = PinnedRoute("cohere", "command", config_key="k-c")
    not_permitted: PinnedRoute = PinnedRoute("bedrock", "claude", config_key="k-b")

    def __post_init__(self) -> None:
        self.rig.add(self.configured)
        self.rig.inner.refuse(self.tenant, self.unsupported, ProviderUnsupported("no transport"))
        self.rig.inner.refuse(self.tenant, self.not_permitted, AccountNotPermitted("cloud account"))

    @property
    def resolver(self) -> GuardedModelAccess:
        return self.rig.guard()


class TestGuardedModelAccess(ModelAccessResolverContract):
    """RS-11: the port's contract holds for the guard over the static resolver."""

    @pytest.fixture
    def resolver_harness(self) -> _GuardedResolver:
        return _GuardedResolver()


@dataclass
class _Policies:
    policies: InMemoryOrgAccessPolicies = field(default_factory=InMemoryOrgAccessPolicies)

    async def write(self, tenant: str, policy: OrgAccessPolicy) -> None:
        self.policies.set(tenant, policy)

    def outage(self) -> None:
        self.policies.down = True


class TestInMemoryOrgAccessPolicies(OrgAccessPoliciesContract):
    @pytest.fixture
    def policies_harness(self) -> _Policies:
        return _Policies()
