"""NTF-10, IX-NTF-PIN-4 (CPD-6): ``pin ⊓ live ⊑ pin`` over the whole dimension table.

A run's pin is what layers resolve to; the live layer is folded onto it with meet on every ceiling a ``live`` layer
may set. Whatever the live layers hold, no ceiling of the result is above the pin's, a choice and an operator row
are the pin's, and the fold never refuses. CPD6-4: live layers fold to one answer in any order, any number of times.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from harness_core.contracts import canonical_json, dump_contract
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.policy import (
    UNDEFINED,
    DimensionPolicyResolver,
    FoldedPrefix,
    InvalidPolicyValue,
    ScopedLayer,
    policy_registry,
)

RESOLVER = DimensionPolicyResolver()
LIVE_FOLD_VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/live-fold.json").read_text(encoding="utf-8")
)["cases"]
RELEASE = ScopedLayer("release", dump_contract(RELEASE_DEFAULTS))

_LEVELS = st.sampled_from(("allow", "ask", "deny"))
_APPROVALS = st.fixed_dictionaries(
    {},
    optional={
        "writes": _LEVELS,
        "classes": st.fixed_dictionaries({}, optional={"read": _LEVELS, "execute": _LEVELS}),
        "tools": st.dictionaries(st.sampled_from(("t1", "t2", "t3")), _LEVELS, max_size=3),
        "flow": st.fixed_dictionaries(
            {"level": st.sampled_from(("off", "ask", "deny")), "above": st.sampled_from(("public", "private"))}
        ),
    },
)
_ROUTES = st.lists(st.sampled_from(("openai:gpt-x", "anthropic:claude-z", "openai:gpt-y")), unique=True, min_size=1)
_BUDGETS = st.fixed_dictionaries(
    {},
    optional={"maxTurns": st.integers(1, 40), "maxTokens": st.integers(1, 2_000_000), "maxCostUsd": st.integers(1, 9)},
)
_ENGINE = st.fixed_dictionaries(
    {},
    optional={
        "mode": st.sampled_from(("build", "plan", "review")),
        "approvals": st.fixed_dictionaries({}, optional={"write": _LEVELS, "execute": _LEVELS, "read": _LEVELS}),
        "limits": st.fixed_dictionaries({}, optional={"maxSteps": st.integers(1, 80), "maxWallS": st.integers(1, 4000)}),
        "tools": st.fixed_dictionaries(
            {}, optional={"effects": st.lists(st.sampled_from(("read", "write", "execute", "workspace")), unique=True)}
        ),
    },
)
# What any layer may carry, valid or not for its place in the chain: the live fold must take all of it.
_SECTIONS = st.fixed_dictionaries(
    {},
    optional={
        "approvals": _APPROVALS,
        "budgets": _BUDGETS,
        "engineProfile": _ENGINE,
        "models": st.fixed_dictionaries({}, optional={"routes": _ROUTES, "primary": _ROUTES.map(lambda r: r[0])}),
        "rollout": st.fixed_dictionaries(
            {}, optional={"AGENT_HARNESS_KILL_SWITCH": st.booleans(), "ENABLE_SKILLS": st.booleans()}
        ),
    },
)
# An org layer as an admin may save it: ceilings only, each at or below the release's (a pin exists for it).
_ORG = st.fixed_dictionaries(
    {},
    optional={
        "approvals": _APPROVALS,
        "budgets": st.fixed_dictionaries({}, optional={"maxTurns": st.integers(1, 15), "maxCostUsd": st.integers(1, 5)}),
        "rollout": st.fixed_dictionaries({}, optional={"ENABLE_SKILLS": st.booleans()}),
    },
)


def _pin(org: dict[str, Any]) -> dict[str, Any]:
    resolved = RESOLVER.resolve([RELEASE, ScopedLayer("org", org)])
    assert resolved.config is not None, resolved.refusal
    return cast("dict[str, Any]", resolved.config)


@settings(max_examples=300, deadline=None, database=None)
@given(org=_ORG, live=st.lists(_SECTIONS, max_size=3))
def test_ntf10_no_ceiling_of_pin_meet_live_is_above_the_pin(org: dict[str, Any], live: list[dict[str, Any]]) -> None:
    pin = _pin(org)
    narrowed = RESOLVER.narrowed_by_live(pin, RESOLVER.live_fold(live))

    for dimension in policy_registry().all():
        before = pin["dimensions"].get(dimension.path)
        after = cast("dict[str, Any]", narrowed["dimensions"]).get(dimension.path)
        if dimension.order is None or dimension.kind != "ceiling" or dimension.order.kind == "admits":
            assert after == before, dimension.path
            continue
        if after is None or before is None:
            continue
        assert dimension.order.leq(after["value"], before["value"]) is None, (dimension.path, before, after)


@settings(max_examples=200, deadline=None, database=None)
@given(org=_ORG, live=st.lists(_SECTIONS, max_size=3))
def test_ntf10_a_live_layer_that_narrows_nothing_leaves_the_pin_as_it_is(
    org: dict[str, Any], live: list[dict[str, Any]]
) -> None:
    pin = _pin(org)
    narrowed = cast("dict[str, Any]", RESOLVER.narrowed_by_live(pin, RESOLVER.live_fold(live)))

    for path, entry in narrowed["dimensions"].items():
        before = pin["dimensions"].get(path)
        if before is not None and canonical_json(entry["value"]) == canonical_json(before["value"]):
            assert entry["setBy"] == before["setBy"], path
        else:
            assert entry["setBy"] == "live", path


def _values(folded: FoldedPrefix) -> dict[str, bytes | None]:
    """Each dimension the fold holds; None for the bottom (an empty meet)."""
    return {path: None if e.value is UNDEFINED else canonical_json(e.value) for path, e in folded.entries.items()}


_CPD6_4 = ({"engineProfile": {"mode": "plan"}}, {"engineProfile": {"mode": "review"}}, {})


@settings(max_examples=300, deadline=None, database=None)
@example(*_CPD6_4)
@given(a=_SECTIONS, b=_SECTIONS, c=_SECTIONS)
def test_cpd6_4_live_fold_is_a_meet_commutative_associative_idempotent(
    a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]
) -> None:
    fold = RESOLVER.live_fold
    abc = _values(fold([a, b, c]))
    for order in itertools.permutations((a, b, c)):
        assert _values(fold(list(order))) == abc
    assert _values(fold([c], fold([a, b]))) == _values(fold([b, c], fold([a]))) == abc
    assert _values(fold([a, b, c, a, b])) == abc
    assert _values(fold([a, a])) == _values(fold([a], fold([a]))) == _values(fold([a]))


@pytest.mark.parametrize("case", LIVE_FOLD_VECTORS, ids=lambda c: c["id"])
def test_cpd6_4_live_fold_vectors(case: dict[str, Any]) -> None:
    layers: list[dict[str, Any]] = case["layers"]
    expect = {path: canonical_json(v) for path, v in case["values"].items()} | dict.fromkeys(case["bottom"])
    pin = _pin({})
    for order in itertools.permutations(layers):
        folded = RESOLVER.live_fold(list(order))
        assert _values(folded) == expect
        narrowed = cast("dict[str, Any]", RESOLVER.narrowed_by_live(pin, folded))["dimensions"]
        for path in case["bottom"]:
            assert path not in narrowed, path


def _child_pin(parent: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    """A child's pin as admission resolves it: release ⊓ parent ⊓ request, no org or agent layer read again. The
    parent layer is the parent's resolved values at their own paths (``parent_layer`` in the control plane)."""
    layer: dict[str, Any] = {}
    for dimension in policy_registry().all():
        entry = parent["dimensions"].get(dimension.path)
        if entry is None or dimension.layer_path != dimension.path or "parent" not in dimension.exposure:
            continue
        *parents, last = dimension.path.split(".")
        node = layer
        for key in parents:
            node = node.setdefault(key, {})
        node[last] = entry["value"]
    resolved = RESOLVER.resolve([RELEASE, ScopedLayer("parent", layer), ScopedLayer("request", request)])
    return cast("dict[str, Any] | None", resolved.config)


