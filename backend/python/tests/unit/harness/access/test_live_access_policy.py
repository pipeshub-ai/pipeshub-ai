"""MAC-G2 (CPD-6): the model gateway's org narrowing from the live layer, on the ``OrgAccessPolicies`` contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.control_plane.contracts.generated.change_event import ChangeEvent
from app.control_plane.testing.memory import InMemoryLiveLayerCache
from app.harness.access.live_policies import LiveOrgAccessPolicies
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY, OrgAccessPolicy
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.access_policies import OrgAccessPoliciesContract

if TYPE_CHECKING:
    from pydantic import JsonValue

ORG = "org-live"


def _models(policy: OrgAccessPolicy) -> JsonValue:
    return {
        "layer": {
            "schemaVersion": 1,
            "scope": {"kind": "live"},
            "models": {"disabledRules": sorted(policy.disabled_rules), "consumerPlans": policy.consumer_plans},
        }
    }


@dataclass
class _Admin:
    """The admin route as the worker sees it: the live route answers the new rows and an event names the org."""

    clock: ManualClock = field(default_factory=ManualClock)
    revisions: dict[str, int] = field(default_factory=dict[str, int])
    notifier_up: bool = True

    def __post_init__(self) -> None:
        self.cache = InMemoryLiveLayerCache(self.clock)
        self.policies = LiveOrgAccessPolicies(self.cache)

    async def write(self, tenant: str, policy: OrgAccessPolicy) -> None:
        revision = self.revisions.get(tenant, 0) + 1
        self.revisions[tenant] = revision
        self.cache.source.put(tenant, revision, _models(policy))
        if self.notifier_up:
            self.cache.on_change(
                ChangeEvent.model_validate(
                    {"type": "cp.changed", "orgId": tenant, "revision": revision, "previous": revision - 1,
                     "touched": [{"kind": "policy-section", "id": "models"}], "narrowing": False}
                )
            )

    def outage(self) -> None:
        self.cache.source.unreachable = True


class TestLiveOrgAccessPolicies(OrgAccessPoliciesContract):
    @pytest.fixture
    def policies_harness(self) -> _Admin:
        return _Admin()


OFF = OrgAccessPolicy(frozenset({"openai-api-key-user"}))


async def test_mac_g2_a_row_switched_off_reaches_the_gateway_s_next_call_with_the_notifier() -> None:
    admin = _Admin()
    assert await admin.policies.policy(ORG) == DEFAULT_ORG_POLICY

    await admin.write(ORG, OFF)

    assert await admin.policies.policy(ORG) == OFF


async def test_mac_g2_without_the_notifier_it_arrives_by_the_time_to_live() -> None:
    admin = _Admin(notifier_up=False)
    _ = await admin.policies.policy(ORG)

    await admin.write(ORG, OFF)
    _ = admin.clock.advance(29.0)
    before = await admin.policies.policy(ORG)
    _ = admin.clock.advance(1.0)

    assert before == DEFAULT_ORG_POLICY
    assert await admin.policies.policy(ORG) == OFF


async def test_mac_g2_an_outage_serves_the_held_rows_for_the_time_to_live_then_refuses() -> None:
    admin = _Admin()
    await admin.write(ORG, OFF)
    _ = await admin.policies.policy(ORG)

    admin.outage()
    _ = admin.clock.advance(29.0)
    held = await admin.policies.policy(ORG)
    _ = admin.clock.advance(1.0)

    assert held == OFF
    with pytest.raises(AccessPolicyUnavailable):
        _ = await admin.policies.policy(ORG)


async def test_mac_g2_a_fresh_layer_costs_the_gateway_no_request_per_call() -> None:
    admin = _Admin()
    await admin.write(ORG, OFF)

    for _ in range(100):
        _ = await admin.policies.policy(ORG)

    assert len(admin.cache.source.calls) == 1
