"""A structural change as the scope filter sees it while the stamps are still the old ones.

For a scenario whose change moves a record, breaks or restores its inheritance, or sets its rule, the index still holds the stamps of the world
BEFORE the change, while every viewer's scopes already come from the world AFTER it. The records the change reaches are pending roots (their
connectors are blocked). The filter must then admit no point of the changed subtree, and no point the oracle denies in the new world.
Shared by the unit test (tree and SQL rule) and the pgvector test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.services.permissions.projector import ScopeProjector
from app.services.vector_db.filters import ScopeFilter

from .model import HIERARCHY_RELATIONS
from .runner import OracleLayer
from .scenario import load_all
from .scope_layer import OracleScopeLayer
from .stamp_world import ORG, stamp_graph

if TYPE_CHECKING:
    from app.services.permissions.scopes import ViewerScopes

    from .model import Mutation, World
    from .scenario import Scenario

STRUCTURAL = frozenset({"moved", "inherit_set", "rule_set"})


def roots_of(world: World, mutations: tuple[Mutation, ...]) -> dict[str, set[str]] | None:
    """connector -> the records the change registers as roots; None when it reaches more than a record's subtree (a group, a delete)."""
    out: dict[str, set[str]] = {}
    for m in mutations:
        if m.kind not in STRUCTURAL:
            continue
        node = world.node(m.node)
        if node.kind != "record" or not node.connector:
            return None
        out.setdefault(node.connector, set()).add(node.id)
    return out


def descendants(world: World, roots: set[str]) -> set[str]:
    below: dict[str, set[str]] = {}
    for n in world.nodes:
        for p in n.parents:
            if p.rel in HIERARCHY_RELATIONS:
                below.setdefault(p.id, set()).add(n.id)
    seen, frontier = set(roots), list(roots)
    while frontier:
        nxt = [c for i in frontier for c in below.get(i, ()) if c not in seen]
        seen.update(nxt)
        frontier = nxt
    return seen


SCENARIOS = [s for s in load_all() if s.mutations and any(m.kind in STRUCTURAL for m in s.mutations) and roots_of(s.world, s.mutations)]


@dataclass
class Corpus:
    scenario: str
    #: record id -> the payload its point holds (the stamps of the world before the change)
    points: dict[str, dict] = field(default_factory=dict)
    #: viewer -> (scopes of the world after the change, with the pending roots; record id -> may the viewer read it now)
    viewers: dict[str, tuple[ViewerScopes, dict[str, bool]]] = field(default_factory=dict)
    subtree: set[str] = field(default_factory=set)
    blocked: frozenset[str] = frozenset()
    current: frozenset[str] = frozenset()
    version: int = 0
    generation: int = 0

    def scope_filter(self, scopes: ViewerScopes) -> ScopeFilter:
        return ScopeFilter.of(scopes, self.current, version=self.version, generation=self.generation, blocked=self.blocked)


async def build(scenario: Scenario) -> Corpus:
    before, after = scenario.world, scenario.final_world()
    roots = roots_of(before, scenario.mutations) or {}
    flat = {r for rs in roots.values() for r in rs}
    graph = stamp_graph(before)
    projector = ScopeProjector(graph)
    out = Corpus(scenario.id, subtree=descendants(before, flat) | descendants(after, flat), blocked=frozenset(roots))
    out.current = frozenset(n.id for n in after.nodes if n.kind == "app")
    for app in (n for n in before.nodes if n.kind == "app"):
        recs = [n.id for n in before.nodes if n.kind == "record" and n.connector == app.id]
        for rid, p in (await projector.project(ORG, app.id, recs)).items():
            if p.skipped:
                continue
            node = graph.node_map[rid]
            out.version, out.generation = p.fields.version, p.fields.generation
            out.points[rid] = {"connectorIds": [app.id], "recordGroupIds": list(node.groups), "rootRecordGroupIds": [],
                               **p.fields.to_payload(), **({"scopeStamp": "legacy"} if p.refused else {})}
    scope_layer, oracle = OracleScopeLayer(), OracleLayer()
    ids = [n.id for n in after.nodes]
    for viewer in after.users():
        scopes = await scope_layer.viewer_scopes(after, viewer)
        truth = oracle.explain(after, viewer, ids)
        out.viewers[viewer] = (replace(scopes, pending=frozenset(flat)),
                               {r: truth[r].admitted for r in out.points if r in truth})
    return out


def leaks(corpus: Corpus, admitted: dict[str, set[str]]) -> list[tuple[str, str, str]]:
    """(viewer, record, why) for every point a viewer is admitted that it must not be: inside the changed subtree, or denied by the new world."""
    found = []
    for viewer, (_, truth) in corpus.viewers.items():
        for rid in sorted(admitted.get(viewer, ())):
            if rid in corpus.subtree:
                found.append((viewer, rid, "in the changed subtree"))
            elif not truth.get(rid, False):
                found.append((viewer, rid, "denied by the new world"))
    return found
