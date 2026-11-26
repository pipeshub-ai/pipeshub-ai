"""QA2-7, QA2-8: the engine-profile resolver held to the stricter-only ladders and the requested-effort table
Node shares (``contracts/harness/vectors/stricter-only.json``): NO-7's property, as vectors."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.contracts.generated.enums import REASONING_EFFORTS, REQUESTED_EFFORTS
from harness_core.engines.errors import ProfileWidening
from harness_core.engines.profiles import (
    ProfileLayer,
    constraint_from_wire,
    profile_from_wire,
    profile_json,
    resolve_profile,
)
from harness_core.policy import ENGINE_PROFILE, policy_registry

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/stricter-only.json").read_text(encoding="utf-8")
)
RELEASE = profile_from_wire(VECTORS["release"], access=AccessRouteRef("route", "digest"))
REGISTRY = policy_registry(ENGINE_PROFILE)
PROFILE_LAYERS = ("org", "agent", "request")
LADDERS: list[dict[str, Any]] = [
    {**ladder, "path": ladder["path"].removeprefix(f"{ENGINE_PROFILE}.")}
    for ladder in VECTORS["ladders"]
    if ladder["path"].startswith(f"{ENGINE_PROFILE}.") and not ladder.get("hostOnly")
]
EFFORTS: list[dict[str, Any]] = VECTORS["efforts"]


def _put(values: dict[str, Any], path: str, value: object) -> dict[str, Any]:
    *parents, last = path.split(".")
    node = values
    for key in parents:
        node = node.setdefault(key, {})
    node[last] = value
    return values


def _layer(name: str, ladder: dict[str, Any], rung: str) -> ProfileLayer:
    raw = _put({}, REGISTRY.get(ladder["path"]).layer_path, ladder[rung])
    return ProfileLayer(name, constraint_from_wire(raw))


@pytest.mark.parametrize("ladder", LADDERS, ids=lambda ladder: ladder["path"])
@pytest.mark.parametrize("layer", PROFILE_LAYERS)
def test_qa2_7_a_narrowing_in_any_profile_layer_is_kept_and_changes_nothing_else(
    ladder: dict[str, Any], layer: str
) -> None:
    kept = ladder.get("kept", ladder["narrow"])
    expected = _put(copy.deepcopy(VECTORS["release"]), ladder["path"], kept)
    assert profile_json(resolve_profile([RELEASE], [_layer(layer, ladder, "narrow")])) == expected


@pytest.mark.parametrize("ladder", LADDERS, ids=lambda ladder: ladder["path"])
@pytest.mark.parametrize(("upper", "lower"), list(combinations(PROFILE_LAYERS, 2)))
def test_qa2_7_a_widening_in_a_lower_profile_layer_is_refused_naming_it(
    ladder: dict[str, Any], upper: str, lower: str
) -> None:
    with pytest.raises(ProfileWidening) as refused:
        resolve_profile([RELEASE], [_layer(upper, ladder, "narrow"), _layer(lower, ladder, "widen")])
    at = ladder.get("at", f"{ENGINE_PROFILE}.{ladder['path']}").removeprefix(f"{ENGINE_PROFILE}.")
    assert (refused.value.field, refused.value.layer) == (at, lower)


def test_qa2_8_every_requested_effort_narrows_to_a_profile_effort_never_above_the_ask() -> None:
    ranks = policy_registry().get(f"{ENGINE_PROFILE}.reasoningEffort")
    assert ranks.order is not None
    bottom = REASONING_EFFORTS[0]
    assert all(ranks.order.leq(bottom, effort) is None for effort in REASONING_EFFORTS)
    narrowing = {row["requested"]: row["narrowing"] for row in EFFORTS}
    assert set(narrowing) == {None, *REQUESTED_EFFORTS}
    assert narrowing[None] is None
    for requested in REQUESTED_EFFORTS:
        assert narrowing[requested] == (requested if requested in REASONING_EFFORTS else bottom)
    asked = [REASONING_EFFORTS.index(narrowing[requested]) for requested in REQUESTED_EFFORTS]
    assert asked == sorted(asked)


@pytest.mark.parametrize(
    ("row", "at"),
    [(row, at) for row in EFFORTS for at in row["byCeiling"]],
    ids=lambda value: str(value.get("requested", value.get("ceiling"))),
)
def test_qa2_8_the_pinned_effort_is_at_or_below_the_ceiling_or_the_request_is_refused(
    row: dict[str, Any], at: dict[str, Any]
) -> None:
    layers = [
        ProfileLayer(name, constraint_from_wire({"reasoningEffort": effort}))
        for name, effort in (("org", at["ceiling"]), ("request", row["narrowing"]))
        if effort is not None
    ]
    release = replace(RELEASE, reasoning_effort=None)
    if at.get("refused"):
        with pytest.raises(ProfileWidening) as refused:
            resolve_profile([release], layers)
        assert (refused.value.field, refused.value.layer) == ("reasoningEffort", "request")
        return
    pinned = resolve_profile([release], layers).reasoning_effort
    assert pinned == at["pinned"]
    if at["ceiling"] is not None:
        assert pinned is not None
        assert REASONING_EFFORTS.index(pinned) <= REASONING_EFFORTS.index(at["ceiling"])
