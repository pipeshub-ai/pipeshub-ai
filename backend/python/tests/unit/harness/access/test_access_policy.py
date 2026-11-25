"""A1 OP-1..OP-4: ``ConfiguredOrgAccessPolicies`` through the production ``ConfigurationService``."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.harness.unpinned import ConfiguredOrgAccessPolicies
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY, OrgAccessPolicy
from harness_core.testing.contracts.access_policies import OrgAccessPoliciesContract
from tests.unit.harness.store_outage import outage_configuration_service

PATH = config_node_constants.AGENT_HARNESS_MODEL_ACCESS.value


@dataclass
class _Store:
    """A key-value store holding plain documents; ``down`` fails reads as the encrypted stores do."""

    values: dict[str, Any] = field(default_factory=dict)
    down: bool = False

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if self.down:
            if raise_on_error:
                raise ConnectionError(f"kv down reading {key}")
            return None
        return self.values.get(key)


def _service(store: _Store) -> ConfigurationService:
    config = object.__new__(ConfigurationService)
    config.logger = logging.getLogger("tests.access_policy")
    config.store = store  # pyright: ignore[reportAttributeAccessIssue]
    config.cache = {}  # pyright: ignore[reportAttributeAccessIssue]
    config._cache_lock = threading.Lock()  # main's ConfigurationService guards its LRU cache with a lock
    config._cached_at = {}  # and keeps when each entry was stored (max_age_seconds)
    return config


@dataclass
class _Configured:
    store: _Store = field(default_factory=_Store)

    @property
    def policies(self) -> ConfiguredOrgAccessPolicies:
        return ConfiguredOrgAccessPolicies(_service(self.store))

    async def write(self, tenant: str, policy: OrgAccessPolicy) -> None:
        orgs = self.store.values.setdefault(PATH, {"orgs": {}})["orgs"]
        orgs[tenant] = {
            "disabledRules": sorted(policy.disabled_rules),
            "consumerPlans": policy.consumer_plans,
            "updatedAt": 1,
            "updatedBy": "p_admin",
        }

    def outage(self) -> None:
        self.store.down = True


class TestConfiguredOrgAccessPolicies(OrgAccessPoliciesContract):
    @pytest.fixture
    def policies_harness(self) -> _Configured:
        return _Configured()


async def test_op3_the_production_service_over_a_down_store_refuses() -> None:
    with pytest.raises(AccessPolicyUnavailable):
        await ConfiguredOrgAccessPolicies(outage_configuration_service()).policy("org-1")


@pytest.mark.parametrize(
    "document",
    [
        {"orgs": {"org-1": {"disabledRules": "openai-api-key-user"}}},
        {"orgs": {"org-1": {"disabledRules": [1]}}},
        {"orgs": {"org-1": {"consumerPlans": "yes"}}},
        {"orgs": {"org-1": {"enabledRules": ["anthropic-claude-plan-injected"]}}},
        {"orgs": ["org-1"]},
        ["not", "an", "object"],
    ],
    ids=["rules-not-list", "rule-not-string", "consumer-not-bool", "unknown-field", "orgs-not-object", "not-object"],
)
async def test_op4_a_malformed_policy_refuses_never_reads_as_default(document: object) -> None:
    store = _Store({PATH: document})
    with pytest.raises(AccessPolicyUnavailable):
        await ConfiguredOrgAccessPolicies(_service(store)).policy("org-1")


async def test_op1_an_absent_orgs_map_and_another_orgs_entry_read_as_the_default() -> None:
    assert await ConfiguredOrgAccessPolicies(_service(_Store({PATH: {}}))).policy("org-1") == DEFAULT_ORG_POLICY
    other = _Store({PATH: {"orgs": {"org-2": {"disabledRules": ["openai-api-key-user"]}}}})
    assert await ConfiguredOrgAccessPolicies(_service(other)).policy("org-1") == DEFAULT_ORG_POLICY
