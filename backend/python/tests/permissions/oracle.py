"""Reference model of PipesHub's read-access rule (rules R1-R13).

Written from the rule's description, not from the Cypher or AQL, so a
disagreement with a graph provider is a finding. It answers what
`IGraphDBProvider.check_access` answers, plus *why*: every verdict carries a
reason code naming the rule or hop that decided it.

Rules: R1 principals, R2 access rule (absent reads OPEN, unknown fails closed),
R3 grant edges, R4 gate, R5 granted set, R6 hop, R7 walk up the hierarchy,
R8 declared record-group-level arm, R9 seed, R10 below a seed, R11 target
guards, R12 open apps (APP_LEVEL / KB), R13 unattached uploads. Inheritance
along a walk is an intersection: every hop passes on its own.

Standard library only, apart from the shared graph types, so another store can
be checked against it by loading the same graph.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph import EdgeDict, Graph, NodeDict, Scalar

HIERARCHY = {"PARENT_CHILD", "ATTACHMENT"}
ORG_SHARE_TYPES = {"ORG", "ORGANIZATION"}
RGL = "RECORD_GROUP_LEVEL"
MAX_HOPS = 50
SEARCHABLE = {"COMPLETED"}


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    reason: str


class Store:
    """Indexed view of a graph."""

    def __init__(self, graph: Graph) -> None:
        self.by_id: dict[str, NodeDict] = {n["id"]: n for n in graph["nodes"]}
        self.nodes = graph["nodes"]
        self.out: dict[tuple[str, str], list[EdgeDict]] = defaultdict(list)
        self.inc: dict[tuple[str, str], list[EdgeDict]] = defaultdict(list)
        for e in graph["edges"]:
            if e["from"] in self.by_id and e["to"] in self.by_id:
                self.out[(e["type"], e["from"])].append(e)
                self.inc[(e["type"], e["to"])].append(e)

    def kind(self, node_id: str | None) -> str | None:
        return self.by_id[node_id]["kind"] if node_id in self.by_id else None

    def p(self, node_id: str) -> dict[str, Scalar]:
        return self.by_id[node_id]["props"]


@dataclass
class Viewer:
    user: str
    org: str
    ps: set[str]
    apps: set[str]
    granted: set[str]


def _rule(s: Store, n: str) -> Scalar:
    r = s.p(n).get("accessRule")
    return "OPEN" if r is None else r  # R2


def _deleted(s: Store, n: str) -> bool:
    return bool(s.p(n).get("isDeleted"))


def principals(s: Store, user: str) -> set[str]:
    """R1: the user, groups/roles/teams one membership edge away, the organization."""
    if s.kind(user) != "User":
        return set()
    ps = {user}
    for e in s.out[("PERMISSION", user)]:
        if e["props"].get("type") == "USER" and s.kind(e["to"]) in ("Group", "Role", "Teams"):
            ps.add(e["to"])
    for e in s.out[("BELONGS_TO", user)]:
        if s.kind(e["to"]) == "Organization":
            ps.add(e["to"])
    return ps


def gate(s: Store, user: str, org: str, ps: set[str]) -> set[str]:
    """R4: apps of the org reached by PERMISSION or USER_APP_RELATION from a principal,
    plus apps the user authenticated as a source account in. ORG_APP_RELATION never."""
    apps: set[str] = set()
    for p in ps:
        for t in ("PERMISSION", "USER_APP_RELATION"):
            for e in s.out[(t, p)]:
                if s.kind(e["to"]) == "App" and s.p(e["to"]).get("orgId") == org:
                    apps.add(e["to"])
    for e in s.out[("AUTHENTICATED_AS", user)]:
        a = e["props"].get("connectorId")
        if isinstance(a, str) and s.kind(a) == "App" and s.p(a).get("orgId") == org:
            apps.add(a)
    return apps


def _grant_edge(s: Store, grantee: str, e: EdgeDict) -> bool:
    """R3: an org's edge grants only when typed ORG/ORGANIZATION."""
    return s.kind(grantee) != "Organization" or e["props"].get("type") in ORG_SHARE_TYPES


def _granted_targets(s: Store, holder: str, *, typed: bool) -> list[str]:
    return [e["to"] for e in s.out[("PERMISSION", holder)]
            if s.kind(e["to"]) in ("Record", "RecordGroup") and not _deleted(s, e["to"])
            and (not typed or (_grant_edge(s, holder, e) and s.p(e["to"]).get("connectorId") is not None))]


