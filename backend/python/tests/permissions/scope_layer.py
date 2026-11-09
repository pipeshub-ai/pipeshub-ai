"""The `ViewerScopes` layer of the conformance runner.

An `AccessLayer` answers "may this viewer read this node". A `ScopeLayer` answers "which containers and scopes
may this viewer read": the resolver's output, in world-local ids, compared with what the product oracle derives.

Oracle-derived scopes (the definition, in code that shares nothing with the Cypher/AQL adapters):
- apps: the apps the viewer passes the gate for (R4);
- groups: record groups of those apps (same organisation) the oracle admits;
- scopes: records and record groups of those apps with an access list of their own: a grant, a rule other than
  OPEN/STRICT, or a hierarchy parent they do not inherit from; a deleted node only when it has children;
- readable scopes: the scopes the oracle admits; unreadable restricted: the scopes that are *restricted* (rule
  other than OPEN/STRICT, a parent not inherited from, or deleted) and not admitted;
- tokens: `u:<user>` and `g:<principal>` for every other principal of R1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from app.services.permissions.fakes import InMemoryHierarchyView, InMemoryPrincipalView
from app.services.permissions.ports import AppFacts, Principal, ScopeNode
from app.services.permissions.rules import uses_exclusion
from app.services.permissions.scopes import ViewerScopes
from app.services.permissions.viewer import ViewerScopeResolver

from . import oracle
from .graph import Graph, Namespace, compile_world

if TYPE_CHECKING:
    from .layers import GraphCheckLayer
    from .model import World

FIELDS = ("gated_apps", "open_apps", "readable_groups", "readable_scopes", "unreadable_restricted", "principal_tokens")
# For these a missing id is the dangerous direction (the filter would admit more); for the others it is an extra id.
HIDING = frozenset({"unreadable_restricted"})


class ScopeLayer(Protocol):
    name: str

    def shape(self, graph: Graph) -> Graph: ...

    async def viewer_scopes(self, world: World, viewer: str) -> ViewerScopes:
        """The viewer's scopes in world-local ids."""
        ...


def _localise(ns: Namespace, scopes: ViewerScopes) -> ViewerScopes:
    def ids(xs: frozenset[str]) -> frozenset[str]:
        return frozenset(ns.local(x) for x in xs)

    tokens = frozenset(f"{t[:2]}{ns.local(t[2:])}" for t in scopes.principal_tokens)
    return ViewerScopes(
        org_id="", user_key=ns.local(scopes.user_key), gated_apps=ids(scopes.gated_apps), open_apps=ids(scopes.open_apps),
        readable_groups=ids(scopes.readable_groups), readable_scopes=ids(scopes.readable_scopes),
        unreadable_restricted=ids(scopes.unreadable_restricted), principal_tokens=tokens,
        pending=ids(scopes.pending), blocked=ids(scopes.blocked), legacy_apps=ids(scopes.legacy_apps), version=scopes.version)


# ---- the oracle-derived scopes -------------------------------------------------------------------------------

def scope_facts(s: oracle.Store, apps: set[str], org: str) -> tuple[set[str], list[ScopeNode]]:
    """(record groups, scopes) of these apps in this organisation, by the definition in the module doc."""
    groups: set[str] = set()
    scopes: list[ScopeNode] = []
    special = {a: _special(s, a) for a in apps}
    for n in s.nodes:
        p = n["props"]
        if n["kind"] not in ("Record", "RecordGroup") or p.get("connectorId") not in apps or p.get("orgId") != org:
            continue
        nid = n["id"]
        if n["kind"] == "RecordGroup":
            groups.add(nid)
        deleted = bool(p.get("isDeleted"))
        rule = p.get("accessRule")
        bad = rule is not None and rule not in ("OPEN", "STRICT")
        breaks = any(not oracle._inherits(s, nid, parent) for parent in oracle._parents(s, nid))
        has_child = any(e["props"].get("relationshipType") in oracle.HIERARCHY for e in s.out[("NODE_RELATION", nid)])
        own_list = bad or breaks or bool(s.inc[("PERMISSION", nid)])
        inherits_a_parent = any(oracle._inherits(s, nid, parent) for parent in oracle._parents(s, nid))
        needs_grant = deleted or (not special[p["connectorId"]] and (bad or not inherits_a_parent))
        if has_child if deleted else own_list:
            scopes.append(ScopeNode(nid, p["connectorId"], deleted or bad or breaks, needs_grant, deleted or bad))
    return groups, scopes


