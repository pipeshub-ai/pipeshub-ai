"""CPD3-12 (POL-12): meet is idempotent, commutative and associative per order kind, and lies at or below both
operands; CPD3-14: a choice lies within its ceiling or the run is refused."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness_core.policy import (
    UNDEFINED,
    DimensionPolicyResolver,
    Order,
    ScopedLayer,
    order_of,
    policy_registry,
)

from .support import POLICY_VECTORS

SPECS: dict[str, dict[str, Any]] = {entry["id"]: entry["order"] for entry in POLICY_VECTORS["orders"]}
_LEVELS = st.sampled_from(("allow", "ask", "deny"))
_APPROVALS = st.fixed_dictionaries(
    {},
    optional={
        "writes": _LEVELS,
        "classes": st.fixed_dictionaries({}, optional={"read": _LEVELS, "workspace": _LEVELS}),
        "tools": st.dictionaries(st.sampled_from(("t1", "t2", "t3")), _LEVELS, max_size=3),
        "flow": st.fixed_dictionaries({"level": st.sampled_from(("off", "ask", "deny")), "above": st.sampled_from(("public", "private"))}),
    },
)
VALUES: dict[str, st.SearchStrategy[Any]] = {
    "min": st.none() | st.integers(0, 50) | st.floats(0, 50, allow_nan=False),
    "subset": st.none() | st.lists(st.sampled_from("abcde"), unique=True),
    "min-map": st.none() | st.dictionaries(st.sampled_from(("default", "coder", "researcher")), st.integers(0, 50) | st.floats(0, 50, allow_nan=False), max_size=3),
    "rank": st.none() | st.sampled_from(("low", "medium", "high")),
    "rank-raise": st.none() | st.sampled_from(("docker", "gvisor", "k8s", "managed", "microvm")),
    "bool-and": st.booleans(),
    "bool-or": st.booleans(),
    "flat-bottom": st.sampled_from(("none", "allowlist:a", "allowlist:b")),
    "flat": st.none() | st.sampled_from(({"a": 1}, {"a": 2}, "x")),
    "tree": st.sampled_from(("build", "plan", "review")),
    "gated": st.builds(
        lambda on, d, c: {"enabled": on, "maxDepth": d, "maxConcurrent": c} if on else {"enabled": False, "maxDepth": 1, "maxConcurrent": 1},
        st.booleans(), st.integers(1, 4), st.integers(1, 4),
    ),
    "approval-rules": _APPROVALS,
    "rank-raise-rules": st.none()
    | st.dictionaries(
        st.sampled_from(("install:*", "install:mcp", "install:skill", "publish:*", "publish:skill")),
        st.sampled_from(("none", "owner", "teamAdmin", "orgAdmin")),
        max_size=4,
    ),
}


def _meet(order: Order, a: object, b: object) -> object:
    return UNDEFINED if UNDEFINED in (a, b) else order.meet(a, b)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_cpd3_12_meet_is_idempotent_commutative_associative_and_below_both(name: str) -> None:
    order = order_of(SPECS[name])

    @given(VALUES[name], VALUES[name], VALUES[name])
    def check(x: object, y: object, z: object) -> None:
        a, b, c = order.lift(x), order.lift(y), order.lift(z)
        assert order.meet(a, a) == a
        assert order.meet(a, b) == order.meet(b, a)
        assert _meet(order, _meet(order, a, b), c) == _meet(order, a, _meet(order, b, c))
        met = order.meet(a, b)
        if met is not UNDEFINED:
            assert order.leq(met, a) is None
            assert order.leq(met, b) is None

    check()


def test_cpd3_12_the_properties_cover_every_order_kind_the_table_uses() -> None:
    used = {row.order.kind for row in policy_registry().all() if row.order is not None}
    # An admitted item is chosen outside the order, so it has no meet to test.
    assert used - {"admits"} <= {spec["kind"] for spec in SPECS.values()}


@given(st.lists(st.sampled_from("abcd"), unique=True, min_size=1), st.sampled_from("abcd"), st.sampled_from(("org", "agent", "user", "request")))
def test_cpd3_14_a_resolved_choice_lies_within_its_ceiling(routes: list[str], primary: str, chooser: str) -> None:
    resolved = DimensionPolicyResolver().resolve(
        [ScopedLayer("org", {"models": {"routes": routes}}), ScopedLayer(chooser, {"models": {"primary": primary}})]
    )
    if primary in routes:
        assert resolved.config is not None
        dimensions = resolved.config["dimensions"]
        assert isinstance(dimensions, dict)
        assert dimensions["models.primary"] == {"value": primary, "setBy": chooser, "ceilings": []}
    else:
        assert resolved.refusal is not None
        assert resolved.refusal.wire() == {"code": "outside_ceiling", "dimension": "models.primary", "layer": chooser}
