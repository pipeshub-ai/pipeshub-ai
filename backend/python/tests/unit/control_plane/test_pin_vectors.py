"""CPD-5: pin-replay determinism across languages (CPD5-23) and the Python ``RunConfigPinner`` contract on the
reference adapter (CPD5-24). Node reads the same vectors (``pin-vectors.test.ts``).

CPD10-87: ``resolved-run-config.v0.json`` holds the pins the tree before CPD-10 made. A stored pin is never
resolved or digested again, so each must still validate, match its digest as stored, and answer a run's policy."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from app.control_plane.runtime.deployment import DeploymentLayer
from app.control_plane.runtime.pinning import (
    REFUSAL_CODES,
    PinSubject,
    assemble,
    inherited_dimensions,
    parent_config_layer,
    parent_layer,
    pin_digest,
    pin_scope,
    release_layer,
)
from app.control_plane.testing.pinning import (
    InMemoryRunConfigPinner,
    RunConfigPinnerContract,
)
from app.harness.run_policy.pinned import PinnedRunPolicy
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.logs import HarnessLog
from harness_core.policy.resolver import DimensionPolicyResolver
from harness_core.policy.types import ScopedLayer

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from app.control_plane.testing.pinning import SnapshotSource
    from app.harness.assembly.context import RunContext

VECTORS = (
    Path(__file__).resolve().parents[5]
    / "contracts"
    / "control-plane"
    / "vectors"
    / "resolved-run-config.json"
)
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
V0 = [
    case
    for case in json.loads(VECTORS.with_name("resolved-run-config.v0.json").read_text(encoding="utf-8"))["cases"]
    if "stored" in case
]


def _shuffled(value: JsonValue, rng: random.Random) -> JsonValue:
    if isinstance(value, dict):
        keys = list(value)
        rng.shuffle(keys)
        return {key: _shuffled(value[key], rng) for key in keys}
    return value


def _pin(
    case: dict[str, JsonValue], rng: random.Random | None = None
) -> dict[str, object]:
    layers = cast("list[dict[str, JsonValue]]", case["layers"])
    fields = cast("dict[str, JsonValue]", case["pin"])
    if rng is not None:
        layers = [
            cast("dict[str, JsonValue]", _shuffled(layer, rng)) for layer in layers
        ]
        fields = cast("dict[str, JsonValue]", _shuffled(fields, rng))
    parent = cast("dict[str, dict[str, JsonValue]] | None", case.get("parent"))
    inherited_from = [] if parent is None else [ScopedLayer("parent", parent_config_layer(parent["effectiveConfig"]))]
    chain = [release_layer(), *inherited_from] + [
        ScopedLayer(cast("dict[str, str]", layer["scope"])["kind"], layer)
        for layer in layers
    ]
    resolved = DimensionPolicyResolver().resolve(chain)
    if resolved.refusal is not None:
        refusal = resolved.refusal
        return {
            "refusal": {
                "code": REFUSAL_CODES[refusal.code],
                "dimension": refusal.dimension,
                "layer": refusal.layer,
            }
        }
    if parent is None:
        pin = assemble({"schemaVersion": 1, **fields, "effectiveConfig": cast("JsonValue", resolved.config)})
        return {"configDigest": resolved.digest, "digest": pin.digest.root}
    assert resolved.config is not None
    config = inherited_dimensions(resolved.config, parent["effectiveConfig"])
    # A vector with a ``subject`` has its scope computed, as the pinner does: the owner is the parent pin's.
    scope = {} if "subject" not in case else {"scope": _child_scope(case, parent)}
    # The parent's model, unless the child's role set its own (MA1): then the case's pin names that entry.
    pin = assemble(
        {"schemaVersion": 1, "llm": parent["llm"], **fields, **scope, "effectiveConfig": cast("JsonValue", config)}
    )
    return {
        "configDigest": canonical_digest(config),
        "digest": pin.digest.root,
        **scope,
        "inherited": sorted(
            path
            for path, entry in cast("dict[str, JsonValue]", config["dimensions"]).items()
            if entry != cast("dict[str, JsonValue]", resolved.config["dimensions"]).get(path)
        ),
    }


def _child_scope(case: dict[str, JsonValue], parent: dict[str, dict[str, JsonValue]]) -> dict[str, JsonValue]:
    subject = cast("dict[str, str | None]", case["subject"])
    held = ResolvedRunConfig.model_validate(
        {
            "schemaVersion": 1,
            "headRevision": 1,
            "routing": {"path": "durable", "reason": None},
            "scope": parent["scope"],
            "effectiveSet": {"items": [], "digest": canonical_digest([])},
            "effectiveConfig": parent["effectiveConfig"],
            "llm": parent["llm"],
            "deploymentDigest": canonical_digest({}),
            "digest": canonical_digest({}),
        }
    )
    return pin_scope(
        PinSubject(
            org_id="o1",
            actor_id="u3",
            agent_key=subject["agentKey"],
            project_id=subject["projectId"],
            agent_owner=subject["agentOwner"],
            chat_mode="auto",
            surface="child",
            request={},
            parent=held,
        )
    )


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_cpd5_23_every_vector_pins_its_digest(case: dict[str, JsonValue]) -> None:
    assert _pin(case) == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_cpd5_23_member_order_never_changes_the_pin(case: dict[str, JsonValue]) -> None:
    for seed in range(5):
        assert _pin(case, random.Random(seed)) == case["expected"]


def test_cpd5_23_the_digest_covers_every_member_but_itself() -> None:
    case = next(case for case in CASES if "digest" in case["expected"])
    fields: dict[str, JsonValue] = {
        "schemaVersion": 1,
        **case["pin"],
        "effectiveConfig": {"schemaVersion": 1, "dimensions": {}},
    }
    pin = assemble(fields)
    assert (
        pin_digest(cast("dict[str, JsonValue]", dump_contract(pin))) == pin.digest.root
    )
    moved = assemble({**fields, "headRevision": 42})
    assert moved.digest != pin.digest


def test_cpd5_23_a_parent_layer_carries_the_parents_values_where_a_layer_sets_them() -> (
    None
):
    parent = ResolvedRunConfig.model_validate(
        {
            "schemaVersion": 1,
            "headRevision": 1,
            "routing": {"path": "durable", "reason": None},
            "scope": {"agentKey": None, "projectId": None},
            "effectiveSet": {"items": [], "digest": "sha256:" + "0" * 64},
            "effectiveConfig": {
                "schemaVersion": 1,
                "dimensions": {
                    "budgets.maxTurns": {"value": 10, "setBy": "agent", "ceilings": []},
                    "engineProfile.engine": {
                        "value": {"kind": "opencode"},
                        "setBy": "release",
                        "ceilings": [],
                    },
                },
            },
            "llm": None,
            "deploymentDigest": "sha256:" + "0" * 64,
            "digest": "sha256:" + "0" * 64,
        }
    )
    layer = parent_layer(parent)
    assert layer["budgets"] == {"maxTurns": 10}
    assert "engineProfile" not in layer


class TestInMemoryRunConfigPinner(RunConfigPinnerContract):
    @pytest.fixture
    def pinner_factory(self) -> Callable[[SnapshotSource], object]:
        return InMemoryRunConfigPinner


def _policy(stored: dict[str, JsonValue]) -> PinnedRunPolicy:
    pin = validate_contract(ResolvedRunConfig, stored)
    run = cast("RunContext", SimpleNamespace(org_id="o1", agent=None))
    return PinnedRunPolicy(run, pin, deployment=DeploymentLayer(), live=None, log=HarnessLog.named("tests.cpd10.v0"))


def test_cpd10_87_the_old_vectors_hold_every_pin_the_old_tree_made() -> None:
    assert [case["id"] for case in V0] == ["PV-1", "PV-3", "PV-4", "PV-5", "PV-7", "PV-8"]


@pytest.mark.parametrize("case", V0, ids=[case["id"] for case in V0])
async def test_cpd10_87_a_pin_made_before_the_upgrade_validates_and_runs_as_pinned(case: dict[str, JsonValue]) -> None:
    stored = cast("dict[str, JsonValue]", case["stored"])
    expected = cast("dict[str, JsonValue]", case["expected"])

    assert pin_digest(stored) == stored["digest"] == expected["digest"]
    policy = _policy(stored)

    dimensions = cast("dict[str, dict[str, JsonValue]]", cast("dict[str, JsonValue]", stored["effectiveConfig"])["dimensions"])
    assert policy.stages() == {
        path.removeprefix("rollout."): entry["value"] for path, entry in dimensions.items() if path.startswith("rollout.")
    }
    assert not any(path.startswith("rollout.durable.") for path in dimensions)
    pinned_flow = cast("dict[str, list[dict[str, str]]]", dimensions.get("approvals", {}).get("value") or {}).get("flow", [])
    approvals = await policy.approvals()
    assert {(rule.level, rule.above) for rule in approvals.flow_rules} == {(rule["level"], rule["above"]) for rule in pinned_flow}


def test_cpd10_9_a_pins_stages_are_its_switches_not_its_routing_lists() -> None:
    case = next(case for case in CASES if "inherited" in case["expected"])
    config = cast("dict[str, JsonValue]", case["parent"]["effectiveConfig"])
    held = cast("dict[str, JsonValue]", config["dimensions"])
    assert "rollout.durable.modes" in held and "rollout.durable.modelFamilies" in held
    pin = assemble({"schemaVersion": 1, **case["pin"], "scope": {"agentKey": "a7", "projectId": None}, "llm": None, "effectiveConfig": config})

    stages = _policy(cast("dict[str, JsonValue]", dump_contract(pin))).stages()

    assert stages and all(name.startswith(("ENABLE_", "AGENT_HARNESS_", "CONTROL_PLANE_")) for name in stages)
    assert all(isinstance(on, bool) for on in stages.values())
