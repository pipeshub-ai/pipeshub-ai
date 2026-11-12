"""`ScopeProjector`: the scope fields of a record (or of a virtual record that stands for several), from the graph.

Pure over `StampGraph` (the nodes it is given), no I/O of its own and no viewer. The decisions are the ones
`rules.assert_may_stamp` allows, and nothing else:

1. A record whose path (itself and every hierarchy ancestor) carries no scope of its own, in an app that declares no
   exception rule or whose app opens everything, equals its record group: **container**.
2. Otherwise the registry's rule for the connector (`rules.rule_for`) gives the stamp, with the facts computed here.
3. `assert_may_stamp` refuses the stamp -> the record gets **no exact stamp** (`legacy`) and is counted as refused for
   its connector (`rules.connector_is_exact`). It is never stamped container.

A *scope* is a node with an access list of its own: a direct grant, a rule other than OPEN/STRICT, a hierarchy parent
it does not inherit from, or deleted. Same definition as `HierarchyView.scope_nodes`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .rules import (
    AncestorFact,
    Rule,
    RuleNotAllowed,
    assert_may_stamp,
    below_seed,
    rule_for,
)
from .scope_fields import ScopeFields, Stamp, combine, plain_id

if TYPE_CHECKING:
    from .ports import StampApp, StampGraph, StampNode

MAX_DEPTH = 50  # same cap as the graph check's walk


@dataclass(frozen=True)
class Projection:
    """The outcome for one record."""

    record_id: str
    fields: ScopeFields
    #: `assert_may_stamp` refused it: it counts against its connector's exactness.
    refused: bool = False
    #: Why (the registry's message), for the operator.
    reason: str = ""
    #: Not a record the index holds a point for (deleted, placeholder, unknown): nothing to stamp, nothing refused.
    skipped: bool = False


def _bad_rule(n: StampNode) -> bool:
    return n.rule not in (None, "OPEN", "STRICT")


def _breaks(n: StampNode) -> bool:
    return any(p not in n.inherits for p in n.parents)


def _restricted(n: StampNode) -> bool:
    return n.kind != "app" and (n.deleted or _bad_rule(n) or _breaks(n))


def _hides_below(n: StampNode) -> bool:
    """A container (the top of a record's group chain) hides what is below it when it is deleted or its rule is unknown (the
    walk through it fails closed), or it does not inherit from its parent; a RESTRICTED rule only asks for the grant the
    container's own readers hold, which is what "the record equals its group" already says."""
    return n.kind != "app" and (n.deleted or n.rule not in (None, "OPEN", "STRICT", "RESTRICTED") or _breaks(n))


def _is_scope(n: StampNode) -> bool:
    """An app is gated by its grants: the outermost container, never a scope of its own."""
    return n.kind != "app" and (n.granted or _restricted(n))


