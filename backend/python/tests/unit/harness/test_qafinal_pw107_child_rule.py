"""QAFINAL-FU-PW107: whose rule a deep child's approval card names.

On ST-1 (PW-107, PW-113: deep mode, the org's ``writes: ask``, the flow rule off) the coordinator's pin held the
org's ceiling (``setBy: org``) and the child's held one ceiling, the ``parent`` layer's restatement (``setBy:
parent``); the card took its layer from ``setBy`` and named anything but ``org``/``agent`` the deployment's, so the
person read "Your administrator requires approval" for their organization's rule. No deployment rule asked.
The entries below are the two pins of that run, as Mongo held them.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.control_plane.runtime.deployment import DeploymentLayer
from app.control_plane.runtime.pinning import (
    MAX_CEILINGS,
    inherited_dimensions,
    with_parent_ceilings,
)
from app.harness.run_policy.resolved import (
    resolved_approvals,
    resolved_engine_governance,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import EffectKind, ToolEffect
from harness_core.kernel.core.messages import ToolCall

REPO = Path(__file__).resolve().parents[5]
WRITE_ASK = {"classes": {"execute": "ask", "read": "allow", "workspace": "allow", "write": "ask"}, "tools": {}}
# PW-107's coordinator (073c9e07) and its child (92a894d7) before the fix.
PARENT_APPROVALS = {"ceilings": [{"layer": "org", "value": {"writes": "ask"}}], "setBy": "org", "value": WRITE_ASK}
CHILD_APPROVALS = {"ceilings": [{"layer": "parent", "value": WRITE_ASK}], "setBy": "parent", "value": WRITE_ASK}
TICKET = "mcp@20fe9b37-41f6-4024-b2d2-eea852e11631/create_ticket"


def _effect(kind: EffectKind = "write", tool_id: str = TICKET) -> ToolEffect:
    return ToolEffect(tool_id, kind, False, SinkDescriptor(egress="external", args_leave_boundary=True))


def _reason(approvals: dict[str, Any], effect: ToolEffect | None = None) -> tuple[object, object] | None:
    policy = resolved_approvals({"approvals": approvals}, DeploymentLayer()).rules
    asked = policy.ask_reason(ToolCall(id="c1", name="create_ticket"), effect or _effect())
    return None if asked is None else (asked["rule"], asked["layer"])


def _child_of(parent_entry: dict[str, Any], child_entry: dict[str, Any], path: str = "approvals") -> dict[str, Any]:
    child = {"schemaVersion": 1, "dimensions": {path: child_entry}}
    parent = {"schemaVersion": 1, "dimensions": {path: parent_entry}}
    return inherited_dimensions(child, parent)["dimensions"][path]


def test_pw107_c1_the_coordinator_names_the_orgs_rule_and_the_childs_pin_now_keeps_it() -> None:
    assert _reason(PARENT_APPROVALS) == ("writes", "org")
    assert _reason(CHILD_APPROVALS) == ("writes", "deployment"), "the old child pin: no provenance, the fallback"
    child = _child_of(PARENT_APPROVALS, CHILD_APPROVALS)
    assert child["ceilings"] == [{"layer": "org", "value": {"writes": "ask"}}, CHILD_APPROVALS["ceilings"][0]]
    assert child["setBy"] == "parent", "who set the child's value is still its parent (MA1's setBy)"
    assert _reason(child) == ("writes", "org")


def test_pw107_c2_a_grandchild_keeps_the_roots_provenance_once() -> None:
    child = _child_of(PARENT_APPROVALS, CHILD_APPROVALS)
    grandchild = _child_of(child, copy.deepcopy(CHILD_APPROVALS))
    assert [c["layer"] for c in grandchild["ceilings"]] == ["org", "parent"]
    assert _reason(grandchild) == ("writes", "org")


@pytest.mark.parametrize(("parent_ceilings", "child_ceilings", "kept"), [
    ([{"layer": "release", "value": "ask"}], [{"layer": "release", "value": "ask"}, {"layer": "parent", "value": "ask"}],
     ["release", "parent"]),
    ([{"layer": "org", "value": "ask"}], [{"layer": "release", "value": "ask"}], ["release"]),
    ([{"layer": "release", "value": "ask"}, {"layer": "org", "value": "ask"}],
     [{"layer": "release", "value": "ask"}, {"layer": "parent", "value": "ask"}, {"layer": "request", "value": "ask"}],
     ["release", "org", "parent", "request"]),
])
def test_pw107_c3_only_the_parents_own_layers_go_before_the_parent_ceiling(
    parent_ceilings: list[dict[str, Any]], child_ceilings: list[dict[str, Any]], kept: list[str]
) -> None:
    entry = {"value": "ask", "setBy": "parent", "ceilings": child_ceilings}
    held = with_parent_ceilings(entry, {"value": "ask", "setBy": "org", "ceilings": parent_ceilings})
    assert [c["layer"] for c in held["ceilings"]] == kept


def test_pw107_c4_a_parent_with_too_many_ceilings_leaves_the_childs_as_it_was() -> None:
    parent = {"value": "ask", "setBy": "agent", "ceilings": [{"layer": "org", "value": "ask"}] * MAX_CEILINGS}
    entry = {"value": "ask", "setBy": "parent", "ceilings": [{"layer": "parent", "value": "ask"}]}
    assert with_parent_ceilings(entry, parent) is entry


def test_pw107_c5_the_first_layer_that_asks_is_named_not_the_last_that_wrote() -> None:
    """policy-resolve.json case 12: the org asks before writes, the agent restates ``writes: allow`` and denies one
    tool; ``setBy`` is the agent's, the writes rule the org's."""
    case = json.loads((REPO / "contracts/harness/vectors/policy-resolve.json").read_text())["cases"][12]
    entry = case["expect"]["config"]["dimensions"]["approvals"]
    assert entry["setBy"] == "agent"
    assert _reason(entry) == ("writes", "org")
    both = {**PARENT_APPROVALS, "ceilings": [{"layer": "deployment", "value": {"writes": "ask"}}, *PARENT_APPROVALS["ceilings"]]}
    assert _reason(both) == ("writes", "deployment"), "the operator's default comes first in precedence"
    tool_ask = {**PARENT_APPROVALS, "ceilings": [*PARENT_APPROVALS["ceilings"], {"layer": "agent", "value": {"tools": {TICKET: "ask"}}}],
                "value": {**WRITE_ASK, "tools": {TICKET: "ask"}}}
    assert _reason(tool_ask, _effect("read")) == ("tool", "agent")


def _approval_entries() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            held = node.get("approvals")
            if isinstance(held, dict) and "ceilings" in held and "setBy" in held:
                found.append(held)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for name in ("contracts/harness/vectors/policy-resolve.json", "contracts/control-plane/vectors/resolved-run-config.json",
                 "contracts/control-plane/vectors/child-owner-pin.json"):
        walk(json.loads((REPO / name).read_text()))
    return found


def test_pw107_c6_the_ceilings_decide_nothing_the_resolved_value_does_not() -> None:
    entries = _approval_entries()
    assert len(entries) >= 3
    for entry in entries:
        only = {**entry, "ceilings": []}
        for kind in ("read", "workspace", "write", "execute"):
            for tool in (TICKET, "jira.create_issue"):
                call, effect = ToolCall(id="c", name="t"), _effect(kind, tool)  # type: ignore[arg-type]
                with_ceilings = resolved_approvals({"approvals": entry}, DeploymentLayer()).rules(call, effect)
                assert with_ceilings == resolved_approvals({"approvals": only}, DeploymentLayer()).rules(call, effect)


def test_pw107_c7_a_childs_engine_governance_holds_the_class_its_parents_org_named() -> None:
    """The same provenance decides which engine classes are the org's rule (QA10-F3): a code_task engine child held
    only ``release`` and ``parent`` ceilings, so the org's named ``write: ask`` read as the release default."""
    from harness_core.testing.engines.durable import engine_profile

    parent = {"value": "ask", "setBy": "org", "ceilings": [{"layer": "release", "value": "ask"}, {"layer": "org", "value": "ask"}]}
    old = {"value": "ask", "setBy": "parent", "ceilings": [{"layer": "release", "value": "ask"}, {"layer": "parent", "value": "ask"}]}
    path = "engineProfile.approvals.write"
    approvals = resolved_approvals({}, DeploymentLayer())
    profile = engine_profile()

    def org_named(entry: dict[str, Any]) -> bool:
        governance = resolved_engine_governance({path: entry}, approvals, profile)
        return governance.scopes[-1] == "org" and governance.layers[-1].classes.get("write") == "ask"

    assert not org_named(old)
    assert org_named(_child_of(parent, old, path))
