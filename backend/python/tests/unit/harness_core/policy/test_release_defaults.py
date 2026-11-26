"""CPD3-50..CPD3-54 (POL-24, R2 F10): every default the release ships is ``contracts/harness/core/policy/release.json``,
read through the generated ``RELEASE_DEFAULTS``; no module keeps a copy of it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agents.run_definition import MAX_CHILDREN_PER_RUN
from app.harness.access.offering import DEFAULT_OFFERING, parse_offering
from harness_core.access.types import AccessRouteRef, AccountKind
from harness_core.contracts import dump_contract
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.profiles import profile_json
from harness_core.engines.types import (
    GATEWAY_ACCOUNT_KINDS,
    ProfileApprovals,
    SubAgentPolicy,
)
from harness_core.memories.memory import MAX_MEMORY_CHARS
from harness_engines.release import release_allowlist, release_profiles

REPO = Path(__file__).resolve().parents[6]
RELEASE: dict[str, Any] = json.loads((REPO / "contracts/harness/core/policy/release.json").read_text(encoding="utf-8"))
ALLOWLIST: dict[str, Any] = json.loads((REPO / "backend/python/harness_engines/allowlist.json").read_text(encoding="utf-8"))
FLAGS: dict[str, Any] = json.loads((REPO / "contracts/harness/flags.json").read_text(encoding="utf-8"))


def test_release_defaults_is_release_json() -> None:
    assert dump_contract(RELEASE_DEFAULTS) == RELEASE


def test_cpd3_50_the_children_bound_is_the_release_one() -> None:
    assert MAX_CHILDREN_PER_RUN == RELEASE["children"]["maxPerRun"]


def test_cpd3_51_the_memory_bound_is_the_release_one() -> None:
    assert MAX_MEMORY_CHARS == RELEASE["memory"]["maxChars"]


def test_cpd3_52_the_offering_default_is_the_release_one_and_the_flag_matrix_says_so() -> None:
    assert DEFAULT_OFFERING == parse_offering(None) == RELEASE["models"]["offering"]
    row = next(flag for flag in FLAGS["flags"] if flag["name"] == "AGENT_HARNESS_OFFERING")
    assert row["default"] == RELEASE["models"]["offering"]


def test_cpd3_54_every_release_profile_is_an_allowlist_engine_with_the_release_profile() -> None:
    assert "defaultProfile" not in ALLOWLIST
    access = AccessRouteRef("route", "digest")
    engines = [{"kind": e.version.kind, "version": e.version.version, "binaryDigest": e.version.binary_digest} for e in release_allowlist()]
    assert [profile_json(p) for p in release_profiles(access)] == [{**RELEASE["engineProfile"], "engine": e} for e in engines]


def test_cpd3_54_the_profile_dataclass_defaults_that_equal_a_release_value_are_read_from_it() -> None:
    profile = RELEASE["engineProfile"]
    assert ProfileApprovals().as_mapping() == profile["approvals"]
    sub_agents = SubAgentPolicy()
    assert {"enabled": sub_agents.enabled, "maxDepth": sub_agents.max_depth, "maxConcurrent": sub_agents.max_concurrent} == profile["subAgents"]
    assert GATEWAY_ACCOUNT_KINDS == {AccountKind(kind) for kind in profile["accountKinds"]}
