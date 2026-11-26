"""CPD3-11, CPD3-2, CPD3-19: every order on its order vectors, the tables both registries refuse, and the
effective config's digest (``contracts/harness/vectors/policy-resolve.json``)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from harness_core.contracts import canonical_digest
from harness_core.policy import (
    UNDEFINED,
    DimensionPolicyResolver,
    DimensionRegistry,
    InvalidDimensionTable,
    ScopedLayer,
    order_of,
)

from .support import POLICY_VECTORS


@pytest.mark.parametrize("entry", POLICY_VECTORS["orders"], ids=lambda e: e["id"])
def test_cpd3_11_every_order_meets_and_compares_as_the_vectors_say(entry: dict[str, Any]) -> None:
    order = order_of(entry["order"])
    for case in entry["cases"]:
        widened = order.leq(case["a"], case["b"])
        assert (widened is None) == case["leq"], case
        if widened is not None:
            assert widened.at == case.get("at"), case
        met = order.meet(case["a"], case["b"])
        if case.get("meetUndefined"):
            assert met is UNDEFINED, case
        else:
            assert met == case["meet"], case


@pytest.mark.parametrize("entry", POLICY_VECTORS["invalidTables"], ids=lambda e: e["id"])
def test_cpd3_2_a_table_that_breaks_a_rule_is_refused_naming_the_row(entry: dict[str, Any]) -> None:
    with pytest.raises(InvalidDimensionTable, match=entry["names"].replace(".", r"\.")):
        DimensionRegistry.from_table({"schemaVersion": 1, "dimensions": entry["dimensions"]})


@pytest.mark.parametrize("case", [c for c in POLICY_VECTORS["cases"] if "config" in c["expect"]], ids=lambda c: c["id"])
def test_cpd3_19_the_digest_is_the_canonical_digest_whatever_the_member_order(case: dict[str, Any]) -> None:
    def reversed_members(node: Any) -> Any:  # noqa: ANN401 - JSON
        if isinstance(node, dict):
            return {k: reversed_members(v) for k, v in reversed(list(node.items()))}  # pyright: ignore[reportUnknownVariableType]
        return node

    layers = [ScopedLayer(layer["kind"], reversed_members(copy.deepcopy(layer["values"]))) for layer in case["layers"]]
    resolved = DimensionPolicyResolver().resolve(layers)
    assert resolved.digest == case["expect"]["digest"] == canonical_digest(case["expect"]["config"])
