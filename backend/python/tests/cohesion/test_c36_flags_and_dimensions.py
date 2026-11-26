"""C-36 (05-migration-plan §6.3, POL-23; CPD-3, CP8 extends): flags and policy dimensions name each other.

Every rollout and operator row of ``policy-dimensions.json`` is a ``flags.json`` row whose ``dimension`` names it
back; every Labs flag is a rollout dimension; an operator row is an env flag of the deployment; and the policy
layer's ``rollout`` and ``operator`` sections hold exactly those rows. A flag that becomes policy without a
dimension, or a dimension that names no flag, fails here.

A rollout row that no switch sets (CPD-10: routing lists, the capability-migration marker) is listed in
``POLICY_ONLY`` by name with what does set it; nothing else may go without a flag, and no flag may name one.
"""

from __future__ import annotations

import json
from typing import Any

from tests.cohesion.support import (
    REPO_ROOT,
    contract_schema,
    policy_dimensions,
    with_overlay,
)

CONTRACTS = REPO_ROOT / "contracts" / "harness"
FLAGS: list[dict[str, Any]] = with_overlay(
    json.loads((CONTRACTS / "flags.json").read_text(encoding="utf-8")), "flags.json"
)["flags"]
POLICY = CONTRACTS / "core" / "policy"
DIMENSIONS: list[dict[str, Any]] = policy_dimensions()
LAYER: dict[str, Any] = contract_schema(POLICY / "policy-layer.schema.json")
SECTIONS = ("rollout", "operator")
# Rollout rows no Labs switch, env variable or KV flag sets, and what sets each instead.
POLICY_ONLY = {
    "rollout.durable.modes": "the release layer; a deployment or org layer narrows it",
    "rollout.durable.assistantModes": "the release layer; a deployment or org layer narrows it",
    "rollout.durable.modelFamilies": "the platform settings' agentHarness.modelFamilies (a list, not a switch), folded into the org layer",
    "rollout.CONTROL_PLANE_CAPABILITIES": "the capability migration's ledger, folded into the org layer",
}


def _section_rows() -> list[dict[str, Any]]:
    return [d for d in DIMENSIONS if d["path"].split(".")[0] in SECTIONS or d["kind"] == "operator"]


def _flag_rows() -> list[dict[str, Any]]:
    return [row for row in _section_rows() if row["path"] not in POLICY_ONLY]


def _leaves(schema: dict[str, Any], prefix: str = "") -> set[str]:
    """A section's members by path: a member that is an object of the layer schema holds its own."""
    found: set[str] = set()
    for name, member in schema["properties"].items():
        ref = member.get("$ref", "")
        if ref.startswith("#/$defs/"):
            found |= _leaves(LAYER["$defs"][ref.removeprefix("#/$defs/")], f"{prefix}{name}.")
        else:
            found.add(f"{prefix}{name}")
    return found


def test_c36_a_row_without_a_flag_is_listed_by_name_and_no_flag_names_it() -> None:
    flagless = {row["path"] for row in _section_rows() if "flag" not in row}
    assert flagless == set(POLICY_ONLY)
    assert all(path.startswith("rollout.") for path in POLICY_ONLY)
    named = {flag["dimension"] for flag in FLAGS if "dimension" in flag}
    assert named.isdisjoint(POLICY_ONLY)
    by_path = {row["path"]: row for row in DIMENSIONS}
    for path in POLICY_ONLY:
        assert by_path[path]["exposure"] == ["deployment", "org"], path


def test_c36_every_rollout_and_operator_dimension_is_a_flag_that_names_it_back() -> None:
    by_name = {flag["name"]: flag for flag in FLAGS}
    for row in _flag_rows():
        assert "flag" in row, row["path"]
        flag = by_name.get(row["flag"])
        assert flag is not None, f"{row['path']}: {row['flag']} is not in flags.json"
        assert flag.get("dimension") == row["path"], row["path"]
        assert row["path"] == f"{row['path'].split('.')[0]}.{row['flag']}", row["path"]


def test_c36_every_flag_that_names_a_dimension_has_it() -> None:
    by_path = {row["path"]: row for row in DIMENSIONS}
    for flag in FLAGS:
        if "dimension" in flag:
            row = by_path.get(flag["dimension"])
            assert row is not None, f"{flag['name']}: no dimension {flag['dimension']}"
            assert row.get("flag") == flag["name"], flag["name"]


def test_c36_every_labs_flag_is_a_rollout_dimension_and_every_operator_row_a_deployment_env_flag() -> None:
    by_name = {flag["name"]: flag for flag in FLAGS}
    # The kill switch is a rollout dimension the operator sets from env for the whole instance (CTL-5).
    kill_switches = {f["name"] for f in FLAGS if f["kind"] == "env" and "killSwitchFor" in f}
    assert {f["name"] for f in FLAGS if f["kind"] == "labs"} | kill_switches == {
        row["flag"] for row in _flag_rows() if row["path"].startswith("rollout.")
    }
    for row in _flag_rows():
        if row["kind"] == "operator":
            flag = by_name[row["flag"]]
            assert (flag["kind"], flag["scope"]) == ("env", "deployment"), row["path"]
            assert row["exposure"] == ["deployment"], row["path"]


def test_c36_the_layer_sections_hold_exactly_the_flag_dimensions() -> None:
    for section in SECTIONS:
        properties = _leaves(LAYER["$defs"][f"{section.capitalize()}Policy"])
        rows = {row["path"].split(".", 1)[1] for row in _section_rows() if row["path"].startswith(f"{section}.")}
        assert properties == rows, section