class ScopeProjector:
    def __init__(self, graph: StampGraph, *, max_depth: int = MAX_DEPTH) -> None:
        self._graph = graph
        self._max_depth = max_depth
        self._apps: dict[tuple[str, str], StampApp] = {}  # one read of an app's facts per projector (a batch)

    async def _closure(self, org_id: str, seeds: Iterable[str]) -> dict[str, StampNode]:
        """The seeds, every hierarchy ancestor, and the record-group chains, level by level (one read per level)."""
        known: dict[str, StampNode] = {}
        missing: set[str] = set()
        frontier = set(seeds)
        for _ in range(self._max_depth + 1):
            want = frozenset(frontier - known.keys() - missing)
            if not want:
                break
            found = await self._graph.nodes(org_id, want)
            known.update(found)
            missing |= want - found.keys()
            frontier = {i for n in found.values() for i in (*n.parents, *n.groups)}
        return known

    async def project(self, org_id: str, app_id: str, record_ids: Iterable[str]) -> dict[str, Projection]:
        ids = list(dict.fromkeys(record_ids))
        if (org_id, app_id) not in self._apps:
            self._apps[(org_id, app_id)] = await self._graph.app(org_id, app_id)
        app = self._apps[(org_id, app_id)]
        nodes = await self._closure(org_id, ids)
        return {rid: self._one(app, app_id, nodes, rid) for rid in ids}

    async def project_virtual_records(self, org_id: str, vrids: Mapping[str, Mapping[str, str]]) -> dict[str, tuple[ScopeFields, list[Projection]]]:
        """The fields of each point that stands for records (`vrid -> {record id: app id}`) and each record's outcome. The
        graph is read once per app for all of them (the ancestors are shared)."""
        by_app: dict[str, list[str]] = {}
        for records in vrids.values():
            for rid, app_id in records.items():
                by_app.setdefault(app_id, []).append(rid)
        outcome: dict[tuple[str, str], Projection] = {}
        for app_id, rids in by_app.items():
            for rid, p in (await self.project(org_id, app_id, rids)).items():
                outcome[(app_id, rid)] = p
        out: dict[str, tuple[ScopeFields, list[Projection]]] = {}
        for vrid, records in vrids.items():
            ps = [outcome[(app_id, rid)] for rid, app_id in records.items()]
            live = [p for p in ps if not p.skipped]
            out[vrid] = (combine(p.fields for p in live) if live else ScopeFields.stale(), ps)
        return out

    async def project_virtual_record(self, org_id: str, records: Mapping[str, str]) -> tuple[ScopeFields, list[Projection]]:
        """One point (`record id -> app id`): see `project_virtual_records`."""
        return (await self.project_virtual_records(org_id, {"": records}))[""]

    # ---- one record --------------------------------------------------------------------------------------------

    def _one(self, app: StampApp, app_id: str, nodes: Mapping[str, StampNode], rid: str) -> Projection:
        node = nodes.get(rid)
        if node is None or node.kind != "record" or node.deleted or node.placeholder:
            return Projection(rid, ScopeFields.legacy(), skipped=True)
        path = self._ancestors(nodes, rid)
        if app_id not in path and not (app.opens and not node.groups) and not any(nodes[i].group_level for i in path):
            # the graph check walks up to the app; a record that never reaches it is read only by a direct grant or a
            # declared group, which no path field can express
            return Projection(rid, ScopeFields.legacy(), refused=True, reason=f"{app.connector_type!r}: the path does not reach the app")
        rule = Rule.CONTAINER if app.opens else rule_for(app.connector_type)
        equals = self._equals_group(nodes, rid, path, app, app_id)
        try:
            if equals and not app.opens and not nodes[rid].groups:
                # no record group to match: the connector arm returns it for any viewer past the gate, yet the container rule would hide it
                # from one the app does not open to, so no stamp decides it (not a refusal: the connector stays exact)
                return Projection(rid, ScopeFields.legacy())
            if equals:
                assert_may_stamp(Rule.CONTAINER, app.connector_type, equals_record_group=True)
                return Projection(rid, ScopeFields.container())
            fields = self._exception(app, app_id, nodes, node, rule, path)
            if not all(plain_id(i) for i in (*fields.path_ids, *fields.acl_scope)):
                raise RuleNotAllowed(f"{app.connector_type!r}: an id on the path has a comma or whitespace, which a store may not hold exactly")
            return Projection(rid, fields)
        except RuleNotAllowed as exc:
            return Projection(rid, ScopeFields.legacy(), refused=True, reason=str(exc))

    def _ancestors(self, nodes: Mapping[str, StampNode], rid: str) -> dict[str, int]:
        """The record and every hierarchy ancestor, with its distance (the nearest, for a node reached twice)."""
        dist = {rid: 0}
        frontier = [rid]
        for d in range(1, self._max_depth + 1):
            nxt = [p for i in frontier for p in nodes[i].parents if p in nodes and p not in dist]
            for p in nxt:
                dist.setdefault(p, d)
            frontier = list(dict.fromkeys(nxt))
            if not frontier:
                break
        return dist

    def _equals_group(self, nodes: Mapping[str, StampNode], rid: str, path: Mapping[str, int], app: StampApp, app_id: str) -> bool:
        """No node whose access could differ from the record group's.

        The record groups the record belongs to (and the top of their chains) are the container: their own grants define
        who reads the container, so only a rule or deletion that hides what is below them counts. Every other node on the
        path (records, folders that are groups) and every group below the top of a chain must have no scope of its own.

        Under a record-group-level group the graph check's declared arm decides (the walk skips that group), and the
        record is read exactly when that group is: see `_declared_equals`."""
        if app.opens:
            # every gated user reads a member of an app that opens everything (R12: a BELONGS_TO edge to the app), whatever is
            # above it; any other record is read through its hierarchy to the app (nothing deleted on the way, no
            # record-group-level group skipped), and then it is read by the same people
            return nodes[rid].belongs_app or (_reaches(nodes, rid, app_id, self._max_depth) and not any(nodes[i].deleted for i in path))
        chain: set[str] = set()
        stack = [g for g in nodes[rid].groups if g in nodes]
        while stack:
            g = stack.pop()
            if g in chain:
                continue
            chain.add(g)
            stack += [u for u in nodes[g].groups if u in nodes]
        tops = {g for g in chain if not any(u in nodes for u in nodes[g].groups)}
        if nodes[rid].groups and not (set(nodes[rid].groups) | tops) <= path.keys():
            # its access comes down its own hierarchy; a group it is filed under (or the top of one's chain) that the hierarchy
            # never passes through says nothing about who reads it, yet the container filter admits through every one of them
            return False
        if self._declared(nodes, rid):
            return self._declared_equals(nodes, rid, app_id)
        if any(nodes[i].group_level for i in path):  # the walk skips that group, and nothing declares it for this record
            return False
        for i in path.keys() | chain:
            n = nodes[i]
            tops = i in chain and not any(u in nodes for u in n.groups)
            if (_hides_below(n) if tops else _is_scope(n)):
                return False
        return True

    def _declared(self, nodes: Mapping[str, StampNode], rid: str) -> bool:
        """The graph check's declared arm applies: a group the record belongs to is, or sits below through group parents,
        a live record-group-level group."""
        seen = set(nodes[rid].groups)
        frontier = [g for g in seen if g in nodes]
        for _ in range(self._max_depth):
            if any(nodes[g].group_level and not nodes[g].deleted for g in frontier):
                return True
            nxt = [p for g in frontier if not nodes[g].deleted for p in nodes[g].parents if p in nodes and nodes[p].kind == "group" and p not in seen]
            seen.update(nxt)
            frontier = list(dict.fromkeys(nxt))
            if not frontier:
                break
        return False

    def _declared_equals(self, nodes: Mapping[str, StampNode], rid: str, app_id: str) -> bool:
        """Every hierarchy route from the record ends at a record-group-level group, and nothing on the way is granted (a
        directly granted node is a seed, a second way in). Then the declared arm is the only arm, and it is the group's."""
        seen, frontier = {rid}, [rid]
        for _ in range(self._max_depth):
            nxt: list[str] = []
            for i in frontier:
                n = nodes[i]
                if n.group_level:
                    # the group is also readable below a seed (R10), which the declared arm does not give
                    if below_seed(_ancestor_facts(nodes, i, app_id)):
                        return False
                    continue
                if n.kind == "app" or n.granted:
                    return False
                nxt += [p for p in n.parents if p in nodes and p not in seen]
            seen.update(nxt)
            frontier = list(dict.fromkeys(nxt))
            if not frontier:
                break
        return True

    def _exception(self, app: StampApp, app_id: str, nodes: Mapping[str, StampNode], node: StampNode, rule: Rule,
                   path: Mapping[str, int]) -> ScopeFields:
        facts = _facts(app, app_id, nodes, path)
        assert_may_stamp(rule, app.connector_type, **facts)
        if rule is Rule.INTERSECTION:
            return ScopeFields(Stamp.INTERSECTION, tuple(sorted(path)), (node.id,))
        if rule is Rule.UNION:
            chain = self._inherit_chain(nodes, node.id)
            # a seed on the chain admits everything below it (R10) even where a node above the seed is restricted: the seeds go in
            # `aclScope` beside the record's own id, so a reader of a seed is admitted whatever the exclusion says
            seeds = [a for a in _seed_ids(app_id, nodes, path) if a in chain]
            return ScopeFields(Stamp.UNION, tuple(sorted(chain)), (node.id, *seeds))
        if rule is Rule.OVERRIDE:
            scope = self._nearest_scope(nodes, path, app_id)
            if scope is None:
                raise RuleNotAllowed(f"{app.connector_type!r}: Override found no single nearest scope")
            return ScopeFields(Stamp.OVERRIDE, tuple(sorted(path)), (scope, node.id))
        raise RuleNotAllowed(f"{app.connector_type!r}: {rule.value} has no exception stamp")

    def _inherit_chain(self, nodes: Mapping[str, StampNode], rid: str) -> set[str]:
        """The node and the ancestors whose access flows down to it: climb only through parents it inherits."""
        seen, frontier = {rid}, [rid]
        for _ in range(self._max_depth):
            frontier = [p for i in frontier for p in nodes[i].parents if p in nodes and p in nodes[i].inherits and p not in seen]
            seen.update(frontier)
            if not frontier:
                break
        return seen

    @staticmethod
    def _nearest_scope(nodes: Mapping[str, StampNode], path: Mapping[str, int], app_id: str) -> str | None:
        """The nearest node (itself first) with an access list of its own. Two at the same distance on different
        branches is ambiguous: None, so the record is refused. Every node of the path counts, a restricted record group
        wherever it sits; only a group directly under the app is the container itself."""
        scopes = [(d, i) for i, d in path.items() if _is_scope(nodes[i]) and not (nodes[i].kind == "group" and app_id in nodes[i].parents)]
        if not scopes:
            return None
        best = min(d for d, _ in scopes)
        near = sorted(i for d, i in scopes if d == best)
        return near[0] if len(near) == 1 else None


