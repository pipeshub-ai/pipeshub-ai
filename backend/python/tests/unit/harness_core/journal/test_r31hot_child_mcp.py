"""R31-HOT, ADR-OWD-07 D-LAZY-8: a child keeps the MCP server its parent holds whole, on the table Node runs too (CM-*).

The reference journal plays Node in kernel tests, so it pins a child's MCP grant by the same rule: whole stays whole
when the request names no tools (a code_task job) or names the server whole; named tools of it are pinned to exactly
those; nothing the parent does not hold is granted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import ChildGrantRequest, child_grants, child_spec

TABLE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/child-mcp.json").read_text(encoding="utf-8")
)
PARENT = validate_contract(RunSpec, TABLE["parentSpec"])


def _parent(case: dict[str, Any]) -> RunSpec:
    """The table's parent, its own MCP entries pinned as ``parentSpecPins`` says (R31HOT2: a child that inherited a
    pin, spawning in turn)."""
    spec = json.loads(json.dumps(TABLE["parentSpec"]))
    for server in spec["extensions"]["x.pipeshub"]["mcp"]:
        pin = (case.get("parentSpecPins") or {}).get(server["instanceId"])
        if pin is not None:
            server["pins"] = dict(pin)
    return validate_contract(RunSpec, spec)


@pytest.mark.parametrize("case", TABLE["cases"], ids=[c["id"] for c in TABLE["cases"]])
def test_child_mcp(case: dict[str, Any]) -> None:
    parent = _parent(case)
    decision = child_grants(parent, ChildGrantRequest.from_wire(case["request"]), 1, case.get("pins"))
    assert decision.refusal == case["expect"]["refusal"]
    if decision.grants is None:
        return
    assert decision.grants["tools"] == case["expect"]["tools"]
    child = child_spec(parent, decision.grants, mcp_pins=case.get("pins"))
    assert child["grants"]["tools"] == case["expect"]["childTools"]  # type: ignore[index]
    assert child["extensions"]["x.pipeshub"]["mcp"] == case["expect"]["mcp"]  # type: ignore[index]
    validate_contract(RunSpec, child)


def test_a_code_task_child_on_the_engine_holds_the_server_whole() -> None:
    decision = child_grants(PARENT, ChildGrantRequest(mounts=("/workspace",)), 1)
    assert decision.grants is not None
    child = child_spec(PARENT, decision.grants, executor={"kind": "loop"})
    whole = [s for s in child["extensions"]["x.pipeshub"]["mcp"] if s.get("tools") == "at_first_use"]  # type: ignore[index,union-attr]
    assert [s["instanceId"] for s in whole] == ["mcp-rovo"]
