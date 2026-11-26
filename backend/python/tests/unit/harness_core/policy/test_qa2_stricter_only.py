"""QA2-1..QA2-6, QA2-9, QA2-10: a lower layer may only make things stricter, for every row of the dimension table
in every layer that may set it (``contracts/harness/vectors/stricter-only.json``, shared with Node)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness_core.contracts.generated.enums import LAYERS
from harness_core.policy import DimensionPolicyResolver, ScopedLayer, policy_registry
from harness_core.testing.contracts.policy_resolver import PolicyResolverContract

from .support import STRICTER_ONLY

if TYPE_CHECKING:
    from harness_core.policy import Dimension, Resolved

REGISTRY = policy_registry()
RESOLVER = DimensionPolicyResolver()
LADDERS: list[dict[str, Any]] = STRICTER_ONLY["ladders"]
CHOICES: list[dict[str, Any]] = STRICTER_ONLY["choices"]


def _exposed(dimension: Dimension) -> list[str]:
    return [layer for layer in LAYERS if layer in dimension.exposure]


def _put(values: dict[str, Any], path: str, value: object) -> dict[str, Any]:
    *parents, last = path.split(".")
    node = values
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = value
    return values


def _layer(kind: str, dimension: Dimension, value: object) -> ScopedLayer:
    return ScopedLayer(kind, _put({}, dimension.layer_path, value))


def _base(ladder: dict[str, Any]) -> list[ScopedLayer]:
    return [ScopedLayer(layer["kind"], layer["values"]) for layer in ladder.get("base", [])]


def _entry(resolved: Resolved, path: str) -> dict[str, Any]:
    assert resolved.refusal is None, resolved.refusal
    assert resolved.config is not None
    return cast("dict[str, dict[str, Any]]", resolved.config["dimensions"])[path]


def _sample(dimension: Dimension) -> object:
    if dimension.kind == "operator":
        return STRICTER_ONLY["operatorSample"]
    rows = LADDERS if dimension.kind == "ceiling" else CHOICES
    row = next(row for row in rows if row["path"] == dimension.path)
    return row["top"] if dimension.kind == "ceiling" else row["inside"]


LOWER = [(ladder, lower) for ladder in LADDERS for lower in _exposed(REGISTRY.get(ladder["path"]))[1:]]
UNEXPOSED = [
    (dimension, layer) for dimension in REGISTRY.all() for layer in LAYERS[1:] if layer not in dimension.exposure
]
CHOOSERS = [(choice, chooser) for choice in CHOICES for chooser in _exposed(REGISTRY.get(choice["path"]))]


def _lower_id(case: object) -> str:
    return case["path"] if isinstance(case, dict) else str(getattr(case, "path", case))


def test_qa2_1_every_ceiling_row_has_a_ladder_and_every_choice_row_a_sample() -> None:
    rows = REGISTRY.all()
    assert sorted(ladder["path"] for ladder in LADDERS) == sorted(d.path for d in rows if d.kind == "ceiling")
    assert sorted(choice["path"] for choice in CHOICES) == sorted(d.path for d in rows if d.kind == "choice")


@pytest.mark.parametrize(("ladder", "lower"), LOWER, ids=_lower_id)
def test_qa2_2_a_narrowing_by_a_lower_layer_is_kept(ladder: dict[str, Any], lower: str) -> None:
    dimension = REGISTRY.get(ladder["path"])
    upper = _exposed(dimension)[0]
    resolved = RESOLVER.resolve(
        [*_base(ladder), _layer(upper, dimension, ladder["top"]), _layer(lower, dimension, ladder["narrow"])]
    )
    entry = _entry(resolved, dimension.path)
    assert entry["value"] == ladder.get("kept", ladder["narrow"])
    assert entry["setBy"] == ladder.get("setBy", lower)
    assert entry["ceilings"][-2:] == [
        {"layer": upper, "value": ladder["top"]},
        {"layer": lower, "value": ladder["narrow"]},
    ]


@pytest.mark.parametrize(("ladder", "lower"), LOWER, ids=_lower_id)
def test_qa2_3_a_widening_by_a_lower_layer_is_refused_or_met(ladder: dict[str, Any], lower: str) -> None:
    dimension = REGISTRY.get(ladder["path"])
    upper = _exposed(dimension)[0]
    resolved = RESOLVER.resolve(
        [*_base(ladder), _layer(upper, dimension, ladder["narrow"]), _layer(lower, dimension, ladder["widen"])]
    )
    if dimension.widening == "meet":
        assert _entry(resolved, dimension.path)["value"] == ladder.get("kept", ladder["narrow"])
        return
    assert resolved.refusal is not None
    assert resolved.refusal.wire() == {
        "code": "widening",
        "dimension": ladder.get("at", dimension.path),
        "layer": lower,
    }


@pytest.mark.parametrize(("dimension", "layer"), UNEXPOSED, ids=_lower_id)
def test_qa2_4_a_layer_outside_a_rows_exposure_may_not_set_it(dimension: Dimension, layer: str) -> None:
    resolved = RESOLVER.resolve([_layer(layer, dimension, _sample(dimension))])
    assert resolved.refusal is not None
    assert resolved.refusal.wire() == {"code": "not_exposed", "dimension": dimension.path, "layer": layer}


@pytest.mark.parametrize(("choice", "chooser"), CHOOSERS, ids=_lower_id)
def test_qa2_5_a_choice_lies_within_its_ceiling_whoever_chooses(choice: dict[str, Any], chooser: str) -> None:
    dimension = REGISTRY.get(choice["path"])
    assert dimension.within is not None
    ceiling = REGISTRY.get(dimension.within)
    top, bottom = _exposed(ceiling)[0], _exposed(ceiling)[-1]
    assert LAYERS.index(top) <= LAYERS.index(chooser) < LAYERS.index(bottom)
    outside = {"code": "outside_ceiling", "dimension": dimension.path, "layer": chooser}

    def resolve(chosen: object, *below: ScopedLayer) -> Resolved:
        ceiling_layer = _put({}, ceiling.layer_path, choice["ceiling"])
        if chooser == top:
            return RESOLVER.resolve([ScopedLayer(top, _put(ceiling_layer, dimension.layer_path, chosen)), *below])
        return RESOLVER.resolve([ScopedLayer(top, ceiling_layer), _layer(chooser, dimension, chosen), *below])

    assert _entry(resolve(choice["inside"]), dimension.path) == {
        "value": choice["inside"],
        "setBy": chooser,
        "ceilings": [],
    }
    refused = resolve(choice["outside"]).refusal
    assert refused is not None
    assert refused.wire() == outside
    narrowed_below = resolve(choice["inside"], _layer(bottom, ceiling, choice["narrowed"])).refusal
    assert narrowed_below is not None
    assert narrowed_below.wire() == outside


def test_qa2_9_qa2_10_every_case_resolves_as_the_vectors_say() -> None:
    PolicyResolverContract().test_cpd3_10_every_case_resolves_as_the_vectors_say(RESOLVER, STRICTER_ONLY)


# An admitted item (the engine) is chosen outside its order, as in CPD3-13: QA2-2 and QA2-3 hold it.
POOLS: dict[str, tuple[object, ...]] = {
    ladder["path"]: (ladder["top"], ladder["narrow"], ladder["widen"])
    for ladder in LADDERS
    if cast("Any", REGISTRY.get(ladder["path"]).order).kind != "admits"
}
CHAIN = [layer for layer in LAYERS if any(layer in REGISTRY.get(path).exposure for path in POOLS)]


def _values(draw: st.DrawFn, kind: str, paths: list[str]) -> ScopedLayer:
    exposed = [path for path in paths if kind in REGISTRY.get(path).exposure]
    values: dict[str, Any] = {}
    for path in draw(st.lists(st.sampled_from(exposed), unique=True)) if exposed else []:
        _put(values, REGISTRY.get(path).layer_path, draw(st.sampled_from(POOLS[path])))
    return ScopedLayer(kind, values)


@st.composite
def chains(draw: st.DrawFn) -> tuple[list[ScopedLayer], ScopedLayer]:
    """A chain over a few rows (so layers meet on the same ones) and one more layer, of a kind not in it."""
    paths = draw(st.lists(st.sampled_from(sorted(POOLS)), min_size=1, max_size=4, unique=True))
    kinds = draw(st.lists(st.sampled_from(CHAIN), min_size=2, max_size=6, unique=True))
    layers = [_values(draw, kind, paths) for kind in sorted(kinds, key=LAYERS.index)]
    extra = draw(st.integers(0, len(layers) - 1))
    return [*layers[:extra], *layers[extra + 1 :]], layers[extra]


def _set_by(layer: ScopedLayer) -> dict[str, object]:
    found: dict[str, object] = {}
    for path in POOLS:
        node: object = layer.values
        for key in REGISTRY.get(path).layer_path.split("."):
            node = cast("dict[str, object]", node).get(key) if isinstance(node, dict) else None
        if node is not None:
            found[path] = node
    return found


def _effective(resolved: Resolved) -> dict[str, object]:
    assert resolved.config is not None
    dimensions = cast("dict[str, dict[str, object]]", resolved.config["dimensions"])
    return {path: entry["value"] for path, entry in dimensions.items()}


@given(chains())
def test_qa2_6_every_resolved_row_is_at_or_below_each_layer_that_sets_it(
    drawn: tuple[list[ScopedLayer], ScopedLayer],
) -> None:
    chain, _ = drawn
    resolved = RESOLVER.resolve(chain)
    if resolved.refusal is not None:
        refused = resolved.refusal
        index = next(i for i, layer in enumerate(chain) if layer.kind == refused.layer)
        assert refused.code == "widening"
        assert any(refused.dimension.startswith(path) for path in _set_by(chain[index]))
        assert RESOLVER.resolve(chain[:index]).refusal is None
        return
    effective = _effective(resolved)
    for layer in chain:
        for path, raw in _set_by(layer).items():
            order = cast("Any", REGISTRY.get(path).order)
            assert order.leq(effective[path], order.lift(raw)) is None, (path, layer.kind)


@given(chains())
def test_qa2_6_inserting_a_layer_never_widens_a_row(drawn: tuple[list[ScopedLayer], ScopedLayer]) -> None:
    chain, extra = drawn
    longer = sorted([*chain, extra], key=lambda layer: LAYERS.index(cast("Any", layer.kind)))
    before, after = RESOLVER.resolve(chain), RESOLVER.resolve(longer)
    if before.refusal is not None or after.refusal is not None:
        return
    old, new = _effective(before), _effective(after)
    for path in old:
        order = cast("Any", REGISTRY.get(path).order)
        assert order.leq(new[path], old[path]) is None, path
