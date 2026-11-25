"""ESR-8 / PIN-18 (CPD-7), Python half: the effective-set vectors Node computes.

Python resolves no effective set (03-interfaces §4.3: it reads the pin). It holds each vector's expected set to
the generated model, recomputes its digest with the Python canonical digest, and checks 02-lld §3.4 invariant 6
(every ready tool is granted, enabled, approved, visible and inside ``tools.grant`` and ``tools.effects``) against
the vector's own inputs, so a Node change that breaks the invariant fails here too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.control_plane.contracts.generated.effective_set import EffectiveSet
from harness_core.contracts.canonical_json import canonical_digest

REPO = Path(__file__).resolve().parents[5]
VECTORS: list[dict[str, Any]] = json.loads(
    (REPO / "contracts/control-plane/vectors/effective-set.json").read_text(encoding="utf-8")
)["vectors"]
SETS = [v for v in VECTORS if "set" in v["expected"]]


def _visible(inst: dict[str, Any], vector: dict[str, Any]) -> bool:
    actor, agent = vector["subject"]["actorId"], vector["subject"]["agentKey"]
    owner = inst["owner"]
    return (
        inst["visibility"] == "org"
        or owner == f"user:{actor}"
        or (inst["visibility"] == "team" and owner in {f"team:{t}" for t in vector["actorTeams"]})
        or (agent is not None and vector["agentOwner"] is not None and owner == f"user:{vector['agentOwner']}")
    )


def _granted(vector: dict[str, Any]) -> dict[str, set[str] | None]:
    agent, actor = vector["subject"]["agentKey"], vector["subject"]["actorId"]
    subjects = (
        {f"agent:{agent}"} if agent else {"org", f"user:{actor}", *(f"team:{t}" for t in vector["actorTeams"])}
    )
    granted: dict[str, set[str] | None] = {}
    for grant in vector["grants"]:
        if grant["subject"] not in subjects:
            continue
        for entry in grant["entries"]:
            tools = None if entry["tools"] is None else set(entry["tools"])
            seen = granted.get(entry["installation"], set())
            granted[entry["installation"]] = None if seen is None or tools is None else seen | tools
    return granted


def test_esr8_there_are_vectors_of_both_outcomes() -> None:
    assert SETS
    assert any("refusal" in v["expected"] for v in VECTORS)


@pytest.mark.parametrize("vector", SETS, ids=lambda v: v["id"])
def test_esr8_each_expected_set_validates_and_its_digest_recomputes_in_python(vector: dict[str, Any]) -> None:
    expected = vector["expected"]["set"]
    model = EffectiveSet.model_validate(expected)
    assert model.digest.root == canonical_digest(expected["items"])


@pytest.mark.parametrize("vector", SETS, ids=lambda v: v["id"])
def test_pin18_every_ready_tool_is_granted_enabled_approved_visible_and_inside_the_ceilings(
    vector: dict[str, Any],
) -> None:
    installations = {inst["id"]: inst for inst in vector["installations"]}
    granted = _granted(vector)
    ceiling = vector["dimensions"].get("tools.grant")
    effects = vector["dimensions"].get("tools.effects")
    for item in vector["expected"]["set"]["items"]:
        if item["state"] != "ready":
            continue
        inst = installations[item["installation"]]
        assert item["installation"] in granted
        assert inst["status"] in ("enabled", "upgrade_pending")
        assert vector["dimensions"].get(f"catalog.kinds.{inst['kind']}.allowed") is not False
        assert _visible(inst, vector)
        for tool in item["tools"]:
            name = tool["toolId"].rsplit("/", 1)[1]
            assert inst["approved"]["tools"][name] == tool["descriptorDigest"]
            entry_tools = granted[item["installation"]]
            assert entry_tools is None or name in entry_tools
            if ceiling is not None:
                allowed = ceiling.get(item["installation"], [])
                assert allowed is None or name in allowed
            if effects is not None:
                assert vector["toolEffects"].get(inst["id"], {}).get(name) in effects