def _opens(s: oracle.Store, app: str) -> bool:
    p = s.p(app)
    return p.get("permissionModel") == "APP_LEVEL" or p.get("type") == "KB"


def _record_group_level(s: oracle.Store, app: str) -> bool:
    return any(n["kind"] == "RecordGroup" and n["props"].get("permissionModel") == oracle.RGL
               and n["props"].get("connectorId") == app for n in s.nodes)


def _special(s: oracle.Store, app: str) -> bool:
    """Something other than a direct grant can admit a node of the app: it opens everything (R12) or one of its
    record groups declares its own readers (R8)."""
    p = s.p(app)
    return p.get("permissionModel") == "APP_LEVEL" or p.get("type") == "KB" or any(
        n["kind"] == "RecordGroup" and n["props"].get("permissionModel") == oracle.RGL
        and n["props"].get("connectorId") == app for n in s.nodes)


def oracle_scopes(graph: Graph, user: str, org: str) -> ViewerScopes:
    """The oracle-derived scopes of one viewer, in the graph's ids."""
    s = oracle.Store(graph)
    who = oracle.viewer(s, user, org)
    # An Intersection app with record-group-level groups is left out (see ViewerScopeResolver): named, never described.
    legacy = frozenset(a for a in who.apps if _record_group_level(s, a) and uses_exclusion(str(s.p(a).get("type") or "")))
    groups, scopes = scope_facts(s, set(who.apps) - legacy, org)
    readable = {n for n in groups | {x.id for x in scopes} if oracle.verdict(s, who, n).admitted}
    tokens = frozenset({f"u:{user}", *(f"g:{p}" for p in who.ps if p != user)})
    return ViewerScopes(
        org_id=org, user_key=user, gated_apps=frozenset(who.apps) - legacy, legacy_apps=legacy,
        open_apps=frozenset(a for a in who.apps if _opens(s, a) and a not in legacy), readable_groups=frozenset(groups & readable),
        readable_scopes=frozenset(x.id for x in scopes if x.id in readable),
        unreadable_restricted=frozenset(x.id for x in scopes if x.restricted and x.id not in readable
                                        and (x.rule_restricted or uses_exclusion(str(s.p(x.connector_id).get("type") or "")))),
        principal_tokens=tokens)


class OracleScopeLayer:
    """The reference: oracle-derived scopes of a world, with the layer's storage shape applied."""

    PREFIX = "o"

    def __init__(self, shape=lambda g: g, name: str = "oracle") -> None:
        self.name = name
        self._shape = shape
        self._ns = Namespace(self.PREFIX)
        self._graphs: dict[World, Graph] = {}

    def shape(self, graph: Graph) -> Graph:
        return self._shape(graph)

    async def viewer_scopes(self, world: World, viewer: str) -> ViewerScopes:
        if world not in self._graphs:
            self._graphs[world] = self._shape(compile_world(world, self.PREFIX))
        return _localise(self._ns, oracle_scopes(self._graphs[world], self._ns.id(viewer), self._ns.org))


# ---- the layers under test -----------------------------------------------------------------------------------