def grants(s: Store, user: str, ps: set[str], apps: set[str]) -> set[str]:
    """R5: records/groups granted to a principal (or, inside its connector only, to a
    linked source account and its one-level groups), live, in a gated connector."""
    found = [n for p in ps for n in _granted_targets(s, p, typed=True)]
    for link in s.out[("AUTHENTICATED_AS", user)]:
        conn, src = link["props"].get("connectorId"), link["to"]
        holders = {src} | {e["to"] for e in s.out[("PERMISSION", src)]
                           if e["props"].get("type") == "USER" and s.kind(e["to"]) in ("Group", "Role", "Teams")}
        found += [n for h in holders for n in _granted_targets(s, h, typed=False)
                  if s.p(n).get("connectorId") == conn]
    return {n for n in found if s.p(n).get("connectorId") in apps}


def viewer(s: Store, user: str, org: str) -> Viewer:
    ps = principals(s, user)
    apps = gate(s, user, org, ps)
    return Viewer(user, org, ps, apps, grants(s, user, ps, apps))


def _inherits(s: Store, c: str, p: str) -> bool:
    return any(e["to"] == p for e in s.out[("INHERIT_PERMISSIONS", c)])


def _parents(s: Store, c: str) -> list[str]:
    """Hierarchy parents: NODE_RELATION parent->child of type PARENT_CHILD/ATTACHMENT."""
    return [e["from"] for e in s.inc[("NODE_RELATION", c)]
            if e["props"].get("relationshipType") in HIERARCHY]


def hop_failure(s: Store, v: Viewer, c: str, p: str, opens: bool) -> str | None:
    """R6, the hop rule for child c under parent p. None means the hop passes."""
    if _deleted(s, c):
        return f"R6:deleted@{c}"
    if opens:
        return None
    r, g, i = _rule(s, c), c in v.granted, _inherits(s, c, p)
    if r in ("OPEN", "STRICT"):
        return None if i or g else f"R6:{r}-no-inherit-no-grant@{c}"
    if r == "RESTRICTED":
        if i and g:
            return None
        return f"R6:RESTRICTED-{'no-grant' if i else 'no-inherit'}@{c}"
    return f"R6:unknown-rule@{c}"


def _is_rgl_of(s: Store, n: str, app: str) -> bool:
    return s.kind(n) == "RecordGroup" and s.p(n).get("permissionModel") == RGL \
        and s.p(n).get("connectorId") == app


def from_app(s: Store, v: Viewer, t: str, app: str, opens: bool, trace: list[str] | None = None) -> bool:
    """R7: some hierarchy path t -> ... -> app of <= 50 hops where every hop passes and
    no hop passes through an RGL group of this connector. `trace` collects why paths died."""
    frontier = {t}
    for _ in range(MAX_HOPS):
        nxt: set[str] = set()
        for c in frontier:
            for p in _parents(s, c):
                if _is_rgl_of(s, p, app):
                    if trace is not None:
                        trace.append(f"R7:rgl-parent-skipped@{p}")
                    continue
                failure = hop_failure(s, v, c, p, opens)
                if failure:
                    if trace is not None:
                        trace.append(failure)
                    continue
                if p == app:
                    return True
                nxt.add(p)
        if not nxt:
            return False
        frontier = nxt
    if trace is not None:
        trace.append("R7:depth-cap")  # every hop up to the cap passed, the app is further than 50 hops away
    return False


def declared(s: Store, v: Viewer, t: str, app: str, opens: bool) -> bool:
    """R8: t is (or BELONGS_TO) a live group at or below a live RGL group of this
    connector that is granted or itself reachable from the app."""
    if s.kind(t) == "RecordGroup":
        starts = [t]
    else:
        starts = [e["to"] for e in s.out[("BELONGS_TO", t)] if s.kind(e["to"]) == "RecordGroup"]
    for mg in starts:
        if _deleted(s, mg):
            continue
        seen, frontier = {mg}, {mg}
        for depth in range(MAX_HOPS + 1):
            for g in frontier:
                if _is_rgl_of(s, g, app) and not _deleted(s, g) \
                        and (g in v.granted or from_app(s, v, g, app, opens)):
                    return True
            if depth == MAX_HOPS:
                break
            nxt: set[str] = set()
            for c in frontier:
                if _deleted(s, c):
                    continue
                for p in _parents(s, c):
                    if s.kind(p) == "RecordGroup" and p not in seen:
                        seen.add(p)
                        nxt.add(p)
            if not nxt:
                break
            frontier = nxt
    return False


