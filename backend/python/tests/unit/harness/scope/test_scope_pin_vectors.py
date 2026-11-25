"""SYNC4 S1, Python's half of ``contracts/harness/vectors/scope-pin.json``: the knowledge pin Node writes is read back
as the filters main's turn scope resolves on every attempt (Node's half: ``scope-pin.test.ts``).

Written test-first, before ``origin/main`` was merged: red until S1 (the pin's schema has no selection fields).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.assembly.context import RunContext
from tests.unit.harness.assembly import world as w

VECTOR: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/scope-pin.json").read_text(encoding="utf-8")
)
PINS = VECTOR["pins"]


def _run(case: dict[str, Any]) -> RunContext:
    agent = case["definition"] is not None
    spec = w.pinned_spec(
        tool_ids=[],
        kb=[],
        agent=agent,
        chat=None if agent else {"mode": "internal_search", "internalSearch": True},
        strict_scope=agent,
    )
    spec.extensions = {**(spec.extensions or {}), "x.pipeshub": {**(spec.extensions or {})["x.pipeshub"], "knowledge": case["pinned"]}}
    return RunContext.from_claim(w.claim(spec))


@pytest.mark.parametrize("case", PINS, ids=[c["id"] for c in PINS])
def test_s1_the_pin_is_read_back_as_the_requested_filters(case: dict[str, Any]) -> None:
    from app.harness.assembly.scope import requested_filters

    assert requested_filters(_run(case).knowledge) == case["requested"]


@pytest.mark.parametrize("case", [c for c in PINS if "sourceLimits" in c["pinned"]], ids=lambda c: c["id"])
def test_s12_the_agents_pinned_limits_are_read_back(case: dict[str, Any]) -> None:
    limits = _run(case).knowledge.source_limits
    assert {k: (list(v.record_groups), list(v.records)) for k, v in limits.items()} == {
        item["connectorId"]: (item["recordGroups"], item["records"]) for item in case["pinned"]["sourceLimits"]
    }


@pytest.mark.parametrize(
    ("pinned", "live", "effective"),
    [
        # Pinned whole, live limited: the owner narrowed it, the narrower applies.
        (None, {"recordGroups": ["grp-1"], "records": []}, {"recordGroups": ["grp-1"], "records": []}),
        # Pinned limited, live whole: the owner widened it, the pin holds.
        ({"recordGroups": ["grp-1"], "records": []}, None, {"recordGroups": ["grp-1"], "records": []}),
        # Both limited: what both name.
        ({"recordGroups": ["grp-1", "grp-2"], "records": ["rec-1"]}, {"recordGroups": ["grp-2", "grp-3"], "records": []},
         {"recordGroups": ["grp-2"], "records": []}),
        # Both limited, nothing in common: the source is not searched at all, never whole.
        ({"recordGroups": ["grp-1"], "records": []}, {"recordGroups": ["grp-9"], "records": []}, "dropped"),
        # Pinned to nothing (the limit could not be read at admission): never searched.
        ({"recordGroups": [], "records": []}, None, "dropped"),
        # The owner narrowed to a folder under the pinned group: the folder is inside both, so it is kept.
        ({"recordGroups": ["grp-1"], "records": []}, {"recordGroups": [], "records": ["fld-1"]},
         {"recordGroups": [], "records": ["fld-1"]}),
        # ... and to a record under the pinned group, found through its own group edge.
        ({"recordGroups": ["grp-1"], "records": []}, {"recordGroups": [], "records": ["rec-out", "rec-g2"]},
         {"recordGroups": [], "records": ["rec-out"]}),
        # The owner widened to the group above a pinned folder: the pinned folder holds.
        ({"recordGroups": [], "records": ["fld-1"]}, {"recordGroups": ["grp-1"], "records": []},
         {"recordGroups": [], "records": ["fld-1"]}),
        # A record pinned under a folder the owner now limits to: the record holds.
        ({"recordGroups": [], "records": ["rec-in"]}, {"recordGroups": [], "records": ["fld-1"]},
         {"recordGroups": [], "records": ["rec-in"]}),
        # Below another group than the pinned one: nothing in common.
        ({"recordGroups": ["grp-2"], "records": []}, {"recordGroups": [], "records": ["fld-1"]}, "dropped"),
    ],
)
@pytest.mark.parametrize("shape", ["neo4j", "arango"])
async def test_s12_live_limits_are_intersected_with_the_pinned_ones(
    pinned: dict[str, list[str]] | None, live: dict[str, list[str]] | None, effective: object, shape: str,
) -> None:
    from unittest.mock import MagicMock

    from app.harness.assembly.context import SourceLimit
    from app.harness.assembly.scope import bounded_agent_knowledge
    from app.modules.retrieval import selection_scope
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from tests.support import scope_world as sw

    selection_scope._memo.clear()
    graph = sw.ScopeWorld.build().install(MagicMock(spec=IGraphDBProvider), shape=shape)
    entry: dict[str, Any] = {"connectorId": "app-a", "type": "CONNECTOR"}
    if live is not None:
        entry["filters"] = json.dumps(live)
    limits = {} if pinned is None else {
        "app-a": SourceLimit(record_groups=tuple(pinned["recordGroups"]), records=tuple(pinned["records"]))
    }
    bounded = await bounded_agent_knowledge([entry], limits, graph, sw.ORG)
    if effective == "dropped":
        assert bounded == []
        return
    (only,) = bounded
    stored = only.get("filters")
    parsed = json.loads(stored) if isinstance(stored, str) else stored
    assert parsed == effective