def _ancestor_facts(nodes: Mapping[str, StampNode], start: str, app_id: str) -> list[AncestorFact]:
    above = _up(nodes, start)
    restricted = {i for i in above if _restricted(nodes[i])}
    return [AncestorFact(open_rule=nodes[a].rule in (None, "OPEN"), directly_granted=nodes[a].granted,
                         restricted_above=bool((_up(nodes, a) - {a}) & restricted), reaches_app=_walks_to_app(nodes, a, app_id))
            for a in above - {start}]


def _facts(app: StampApp, app_id: str, nodes: Mapping[str, StampNode], path: Mapping[str, int]) -> dict[str, bool]:
    """The viewer-independent facts `assert_may_stamp` takes."""
    restricted = {i for i in path if _restricted(nodes[i])}
    ancestors = []
    for a in path:
        if path[a] == 0:
            continue
        ancestors.append(AncestorFact(open_rule=nodes[a].rule in (None, "OPEN"), directly_granted=nodes[a].granted,
                                      restricted_above=bool((_up(nodes, a) - {a}) & restricted), reaches_app=_walks_to_app(nodes, a, app_id)))
    seeds = _seed_ids(app_id, nodes, path)
    reach = max((path[a] for a in seeds), default=0)
    # every node from the record up to the farthest possible seed is OPEN and alive: a non-OPEN or deleted node there stops the walk short of the seed
    stretch_open = all(nodes[i].rule in (None, "OPEN") and not nodes[i].deleted for i, d in path.items() if d <= reach and nodes[i].kind != "app")
    return {
        "record_group_level_on_path": app.has_group_level or any(nodes[i].group_level for i in path),
        "multi_parent_on_path": any(len(nodes[i].parents) > 1 for i in path),
        "placeholder_on_path": any(nodes[i].placeholder for i, d in path.items() if d > 0),
        "below_seed_on_path": below_seed(ancestors),
        "record_is_open": stretch_open,
    }


