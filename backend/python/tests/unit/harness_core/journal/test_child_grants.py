"""P6.3: grants of a child run ⊆ its parent's, on the table Node runs too (CG-*)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import (
    CHILD_MAX_DEPTH,
    ChildGrantRequest,
    child_budget_share,
    child_grants,
    child_run_id_for,
)

TABLE: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/child-grants.json").read_text(
        encoding="utf-8"
    )
)


def _parent(patch: dict[str, Any] | None) -> RunSpec:
    spec = json.loads(json.dumps(TABLE["parentSpec"]))
    for key, value in (patch or {}).items():
        spec["grants"][key] = value
    return validate_contract(RunSpec, spec)


def test_table_and_build_agree_on_the_deepest_level() -> None:
    assert TABLE["maxDepth"] == CHILD_MAX_DEPTH


@pytest.mark.parametrize("case", TABLE["cases"], ids=[c["id"] for c in TABLE["cases"]])
def test_child_grants(case: dict[str, Any]) -> None:
    decision = child_grants(
        _parent(case.get("parent")),
        ChildGrantRequest.from_wire(case["request"]),
        case["depth"],
    )
    assert decision.refusal == case["expect"]["refusal"]
    if "grants" in case["expect"]:
        assert decision.grants == case["expect"]["grants"]


@pytest.mark.parametrize("case", TABLE["childRunIds"], ids=lambda c: c["invocationId"])
def test_child_run_id_matches_node(case: dict[str, str]) -> None:
    assert (
        child_run_id_for(case["orgId"], case["parentRunId"], case["invocationId"])
        == case["childRunId"]
    )


def test_request_wire_round_trip() -> None:
    request = ChildGrantRequest(
        tools=("a@b/c",),
        sandbox_profile="none",
        knowledge_kb=("kb-1",),
        max_turns=3,
    )
    wire = request.wire()
    assert wire == {"tools": ["a@b/c"], "knowledge": {"kb": ["kb-1"]}, "sandboxProfile": "none", "maxTurns": 3}
    assert ChildGrantRequest.from_wire(wire) == request


def test_cg9_max_tokens_round_trips_on_the_wire() -> None:
    request = ChildGrantRequest(max_turns=8, max_tokens=250_000, max_children=0)
    assert request.wire() == {"maxTurns": 8, "maxTokens": 250_000, "maxChildren": 0}
    assert ChildGrantRequest.from_wire(request.wire()) == request


@pytest.mark.parametrize(
    ("parent_turns", "parent_tokens", "definition_turns", "batch", "turns", "tokens"),
    [
        (15, 1_000_000, 8, 1, 8, 500_000),
        (15, 1_000_000, 8, 3, 8, 250_000),
        (5, 1_000_000, 8, 1, 5, 500_000),
        (15, 3, 8, 8, 8, 1),
        (15, 1_000_000, 8, 0, 8, 500_000),
        (1, 1, 0, 1, 1, 1),
    ],
)
def test_bs1_a_childs_interim_share_of_its_parents_budget(
    parent_turns: int, parent_tokens: int, definition_turns: int, batch: int, turns: int, tokens: int
) -> None:
    share = child_budget_share(
        parent_max_turns=parent_turns,
        parent_max_tokens=parent_tokens,
        definition_max_turns=definition_turns,
        batch=batch,
    )
    assert (share.max_turns, share.max_tokens, share.max_children) == (turns, tokens, 0)