class FakeScopeLayer:
    """The resolver over the in-memory fakes, fed from the oracle's facts. No store: unit tests."""

    name = "fakes"
    PREFIX = "f"

    def shape(self, graph: Graph) -> Graph:
        return graph

    async def viewer_scopes(self, world: World, viewer: str) -> ViewerScopes:
        ns = Namespace(self.PREFIX)
        s = oracle.Store(compile_world(world, self.PREFIX))
        who = oracle.viewer(s, ns.id(viewer), ns.org)
        groups, scopes = scope_facts(s, who.apps, ns.org)
        admitted = frozenset(n for n in groups | {x.id for x in scopes} if oracle.verdict(s, who, n).admitted)
        by_app = {a: frozenset(g for g in groups if s.p(g).get("connectorId") == a) for a in who.apps}
        facts = {a: AppFacts(_opens(s, a), str(s.p(a).get("type") or ""), _record_group_level(s, a)) for a in who.apps}
        key = (ns.org, ns.id(viewer))
        tokens = frozenset({f"u:{ns.id(viewer)}", *(f"g:{p}" for p in who.ps if p != ns.id(viewer))})
        resolver = ViewerScopeResolver(
            InMemoryHierarchyView(ns.org, by_app, tuple(scopes), facts),
            InMemoryPrincipalView({key: Principal(tokens, frozenset(who.apps), frozenset(who.granted))}, {key: admitted}))
        return _localise(ns, await resolver.resolve(ns.org, ns.id(viewer)))


class GraphScopeLayer:
    """The resolver over the real adapters of a graph layer (Neo4j or ArangoDB)."""

    def __init__(self, layer: GraphCheckLayer, resolver_for) -> None:
        self.name = f"{layer.name}-scopes"
        self._layer = layer
        self._resolver = resolver_for(layer)

    def shape(self, graph: Graph) -> Graph:
        return self._layer.shape(graph)

    async def viewer_scopes(self, world: World, viewer: str) -> ViewerScopes:
        ns = await self._layer._ns(world)
        return _localise(ns, await self._resolver.resolve(ns.org, ns.id(viewer)))


def resolver_for(layer: GraphCheckLayer) -> ViewerScopeResolver:
    """The production adapters over the provider of a graph layer."""
    from app.services.permissions.adapters import GraphPrincipalView

    provider = layer._provider
    return ViewerScopeResolver(provider.permission_hierarchy_view(), GraphPrincipalView(provider))


# ---- the runner ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ScopeFinding:
    scenario: str
    layer: str
    viewer: str
    field: str
    kind: str  # over-admit (the filter would show more than the oracle) | under-admit
    ids: tuple[str, ...]


@dataclass
class ScopeReport:
    viewers: int = 0
    findings: list[ScopeFinding] = field(default_factory=list)

    @property
    def over_admits(self) -> list[ScopeFinding]:
        return [f for f in self.findings if f.kind == "over-admit"]

    def merge(self, other: ScopeReport) -> None:
        self.viewers += other.viewers
        self.findings += other.findings

    def summary(self) -> str:
        return f"viewers={self.viewers} over-admits={len(self.over_admits)} findings={len(self.findings)}"


def compare(want: ViewerScopes, got: ViewerScopes, scenario: str, layer: str, viewer: str) -> list[ScopeFinding]:
    out: list[ScopeFinding] = []
    for name in FIELDS:
        w, g = getattr(want, name), getattr(got, name)
        extra, missing = tuple(sorted(g - w)), tuple(sorted(w - g))
        hiding = name in HIDING
        if extra:
            out.append(ScopeFinding(scenario, layer, viewer, name, "under-admit" if hiding else "over-admit", extra))
        if missing:
            out.append(ScopeFinding(scenario, layer, viewer, name, "over-admit" if hiding else "under-admit", missing))
    return out


class ScopeConformanceRunner:
    """Every viewer of a world: the layer's scopes against the oracle-derived ones."""

    def __init__(self, layer: ScopeLayer) -> None:
        self.layer = layer
        self._reference = OracleScopeLayer(layer.shape)

    async def check_world(self, world: World, scenario_id: str = "") -> ScopeReport:
        report = ScopeReport()
        for viewer in world.users():
            want = await self._reference.viewer_scopes(world, viewer)
            got = await self.layer.viewer_scopes(world, viewer)
            report.viewers += 1
            report.findings += compare(want, got, scenario_id, self.layer.name, viewer)
        return report