def _seed_ids(app_id: str, nodes: Mapping[str, StampNode], path: Mapping[str, int]) -> list[str]:
    """The ancestors of the record that are seeds (R9/R10): OPEN, directly granted, and either under a restricted node or not reaching
    the app. A reader of a seed reads everything below it through OPEN nodes, whatever restricts the nodes above the seed."""
    restricted = {i for i in path if _restricted(nodes[i])}
    out = []
    for a in path:
        if path[a] == 0:
            continue
        n = nodes[a]
        if n.rule in (None, "OPEN") and n.granted and (bool((_up(nodes, a) - {a}) & restricted) or not _walks_to_app(nodes, a, app_id)):
            out.append(a)
    return out


def _reaches(nodes: Mapping[str, StampNode], rid: str, app_id: str, depth: int = MAX_DEPTH) -> bool:
    """A hierarchy route from the node up to the app that passes through no record-group-level group (the walk skips it)."""
    seen, frontier = {rid}, [rid]
    for _ in range(depth):
        nxt = [p for i in frontier for p in nodes[i].parents if p in nodes and p not in seen and not nodes[p].group_level]
        if app_id in nxt:
            return True
        seen.update(nxt)
        frontier = list(dict.fromkeys(nxt))
        if not frontier:
            break
    return False


def _walks_to_app(nodes: Mapping[str, StampNode], a: str, app_id: str) -> bool:
    return a == app_id or _reaches(nodes, a, app_id)


def _up(nodes: Mapping[str, StampNode], start: str, depth: int = MAX_DEPTH) -> set[str]:
    seen, frontier = {start}, [start]
    for _ in range(depth):
        frontier = [p for i in frontier for p in nodes[i].parents if p in nodes and p not in seen]
        seen.update(frontier)
        if not frontier:
            break
    return seen