def _seed(s: Store, v: Viewer, n: str, app: str) -> bool:
    """R9: an OPEN live record/group of this connector granted directly."""
    return s.kind(n) in ("Record", "RecordGroup") and s.p(n).get("connectorId") == app \
        and not _deleted(s, n) and _rule(s, n) == "OPEN" and n in v.granted


def below_seed(s: Store, v: Viewer, t: str, app: str, opens: bool) -> bool:
    """R10: t hangs, over OPEN live nodes that each inherit or are granted, under a
    seed that no other arm admits."""
    frontier = {t}
    for _ in range(MAX_HOPS):
        nxt: set[str] = set()
        for c in frontier:
            if _deleted(s, c) or _rule(s, c) != "OPEN":
                continue
            for p in _parents(s, c):
                if not (_inherits(s, c, p) or c in v.granted):
                    continue
                if _seed(s, v, p, app) and not from_app(s, v, p, app, opens) \
                        and not declared(s, v, p, app, opens):
                    return True
                nxt.add(p)
        if not nxt:
            return False
        frontier = nxt
    return False


def _denial(trace: list[str]) -> str:
    """The decisive fact among the dead paths: the depth cap, else the first failed hop, else a skipped RGL parent."""
    for prefix in ("R7:depth-cap", "R6:", "R7:rgl-parent-skipped"):
        found = next((t for t in trace if t.startswith(prefix)), None)
        if found:
            return found
    return "no-arm"


def verdict(s: Store, v: Viewer, t: str) -> Verdict:
    """Whether the viewer may read node t, and the rule that decided it."""
    if t not in s.by_id or s.kind(t) not in ("Record", "RecordGroup", "App"):
        return Verdict(False, "R11:not-a-readable-node")
    tp = s.p(t)
    if tp.get("orgId") != v.org:
        return Verdict(False, "R11:other-org")
    if tp.get("isDeleted"):
        return Verdict(False, "R11:deleted")
    if tp.get("isPlaceholder"):
        return Verdict(False, "R11:placeholder")
    app = t if s.kind(t) == "App" else tp.get("connectorId")
    trace: list[str] = []
    if isinstance(app, str) and app in v.apps and s.kind(app) == "App":
        ap = s.p(app)
        opens = ap.get("permissionModel") == "APP_LEVEL" or ap.get("type") == "KB"  # R12
        if t == app:
            return Verdict(True, "R4:app")
        if opens and s.kind(t) == "Record" and any(e["to"] == app for e in s.out[("BELONGS_TO", t)]):
            return Verdict(True, "R12:open-app-member")
        if declared(s, v, t, app, opens):
            return Verdict(True, "R8:declared")
        if from_app(s, v, t, app, opens, trace):
            return Verdict(True, "R12:walk-in-open-app" if opens else "R7:walk")
        if _seed(s, v, t, app):
            return Verdict(True, "R9:seed")
        if below_seed(s, v, t, app, opens):
            return Verdict(True, "R10:below-seed")
    if s.kind(t) == "Record" and tp.get("origin") not in (None, "CONNECTOR") \
            and s.kind(app if isinstance(app, str) else None) != "App":
        for e in s.inc[("PERMISSION", t)]:
            if e["from"] in v.ps and _grant_edge(s, e["from"], e):
                return Verdict(True, "R13:unattached-grant")
        return Verdict(False, "R13:unattached-no-grant")
    if not isinstance(app, str) or app not in v.apps:
        return Verdict(False, "R4:not-gated")
    return Verdict(False, _denial(trace))


def admitted(graph: Graph, user: str, org: str, node_ids: list[str]) -> dict[str, Verdict]:
    """Verdict per node id for one viewer (empty user admits nothing)."""
    s = Store(graph)
    if not user:
        return {n: Verdict(False, "R1:no-viewer") for n in node_ids}
    v = viewer(s, user, org)
    return {n: verdict(s, v, n) for n in node_ids}


def cited(graph: Graph, user: str, org: str, vrids: list[str], *, indexed_only: bool = False) -> dict[str, str | None]:
    """Per virtual record id, the smallest admitted record id (after indexed_only)."""
    s = Store(graph)
    out: dict[str, str | None] = dict.fromkeys(vrids)
    if not user:
        return out
    v = viewer(s, user, org)
    for n in s.nodes:
        vr = n["props"].get("virtualRecordId")
        if n["kind"] != "Record" or vr not in out:
            continue
        if indexed_only and not (n["props"].get("indexingStatus") in SEARCHABLE or n["props"].get("hasIndexedCopy")):
            continue
        if verdict(s, v, n["id"]).admitted and (out[vr] is None or n["id"] < out[vr]):
            out[vr] = n["id"]
    return out
