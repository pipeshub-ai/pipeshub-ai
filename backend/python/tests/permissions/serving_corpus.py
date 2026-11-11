"""The scenario corpus as a serving search sees it: the index holds the stamps the projector computes from a world, the viewer's scopes and the
oracle's verdicts come from the same world, and every connector is served.

For each viewer and record the filter a search carries (`stamp_filter`, deferring) either decides the point by its stamp or leaves it to the
request's own arms and the post-check. A decided point must agree with the oracle both ways: admitted when the oracle denies is an over-admit
(never allowed), hidden when the oracle admits is a recall loss. A left-over point is the legacy request's. Shared by the unit test (the tree) and
the pgvector test (the rows).
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.permissions.projector import ScopeProjector
from app.services.permissions.serving import stamp_filter

from .runner import OracleLayer
from .scenario import load_all
from .scope_layer import OracleScopeLayer
from .stamp_world import ORG, stamp_graph

if TYPE_CHECKING:
    from app.services.permissions.scopes import ViewerScopes
    from app.services.vector_db.filters import ScopeFilter

    from .model import World
    from .scenario import Scenario

SCENARIOS = load_all()
LEGACY = "legacy"


@dataclass
class ServingCorpus:
    scenario: str
    #: record id -> the payload its point holds
    points: dict[str, dict] = field(default_factory=dict)
    #: viewer -> (scopes, record id -> may the viewer read it)
    viewers: dict[str, tuple[ViewerScopes, dict[str, bool]]] = field(default_factory=dict)
    connectors: frozenset[str] = frozenset()

    def scope_filter(self, scopes: ViewerScopes, *, served: frozenset[str] | None = None, defer: bool = True) -> ScopeFilter:
        return stamp_filter(scopes, self.connectors if served is None else served, defer=defer)

    def refused(self, rid: str) -> bool:
        """Whether the stamper left the record to the legacy filter."""
        return self.points[rid]["scopeStamp"] == LEGACY


def roots_of(world: World, groups: tuple[str, ...]) -> list[str]:
    """The top of each group's chain (`rootRecordGroupIds`): never the first edge, no store has one."""
    roots = set()
    for start in groups:
        top, seen = start, {start}
        while up := [b for b in world.node(top).belongs if world.node(b).kind == "group" and b not in seen]:
            top = up[0]
            seen.add(top)
        roots.add(top)
    return sorted(roots)


async def build(scenario: Scenario, world: World | None = None) -> ServingCorpus:
    """The corpus of ``world`` (default: the scenario's world after its mutations, as if every rewrite had completed)."""
    world = world or scenario.final_world()
    graph = stamp_graph(world)
    projector = ScopeProjector(graph)
    out = ServingCorpus(scenario.id, connectors=frozenset(n.id for n in world.nodes if n.kind == "app"))
    for app in (n for n in world.nodes if n.kind == "app"):
        for rid, p in (await projector.project(ORG, app.id, [n.id for n in world.nodes if n.kind == "record" and n.connector == app.id])).items():
            if p.skipped:
                continue
            node = graph.node_map[rid]
            out.points[rid] = {"connectorIds": [app.id], "recordGroupIds": list(node.groups), "rootRecordGroupIds": roots_of(world, node.groups),
                               **p.fields.to_payload(), **({"scopeStamp": LEGACY, "scopeFieldsVersion": 0} if p.refused else {})}
    scope_layer, oracle = OracleScopeLayer(), OracleLayer()
    ids = [n.id for n in world.nodes]
    for viewer in world.users():
        truth = oracle.explain(world, viewer, ids)
        out.viewers[viewer] = (await scope_layer.viewer_scopes(world, viewer), {r: truth[r].admitted for r in out.points if r in truth})
    return out


def legacy_returns(scopes: ViewerScopes, rids: Iterable[str], payload: dict) -> bool:
    """Whether the request's own container arms (`AccessibleContainers`) return the point: a connector the viewer passes the gate for (the arms carry
    every gated app, not only those that open everything), a record group or root group the viewer reads, or a record granted to the viewer directly."""
    return bool(set(payload["connectorIds"]) & scopes.gated_apps or {*payload["recordGroupIds"], *payload["rootRecordGroupIds"]} & scopes.readable_groups
                or any(r in scopes.readable_scopes for r in rids))


@dataclass
class Verdict:
    #: (viewer, record) the filter admits and the oracle denies (never allowed)
    over: list[tuple[str, str]] = field(default_factory=list)
    #: the filter hides, the oracle admits, and the container arms return it: what serving costs
    lost: list[tuple[str, str]] = field(default_factory=list)
    #: the oracle admits and neither the arms nor the filter return it: the legacy request misses it too
    unreached: list[tuple[str, str]] = field(default_factory=list)


def judge(corpus: ServingCorpus, admitted: dict[str, set[str]], served: frozenset[str] | None = None) -> Verdict:
    """``admitted`` is the records each viewer's search returned. A record the stamper left to the legacy filter, or of a connector that is not
    in ``served``, is the legacy request's (the post-check decides it), so it is none of the three."""
    out = Verdict()
    served = corpus.connectors if served is None else served
    for viewer, (scopes, truth) in corpus.viewers.items():
        for rid, payload in corpus.points.items():
            if corpus.refused(rid) or not set(payload["connectorIds"]) <= served:
                continue
            got, may = rid in admitted.get(viewer, ()), truth.get(rid, False)
            if got and not may:
                out.over.append((viewer, rid))
            elif may and not got:
                (out.lost if legacy_returns(scopes, (rid,), payload) else out.unreached).append((viewer, rid))
    return out
