"""LEG-P: ``contracts/harness/vectors/legacy-flow-rules.json`` is what today's read path decides, so the flow
rules Node signs into ``X-PipesHub-Resolved`` (its test reads the same file) are held to the reads."""

from __future__ import annotations

import json

from tests.support.legacy_flow_rules_vector import (
    APPROVALS_PATH,
    DEPLOYMENTS,
    VECTOR_PATH,
    build,
    render,
)
from tests.unit.harness.legacy import test_legacy_parity


def test_the_committed_vector_is_the_generator_s_output() -> None:
    assert VECTOR_PATH.read_text(encoding="utf-8") == render()


def test_it_covers_every_approvals_document_under_the_parity_test_s_deployments() -> None:
    vector = build()
    documents = json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))["documents"]
    assert [case["id"] for case in vector["cases"]] == [case["id"] for case in documents]
    assert test_legacy_parity.DEPLOYMENTS == DEPLOYMENTS
    assert [deployment["id"] for deployment in vector["deployments"]] == list(DEPLOYMENTS)
    assert all(set(case["rules"]) == set(DEPLOYMENTS) for case in vector["cases"])


def test_an_unreadable_layer_asks_from_the_lowest_threshold_with_the_guard_off() -> None:
    cases = {case["id"]: case for case in build()["cases"]}
    assert {name for name, case in cases.items() if case["unreadable"]} == {
        case["id"] for case in test_legacy_parity.UNREADABLE
    }
    assert all(
        case["rules"]["guard off"] == [{"level": "ask", "above": "public"}] for case in cases.values() if case["unreadable"]
    )
    # An operator's own writes-ask is no flow rule on today's loop.
    assert cases["D-3"]["rules"]["guard off"] == []
