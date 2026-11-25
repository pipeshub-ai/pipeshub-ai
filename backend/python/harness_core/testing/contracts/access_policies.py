"""Contract suite for ``OrgAccessPolicies`` (A1): each org's narrowing of the vendor rules.

Provide a ``policies_harness`` fixture (``PoliciesHarness``): the source under test,
``write(tenant, policy)`` (what the host's admin route does) and ``outage()`` (the
store stops answering reads).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY, OrgAccessPolicy

if TYPE_CHECKING:
    from harness_core.access.ports import OrgAccessPolicies


class PoliciesHarness(Protocol):
    @property
    def policies(self) -> OrgAccessPolicies: ...

    async def write(self, tenant: str, policy: OrgAccessPolicy) -> None: ...

    def outage(self) -> None: ...


class OrgAccessPoliciesContract:
    @pytest.fixture
    def policies_harness(self) -> PoliciesHarness:
        raise NotImplementedError("provide a policies_harness fixture")

    async def test_op1_an_org_without_a_policy_has_the_default(self, policies_harness: PoliciesHarness) -> None:
        policy = await policies_harness.policies.policy("org-op-none")
        assert policy == DEFAULT_ORG_POLICY
        assert not policy.consumer_plans and not policy.disabled_rules

    async def test_op2_a_written_policy_is_read_back_for_that_org_only(self, policies_harness: PoliciesHarness) -> None:
        narrowed = OrgAccessPolicy(frozenset({"openai-api-key-user"}), consumer_plans=True)
        await policies_harness.write("org-op-a", narrowed)
        assert await policies_harness.policies.policy("org-op-a") == narrowed
        assert await policies_harness.policies.policy("org-op-b") == DEFAULT_ORG_POLICY

    async def test_op3_a_store_that_cannot_be_read_refuses(self, policies_harness: PoliciesHarness) -> None:
        await policies_harness.write("org-op-c", OrgAccessPolicy(frozenset({"gemini-api-key-user"})))
        policies_harness.outage()
        with pytest.raises(AccessPolicyUnavailable) as refused:
            await policies_harness.policies.policy("org-op-c")
        assert refused.value.http_status == 503


__all__ = ["OrgAccessPoliciesContract", "PoliciesHarness"]