# What a child's request may narrow.
_CHILD_REQUEST = st.fixed_dictionaries(
    {}, optional={"budgets": st.fixed_dictionaries({}, optional={"maxTurns": st.integers(1, 3)})}
)


@settings(max_examples=300, deadline=None, database=None)
@given(org=_ORG, request=_CHILD_REQUEST, before=st.lists(_SECTIONS, max_size=2), after=st.lists(_SECTIONS, max_size=2))
def test_ntf10_a_child_runs_under_its_pin_met_with_the_live_layer_whenever_the_narrowing_was_made(
    org: dict[str, Any], request: dict[str, Any], before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> None:
    """``before``: narrowings made after the root's admission and before the child was spawned, which the child's
    pin cannot hold (it reads no org layer again); ``after``: those made while the child runs. Either way the
    child's ceilings are at or below its pin's and at or below every live value."""
    child = _child_pin(_pin(org), request)
    # A request above its parent is refused at admission: there is no child to narrow.
    assume(child is not None)
    assert child is not None
    live = RESOLVER.live_fold([*before, *after])

    narrowed = cast("dict[str, Any]", RESOLVER.narrowed_by_live(child, live))["dimensions"]

    for dimension in policy_registry().all():
        if dimension.order is None or dimension.kind != "ceiling" or dimension.order.kind == "admits":
            continue
        entry = narrowed.get(dimension.path)
        if entry is None:
            continue
        pinned, held = child["dimensions"].get(dimension.path), live.entries.get(dimension.path)
        if pinned is not None:
            assert dimension.order.leq(entry["value"], pinned["value"]) is None, (dimension.path, "above the child's pin")
        if held is not None and held.value is not UNDEFINED:
            assert dimension.order.leq(entry["value"], held.value) is None, (dimension.path, "above the live layer")


def test_ntf10_a_live_layer_cannot_set_a_choice_or_an_operator_row() -> None:
    pin = _pin({})
    live = RESOLVER.live_fold(
        [{"models": {"primary": "x:y"}, "operator": {"SANDBOX_MODE": "none"}}]
    )

    assert live.entries == {}
    assert RESOLVER.narrowed_by_live(pin, live) == pin


def test_ntf10_the_kill_switch_marks_a_pin_admitted_before_it() -> None:
    pin = _pin({"rollout": {"AGENT_HARNESS_KILL_SWITCH": False}})
    narrowed = cast(
        "dict[str, Any]",
        RESOLVER.narrowed_by_live(pin, RESOLVER.live_fold([{"rollout": {"AGENT_HARNESS_KILL_SWITCH": True}}])),
    )

    switch = narrowed["dimensions"]["rollout.AGENT_HARNESS_KILL_SWITCH"]
    assert (switch["value"], switch["setBy"]) == (True, "live")


@settings(max_examples=200, deadline=None, database=None)
@given(layers=st.lists(_APPROVALS, min_size=1, max_size=3))
def test_cpd6_35_lifting_a_ceiling_again_is_the_ceiling(layers: list[dict[str, Any]]) -> None:
    """A child's pin folds its parent's resolved approvals, which are a ceiling already (two flow rules at most,
    which no layer's ``flow`` can say): read as rules they would be refused, and every child with them."""
    order = policy_registry().get("approvals").order
    assert order is not None
    ceiling = order.lift(layers[0])
    for layer in layers[1:]:
        ceiling = order.meet(order.lift(layer), ceiling)

    assert canonical_json(order.lift(ceiling)) == canonical_json(ceiling)


def test_cpd6_35_a_child_of_a_parent_with_two_flow_rules_is_pinned_with_both() -> None:
    parent = _pin({"approvals": {"flow": {"level": "deny", "above": "private"}}})
    deployment = RESOLVER.live_fold([{"approvals": {"flow": {"level": "ask", "above": "public"}}}])
    parent = cast("dict[str, Any]", RESOLVER.narrowed_by_live(parent, deployment))

    child = _child_pin(parent, {})

    assert child is not None
    assert child["dimensions"]["approvals"]["value"]["flow"] == [
        {"level": "ask", "above": "public"},
        {"level": "deny", "above": "private"},
    ]


def test_cpd6_35_a_ceiling_with_a_level_that_is_none_is_refused_not_read_as_allow() -> None:
    order = policy_registry().get("approvals").order
    assert order is not None
    ceiling = cast("dict[str, Any]", order.lift({"writes": "ask"}))

    with pytest.raises(InvalidPolicyValue):
        _ = order.lift({**ceiling, "classes": {**ceiling["classes"], "write": "sometimes"}})
    with pytest.raises(InvalidPolicyValue):
        _ = order.lift({**ceiling, "flow": [{"level": "off", "above": "public"}]})
