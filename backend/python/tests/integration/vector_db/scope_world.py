"""One world of groups and one table of expected answers, shared by the scope-filter suites of every store: each store admits exactly the same
groups for the same viewer (the table is the pgvector answer, and Qdrant, OpenSearch and Redis are held to it)."""
from __future__ import annotations

import pytest

from app.services.vector_db.filters import ScopeFilter

VERSION, GENERATION = 5, 3
TERMS = (3, 7)


def _stamp(kind: str, path: list[str], acl: list[str], *, version: int = VERSION, generation: int = GENERATION) -> dict:
    return {"scopeStamp": kind, "pathIds": path, "aclScope": acl, "scopeFieldsVersion": version, "scopeGeneration": generation}


#: vrid -> (connectors, record groups, root groups, stamp or None)
WORLD: dict[str, tuple[list[str], list[str], list[str], dict | None]] = {
    "container": (["c1"], ["g1"], [], _stamp("container", [], [])),
    "container_other": (["c1"], ["g2"], ["r2"], _stamp("container", [], [])),
    "union": (["c1"], ["g2"], [], _stamp("union", ["s1", "p1"], ["own_u"])),
    "union_hidden": (["c1"], ["g2"], [], _stamp("union", ["s1", "x1"], ["own_h"])),
    "intersection": (["c1"], ["g2"], [], _stamp("intersection", ["s2"], ["own_i"])),
    "intersection_hidden": (["c1"], ["g2"], [], _stamp("intersection", ["s2", "x1"], ["own_ih"])),
    "override": (["c1"], ["g2"], [], _stamp("override", [], ["own_o", "seed"])),
    "unstamped": (["c1"], ["g1"], [], None),
    "legacy": (["c1"], ["g1"], [], _stamp("legacy", ["s1"], ["own_l"])),
    "unknown_stamp": (["c1"], ["g1"], [], _stamp("something", ["s1"], ["own_x"])),
    "old_version": (["c1"], ["g1"], [], _stamp("container", [], [], version=VERSION - 1)),
    "old_generation": (["c1"], ["g1"], [], _stamp("container", [], [], generation=GENERATION - 1)),
    "new_generation": (["c1"], ["g1"], [], _stamp("container", [], [], generation=GENERATION + 1)),
    "other_connector": (["c9"], ["g1"], [], _stamp("container", [], [])),
    "mixed_connectors": (["c1", "c9"], ["g1"], [], _stamp("container", [], [])),
    "no_connector": ([], ["g1"], [], _stamp("container", [], [])),
    # stamped by a process that could not read the scope generation (-2): no reader may trust it, not even one in the same state
    "unavailable_stamp": (["c1"], ["g1"], [], _stamp("container", [], [], generation=-2)),
}
EVERYTHING = frozenset({"g1", "g2", "r2", "s1", "s2", "p1", "own_u", "own_h", "own_i", "own_ih", "own_o", "seed", "own_l", "own_x", "c1", "c9"})


def f(**kw: object) -> ScopeFilter:
    base = {"current": frozenset({"c1"}), "version": VERSION, "generation": GENERATION}
    return ScopeFilter(**{**base, **kw})  # type: ignore[arg-type]


def _cases(rows: list[tuple[dict, set[str]]], ids: list[str]) -> list:
    return [pytest.param(kw, admitted, id=i) for (kw, admitted), i in zip(rows, ids, strict=True)]


ADMIT_CASES = _cases([
    ({"readable_groups": frozenset({"g1"})}, {"container"}),
    ({"readable_groups": frozenset({"g2"})}, {"container_other"}),
    ({"readable_groups": frozenset({"r2"})}, {"container_other"}),
    ({"open_apps": frozenset({"c1"})}, {"container", "container_other"}),
    ({"open_apps": frozenset({"c9"})}, set()),
    ({"readable_scopes": frozenset({"p1"})}, {"union"}),
    ({"readable_scopes": frozenset({"s1"}), "excluded": frozenset({"x1"})}, {"union"}),
    ({"readable_scopes": frozenset({"s2"})}, {"intersection", "intersection_hidden"}),
    ({"readable_scopes": frozenset({"s2"}), "excluded": frozenset({"x1"})}, {"intersection"}),
    ({"readable_scopes": frozenset({"own_ih"}), "excluded": frozenset({"x1"})}, {"intersection_hidden"}),
    ({"readable_scopes": frozenset({"own_u"})}, {"union"}),
    ({"readable_scopes": frozenset({"seed"})}, {"override"}),
    ({"readable_groups": frozenset({"g2"})}, {"container_other"}),
    ({"current": frozenset({"c1", "c9"}), "readable_groups": frozenset({"g1"})}, {"container", "other_connector", "mixed_connectors"}),
    ({}, set()),
], ['record_group', 'other_group', 'root_group', 'open_app', 'open_app_not_current', 'path', 'path_excluded', 'intersection', 'intersection_excluded', 'intersection_own_id', 'own_id', 'override_seed', 'group_not_union', 'two_current_connectors', 'nothing'])

#: a viewer that reads everything, with one thing wrong about what it may trust
TRUST_CASES = _cases([
    ({"generation": -2}, set()), ({"generation": -1}, set()), ({"current": frozenset()}, set()),
    ({"version": VERSION - 1}, {"old_version"}), ({"generation": GENERATION + 1}, {"new_generation"}),
], ['unavailable', 'unknown', 'no current connector', 'another version', 'another generation'])
