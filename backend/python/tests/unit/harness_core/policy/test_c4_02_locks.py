"""C4.02 (LK-1..LK-5): any dimension can be locked at a layer, and no layer below it can override the locked value.

A locked choice is refused at every lower layer that picks another value; a locked ceiling is refused at every lower
layer that widens it (kept at the stricter value where the row meets), and may still be narrowed. Node's
``c4-02-locks.test.ts`` holds the same rows on the same vectors (``contracts/harness/vectors/stricter-only.json``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness_core.contracts import canonical_digest
from harness_core.contracts.generated.enums import LAYERS
from harness_core.policy import (
    UNDEFINED,
    DimensionPolicyResolver,
    FoldedPrefix,
    ScopedLayer,
    policy_registry,
)

from .support import STRICTER_ONLY

if TYPE_CHECKING:
    from harness_core.policy import Dimension, Resolved

REGISTRY = policy_registry()
RESOLVER = DimensionPolicyResolver()
LADDERS: list[dict[str, Any]] = [ladder for ladder in STRICTER_ONLY["ladders"] if "base" not in ladder]
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


def _entry(resolved: Resolved, path: str) -> dict[str, Any]:
    assert resolved.refusal is None, resolved.refusal
    assert resolved.config is not None
    return cast("dict[str, dict[str, Any]]", resolved.config["dimensions"])[path]


def _section(path: str) -> str:
    return path.split(".", 1)[0]


LADDER_LOWERS = [(ladder, lower) for ladder in LADDERS for lower in _exposed(REGISTRY.get(ladder["path"]))[1:]]
CHOICE_LOWERS = [
    (choice, lower)
    for choice in CHOICES
    for lower in _exposed(REGISTRY.get(choice["path"]))[1:]
]


def _id(case: object) -> str:
    return str(cast("dict[str, Any]", case)["path"]) if isinstance(case, dict) else str(case)


def test_lk_0_every_lockable_row_is_covered() -> None:
    """Every ceiling and choice row with a layer below its first is held by LK-1 or LK-2; the engine's admitted
    item is held by PR vectors (its ladder needs the release's engine as a base)."""
    covered = {ladder["path"] for ladder, _ in LADDER_LOWERS} | {choice["path"] for choice, _ in CHOICE_LOWERS}
    lockable = {d.path for d in REGISTRY.all() if d.kind != "operator" and len(_exposed(d)) > 1}
    assert lockable - covered == {"engineProfile.engine"}


@pytest.mark.parametrize(("choice", "lower"), CHOICE_LOWERS, ids=_id)
def test_lk_1_a_locked_choice_is_not_overridable_at_any_lower_layer(choice: dict[str, Any], lower: str) -> None:
    dimension = REGISTRY.get(choice["path"])
    assert dimension.within is not None
    ceiling = REGISTRY.get(dimension.within)
    owner = _exposed(dimension)[0]
    assert owner in ceiling.exposure
    top = _put(_put({"locks": [dimension.path]}, ceiling.layer_path, choice["ceiling"]), dimension.layer_path, choice["inside"])

    def below(value: object, **extra: object) -> Resolved:
        return RESOLVER.resolve([ScopedLayer(owner, top), ScopedLayer(lower, _put(dict(extra), dimension.layer_path, value))])

    refused = below(choice["other"]).refusal
    assert refused is not None
    assert refused.wire() == {"code": "locked", "dimension": dimension.path, "layer": lower}
    assert _entry(below(choice["inside"]), dimension.path) == {
        "value": choice["inside"],
        "setBy": owner,
        "ceilings": [],
        "lockedBy": owner,
    }
    # The section written by the owner's admins carries the owner's authority (02-lld §11.1).
    assert _entry(below(choice["other"], lockedBy={_section(dimension.path): owner}), dimension.path) == {
        "value": choice["other"],
        "setBy": lower,
        "ceilings": [],
        "lockedBy": owner,
    }
    unset = RESOLVER.resolve([ScopedLayer(owner, {"locks": [dimension.path]}), ScopedLayer(lower, _put({}, dimension.layer_path, choice["inside"]))])
    assert unset.refusal is not None
    assert unset.refusal.wire() == {"code": "locked", "dimension": dimension.path, "layer": lower}


@pytest.mark.parametrize(("ladder", "lower"), LADDER_LOWERS, ids=_id)
def test_lk_2_a_locked_ceiling_is_never_widened_below_and_may_still_narrow(ladder: dict[str, Any], lower: str) -> None:
    dimension = REGISTRY.get(ladder["path"])
    owner = _exposed(dimension)[0]

    def chain(upper: object, value: object) -> Resolved:
        return RESOLVER.resolve(
            [ScopedLayer(owner, _put({"locks": [dimension.path]}, dimension.layer_path, upper)), ScopedLayer(lower, _put({}, dimension.layer_path, value))]
        )

    narrowed = _entry(chain(ladder["top"], ladder["narrow"]), dimension.path)
    assert narrowed["value"] == ladder.get("kept", ladder["narrow"])
    assert narrowed["lockedBy"] == owner
    widened = chain(ladder["narrow"], ladder["widen"])
    if dimension.widening == "meet":
        assert _entry(widened, dimension.path)["value"] == ladder.get("kept", ladder["narrow"])
        return
    assert widened.refusal is not None
    assert widened.refusal.wire() == {"code": "locked", "dimension": ladder.get("at", dimension.path), "layer": lower}


def test_lk_3_operator_rows_are_the_deployments_alone() -> None:
    """An operator row has no layer below the deployment that may set it, locked or not (QA2-4 refuses each)."""
    assert all(_exposed(d) == ["deployment"] for d in REGISTRY.all() if d.kind == "operator")


# The rows the property draws from: every ceiling and choice row whose samples need no base layer.
POOLS: dict[str, tuple[object, ...]] = {
    **{ladder["path"]: (ladder["top"], ladder["narrow"], ladder["widen"]) for ladder in LADDERS},
    **{choice["path"]: (choice["inside"], choice["other"]) for choice in CHOICES},
}
CEILING_OF: dict[str, tuple[str, object]] = {
    cast("str", REGISTRY.get(choice["path"]).within): (choice["path"], choice["ceiling"]) for choice in CHOICES
}
CHAIN = [layer for layer in LAYERS if layer not in ("release", "live")]


@st.composite
def locked_chains(draw: st.DrawFn) -> list[ScopedLayer]:
    paths = draw(st.lists(st.sampled_from(sorted(POOLS)), min_size=1, max_size=3, unique=True))
    kinds = sorted(draw(st.lists(st.sampled_from(CHAIN), min_size=2, max_size=5, unique=True)), key=LAYERS.index)
    layers: list[ScopedLayer] = []
    for kind in kinds:
        exposed = [path for path in paths if kind in REGISTRY.get(path).exposure]
        values: dict[str, Any] = {}
        for path in draw(st.lists(st.sampled_from(exposed), unique=True)) if exposed else []:
            _put(values, REGISTRY.get(path).layer_path, draw(st.sampled_from(POOLS[path])))
        locks = draw(st.lists(st.sampled_from(exposed), unique=True)) if exposed else []
        if locks:
            values["locks"] = locks
        if draw(st.booleans()) and paths:
            values["lockedBy"] = {_section(draw(st.sampled_from(paths))): draw(st.sampled_from(kinds))}
        layers.append(ScopedLayer(kind, values))
    # Each choice drawn inside a ceiling that admits both its samples, so a refusal is the lock's or the order's.
    first = layers[0].kind
    for ceiling, (choice, allowed) in CEILING_OF.items():
        if choice in paths and first in REGISTRY.get(ceiling).exposure:
            _put(cast("dict[str, Any]", layers[0].values), REGISTRY.get(ceiling).layer_path, allowed)
    return layers


def _carries(layer: ScopedLayer, path: str, lock: str) -> bool:
    owner = cast("dict[str, Any]", layer.values).get("lockedBy", {}).get(_section(path))
    return owner is not None and LAYERS.index(owner) <= LAYERS.index(cast("Any", lock))


@given(locked_chains())
def test_lk_4_a_locked_value_is_never_overridden_by_a_layer_below_without_its_authority(chain: list[ScopedLayer]) -> None:
    folded = RESOLVER.fold_prefix(chain)
    if not isinstance(folded, FoldedPrefix):
        assert folded.code in ("locked", "widening", "outside_ceiling")
        if folded.code == "widening":
            # A widening of a locked row is named as the lock, unless the layer holds the lock's authority.
            index = [layer.kind for layer in chain].index(folded.layer)
            above = RESOLVER.fold_prefix(chain[:index])
            assert isinstance(above, FoldedPrefix)
            for path, lock in above.locks.items():
                if folded.dimension.startswith(path):
                    assert _carries(chain[index], path, lock)
        return
    for path, lock in folded.locks.items():
        index = next(i for i, layer in enumerate(chain) if path in cast("dict[str, Any]", layer.values).get("locks", []))
        assert chain[index].kind == lock
        at_lock = RESOLVER.fold_prefix(chain[: index + 1])
        assert isinstance(at_lock, FoldedPrefix)
        held = at_lock.entries.get(path)
        final = folded.entries.get(path)
        dimension = REGISTRY.get(path)
        authorised = any(_carries(layer, path, lock) for layer in chain[index + 1 :])
        if dimension.order is None:
            if not authorised:
                held_value = UNDEFINED if held is None else held.value
                final_value = UNDEFINED if final is None else final.value
                assert held_value is UNDEFINED and final_value is UNDEFINED or canonical_digest(held_value) == canonical_digest(final_value), path
        elif held is not None and held.value is not UNDEFINED:
            assert final is not None
            assert dimension.order.leq(final.value, held.value) is None, path


@given(locked_chains(), st.integers(0, 4))
def test_lk_5_a_fold_of_a_prefix_keeps_its_locks(chain: list[ScopedLayer], cut: int) -> None:
    """CPD3-16 with locks: the pinner folds release … agent once and resolves the rest onto it."""
    cut = min(cut, len(chain))
    prefix = RESOLVER.fold_prefix(chain[:cut])
    if not isinstance(prefix, FoldedPrefix):
        return
    whole, split = RESOLVER.resolve(chain), RESOLVER.resolve_from(prefix, chain[cut:])
    assert (whole.digest, whole.refusal) == (split.digest, split.refusal)


def test_lk_5_a_live_narrowing_keeps_the_lock() -> None:
    resolved = RESOLVER.resolve([ScopedLayer("org", {"budgets": {"maxTurns": 10}, "locks": ["budgets.maxTurns"]})])
    assert resolved.config is not None
    live = RESOLVER.live_fold([{"budgets": {"maxTurns": 4}}])
    narrowed = cast("dict[str, dict[str, Any]]", RESOLVER.narrowed_by_live(resolved.config, live)["dimensions"])
    assert narrowed["budgets.maxTurns"]["value"] == 4
    assert narrowed["budgets.maxTurns"]["lockedBy"] == "org"
