"""EP-7, EP-8, EP-9 (E4a): the vector table Node shares (contracts/harness/vectors/engine-profiles.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.errors import ProfileInvalid, ProfileWidening
from harness_core.engines.isolation import OVERRIDE_ENV
from harness_core.engines.profiles import (
    ProfileLayer,
    constraint_from_wire,
    constraint_json,
    profile_digest,
    profile_from_wire,
    profile_json,
    resolve_profile,
)
from harness_engines.release import release_profiles

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.engines.profiles import AcpOptIn

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/engine-profiles.json").read_text(
        encoding="utf-8"
    )
)
ACCESS = AccessRouteRef("route", "digest")


def test_ep7_the_release_profiles_are_this_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    assert [profile_json(p) for p in release_profiles(ACCESS)] == VECTORS["release"]


def _resolve(layers: dict[str, Any], acp_opt_in: AcpOptIn = "release") -> dict[str, Any]:
    releases = [profile_from_wire(r, access=ACCESS) for r in VECTORS["releases"]]
    try:
        parsed = [ProfileLayer(name, constraint_from_wire(raw)) for name, raw in layers.items()]
        profile = resolve_profile(releases, parsed, acp_opt_in)
    except ProfileWidening as refused:
        return {"refusal": {"code": "widening", "dimension": refused.field, "layer": refused.layer}}
    except ProfileInvalid:
        return {"refusal": {"code": "invalid"}}
    return {"profile": profile_json(profile), "profileDigest": profile_digest(profile)}


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_ep7_every_case_resolves_as_the_table_says(case: dict[str, Any]) -> None:
    assert _resolve(case["layers"], case.get("acpOptIn", "release")) == case["expect"]


@pytest.mark.parametrize(
    "case", [c for c in VECTORS["cases"] if "refusal" not in c["expect"]], ids=lambda case: case["id"]
)
def test_ep7_a_pinned_profile_round_trips_and_keeps_its_digest(case: dict[str, Any]) -> None:
    profile = profile_from_wire(case["expect"]["profile"], access=ACCESS)
    assert profile_json(profile) == case["expect"]["profile"]
    assert profile_digest(profile) == case["expect"]["profileDigest"]
    for raw in case["layers"].values():
        constraint = constraint_from_wire(raw)
        assert constraint_from_wire(constraint_json(constraint)) == constraint


def test_ep8_the_table_covers_every_shared_dimension_both_ways() -> None:
    refused = {c["expect"]["refusal"].get("dimension", "").split(".")[0] for c in VECTORS["cases"] if "refusal" in c["expect"]}
    narrowed = {key for c in VECTORS["cases"] if "profile" in c["expect"] for layer in c["layers"].values() for key in layer}
    shared = {"engine", "mode", "isolationMin", "egress", "tools", "limits", "subAgents", "reasoningEffort", "approvals", "skills"}
    assert shared <= refused
    assert shared - {"engine"} <= narrowed and "engines" in narrowed
    assert {"invalid"} <= {c["expect"]["refusal"]["code"] for c in VECTORS["cases"] if "refusal" in c["expect"]}


@pytest.mark.parametrize(
    "raw",
    [
        {"limits": {"maxSteps": None}},
        {"skills": None},
        {"network": "open"},
        {"engines": ["opencode", "opencode"]},
        {"tools": {"effects": ["read", "read"]}},
        {"skills": ["pdf", "pdf"]},
        {"approvals": {"network": "deny"}},
        {"limits": {"maxSteps": "50"}},
        [],
        "plan",
    ],
)
def test_ep9_a_layer_that_is_not_a_narrowing_is_invalid_never_absent(raw: object) -> None:
    with pytest.raises(ProfileInvalid) as refused:
        constraint_from_wire(raw)
    assert refused.value.reason_code == "profile_invalid"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("skills"),
        lambda p: p.update(mode="yolo"),
        lambda p: p["engine"].update(kind="vim"),
        lambda p: p["tools"].update(effects=["read", "read"]),
    ],
)
def test_ep9_a_pinned_profile_that_does_not_parse_is_invalid(mutate: Callable[[dict[str, Any]], object]) -> None:
    raw = json.loads(json.dumps(VECTORS["release"][0]))
    mutate(raw)
    with pytest.raises(ProfileInvalid):
        profile_from_wire(raw, access=ACCESS)
