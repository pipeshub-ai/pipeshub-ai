"""`ExplainService`: why one person can or cannot read one record, for an organisation admin.

The verdict is always `check_access`'s (the one implementation of the access rule). The reason comes from a walk over the same
facts the scope projector reads (`StampGraph`): the walk reaches its own verdict by the rule's arms, and names the first thing
that stopped the viewer. When the walk disagrees with the graph the answer keeps the graph's verdict (a denial then says `UNEXPLAINED`), and the
pair is counted and kept for export: a reason the graph does not support is never shown.

A container is named only when the *asking* admin passes the same check on it; any other is "a restricted area" (name None).
Nothing here is for the person the record is about, and nothing here returns the record's own title.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from app.config.constants.arangodb import CollectionNames

from .adapters import GraphPrincipalView
from .errors import ViewerScopesUnavailable
from .metrics import EXPLAIN_MISMATCH
from .projector import MAX_DEPTH, ScopeProjector

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .ports import StampApp, StampNode

logger = logging.getLogger(__name__)

GRANTED = "granted"
NOT_LINKED = "not_linked"
NO_GRANT = "no_grant"
RESTRICTED_CONTAINER = "restricted_container"
ITEM_RESTRICTED = "item_restricted"
PARENT_NOT_READABLE = "parent_not_readable"
PENDING_REWRITE = "pending_rewrite"
SYNCING = "syncing"
SCHEME_UNREADABLE = "scheme_unreadable"
DELETED_AT_SOURCE = "deleted_at_source"
UNEXPLAINED = "unexplained"

#: Not indexed (`not_indexed`) is a search fact, not an access one: the graph knows nothing about it, so it is not returned here.
CODES = (GRANTED, NOT_LINKED, NO_GRANT, RESTRICTED_CONTAINER, ITEM_RESTRICTED, PARENT_NOT_READABLE, PENDING_REWRITE, SYNCING,
         SCHEME_UNREADABLE, DELETED_AT_SOURCE, UNEXPLAINED)
#: Mismatches kept for export (ids only), oldest dropped first.
MAX_KEPT_MISMATCHES = 1000

_Why = Literal["deleted", "restricted", "unknown_rule", "rgl", "depth"]


class ExplainNotFound(LookupError):
    """No such person or record (or the record is of another connector): the same answer for each."""


@dataclass(frozen=True)
class ContainerRef:
    #: None: the asking admin cannot read it ("a restricted area").
    name: str | None
    id: str | None = None

    @property
    def restricted(self) -> bool:
        return self.name is None


@dataclass(frozen=True)
class Explanation:
    #: `check_access`'s verdict for the person and the record.
    admitted: bool
    code: str
    container: ContainerRef | None
    #: Containers from the app down to the record's parent.
    path: tuple[ContainerRef, ...]
    #: Given to support when the reason looks wrong; it is also in the mismatch log line.
    reference: str

    def as_dict(self) -> dict[str, object]:
        """The one wire shape: verdict, code, containers (a name or "restricted") and the reference. Nothing of the record itself, and no
        id or link: what the asking admin may not read is never in it."""
        def ref(c: ContainerRef | None) -> dict[str, object] | None:
            return None if c is None else {"name": c.name, "restricted": c.restricted}

        return {"admitted": self.admitted, "code": self.code, "container": ref(self.container),
                "path": [ref(c) for c in self.path], "reference": self.reference}


@dataclass(frozen=True)
class Mismatch:
    reference: str
    org_id: str
    viewer_key: str
    record_id: str
    graph_admitted: bool
    walk_admitted: bool


@dataclass
class _Facts:
    nodes: Mapping[str, StampNode]
    granted: frozenset[str]
    gated: frozenset[str]
    app_id: str
    opens: bool


@dataclass
class _Walk:
    admitted: bool
    code: str
    #: The node the reason is about, when it names one.
    node_id: str | None = None


def _rule(n: StampNode) -> str:
    return "OPEN" if n.rule is None else n.rule


def _hop_failure(f: _Facts, c: StampNode, p: str) -> tuple[_Why, str] | None:
    """The hop rule for child `c` under parent `p`; None means it passes."""
    if c.deleted:
        return "deleted", c.id
    if f.opens:
        return None
    rule, grant, inherits = _rule(c), c.id in f.granted, p in c.inherits
    if rule in ("OPEN", "STRICT"):
        return None if inherits or grant else ("restricted", c.id)
    if rule == "RESTRICTED":
        return None if inherits and grant else ("restricted", c.id)
    return "unknown_rule", c.id


def _is_rgl(f: _Facts, n: StampNode | None) -> bool:
    return n is not None and n.kind == "group" and n.group_level and n.connector_id == f.app_id


def _from_app(f: _Facts, t: str, trace: list[tuple[_Why, str]] | None = None) -> bool:
    """Some hierarchy path from `t` up to the app where every hop passes and none runs through a record group of this connector that
    declares its own readers."""
    frontier = {t}
    for _ in range(MAX_DEPTH):
        nxt: set[str] = set()
        # Sorted: `_denied` keeps the first failure met, so a set's hash order would change the answer from one process to the next.
        for cid in sorted(frontier):
            c = f.nodes[cid]
            for p in c.parents:
                if p not in f.nodes:
                    continue
                if _is_rgl(f, f.nodes[p]):
                    if trace is not None:
                        trace.append(("rgl", p))
                    continue
                failure = _hop_failure(f, c, p)
                if failure:
                    if trace is not None:
                        trace.append(failure)
                    continue
                if p == f.app_id:
                    return True
                nxt.add(p)
        if not nxt:
            return False
        frontier = nxt
    if trace is not None:
        trace.append(("depth", t))
    return False


def _declared(f: _Facts, t: StampNode) -> bool:
    """`t` is, or belongs to, a live group at or below a live declared-readers group of this connector that is granted or itself reachable."""
    starts = [t.id] if t.kind == "group" else [g for g in t.groups if g in f.nodes]
    for start in starts:
        if f.nodes[start].deleted:
            continue
        seen, frontier = {start}, {start}
        for depth in range(MAX_DEPTH + 1):
            for gid in frontier:
                g = f.nodes[gid]
                if _is_rgl(f, g) and not g.deleted and (gid in f.granted or _from_app(f, gid)):
                    return True
            if depth == MAX_DEPTH:
                break
            nxt: set[str] = set()
            for cid in frontier:
                if f.nodes[cid].deleted:
                    continue
                for p in f.nodes[cid].parents:
                    if p in f.nodes and f.nodes[p].kind == "group" and p not in seen:
                        seen.add(p)
                        nxt.add(p)
            if not nxt:
                break
            frontier = nxt
    return False


def _seed(f: _Facts, n: StampNode) -> bool:
    """A live OPEN record or group of this connector that is granted directly."""
    return n.kind in ("record", "group") and n.connector_id == f.app_id and not n.deleted and _rule(n) == "OPEN" and n.id in f.granted


def _below_seed(f: _Facts, t: str) -> bool:
    """`t` hangs, over OPEN live nodes that each inherit or are granted, under a seed that no other arm admits."""
    frontier = {t}
    for _ in range(MAX_DEPTH):
        nxt: set[str] = set()
        for cid in frontier:
            c = f.nodes[cid]
            if c.deleted or _rule(c) != "OPEN":
                continue
            for p in c.parents:
                if p not in f.nodes or not (p in c.inherits or cid in f.granted):
                    continue
                if _seed(f, f.nodes[p]) and not _from_app(f, p) and not _declared(f, f.nodes[p]):
                    return True
                nxt.add(p)
        if not nxt:
            return False
        frontier = nxt
    return False


def _denied(f: _Facts, t: StampNode, trace: list[tuple[_Why, str]]) -> _Walk:
    """The decisive fact among the dead paths: the depth cap, else the first failed hop, else a skipped declared-readers parent."""
    if any(w == "depth" for w, _ in trace):
        return _Walk(False, UNEXPLAINED)
    hop = next(((w, i) for w, i in trace if w in ("deleted", "restricted", "unknown_rule")), None)
    if hop:
        why, node_id = hop
        if why == "unknown_rule":
            return _Walk(False, SCHEME_UNREADABLE, node_id)
        if why == "deleted":
            return _Walk(False, PARENT_NOT_READABLE, node_id)
        return _Walk(False, ITEM_RESTRICTED if node_id == t.id else RESTRICTED_CONTAINER, node_id)
    rgl = next((i for w, i in trace if w == "rgl"), None)
    return _Walk(False, RESTRICTED_CONTAINER, rgl) if rgl else _Walk(False, NO_GRANT)


def _walk(f: _Facts, t: StampNode, app_node: StampNode | None) -> _Walk:
    """The reference decision for node `t`: whether the viewer may read it, and what decided it."""
    if t.deleted:
        return _Walk(False, DELETED_AT_SOURCE)
    if t.placeholder:
        return _Walk(False, SYNCING)
    gated = f.app_id in f.gated and app_node is not None and app_node.kind == "app"
    if gated:
        if t.kind == "app":
            return _Walk(True, GRANTED)
        if f.opens and t.kind == "record" and t.belongs_app:
            return _Walk(True, GRANTED)
        trace: list[tuple[_Why, str]] = []
        if _declared(f, t) or _from_app(f, t.id, trace) or _seed(f, t) or _below_seed(f, t.id):
            return _Walk(True, GRANTED)
        return _denied(f, t, trace)
    if t.kind == "record" and t.origin not in (None, "CONNECTOR") and (app_node is None or app_node.kind != "app"):
        # A record of no connector is read by a direct grant of its own; the graph says which, the walk cannot tell.
        return _Walk(False, NO_GRANT)
    return _Walk(False, NOT_LINKED)


class ExplainService:
    def __init__(self, provider: IGraphDBProvider, *, max_kept: int = MAX_KEPT_MISMATCHES) -> None:
        self._provider = provider
        self._principals = GraphPrincipalView(provider)
        self.mismatches: deque[Mismatch] = deque(maxlen=max_kept)

    def export_mismatches(self) -> list[Mismatch]:
        """The mismatches kept so far (ids only), oldest first."""
        return list(self.mismatches)

    async def _facts(self, org_id: str, record_id: str, viewer_key: str) -> tuple[_Facts, StampNode, StampNode | None, object]:
        graph = self._provider.permission_stamp_graph()
        nodes = dict(await ScopeProjector(graph)._closure(org_id, [record_id]))
        target = nodes.get(record_id)
        if target is None:
            raise ExplainNotFound(record_id)
        app_id = target.id if target.kind == "app" else target.connector_id
        if app_id and app_id not in nodes:
            nodes.update(await graph.nodes(org_id, frozenset({app_id})))
        app: StampApp | None = await graph.app(org_id, app_id) if app_id else None
        if target.kind == "record" and target.origin is None and nodes.get(app_id) is None:
            # The stamp graph does not read the origin; only a record of no connector's app needs it.
            doc = await self._provider.get_document(record_id, CollectionNames.RECORDS.value) or {}
            target = replace(target, origin=doc.get("origin"))
            nodes[record_id] = target
        who = await self._principals.principal(org_id, viewer_key)
        facts = _Facts(nodes, who.granted_ids, who.gated_app_ids, app_id, bool(app and app.opens))
        return facts, target, nodes.get(app_id), who

    async def explain(
        self, org_id: str, viewer_key: str, record_id: str, *, asker_key: str, connector_id: str | None = None,
    ) -> Explanation:
        """`viewer_key` and `asker_key` are graph user keys. Raises `ExplainNotFound` for an unknown record, one of another
        connector than `connector_id`, or an unknown person; `ViewerScopesUnavailable` when the graph cannot answer."""
        if not await self._in_org(org_id, viewer_key):
            raise ExplainNotFound(viewer_key)
        try:
            facts, target, app_node, who = await self._facts(org_id, record_id, viewer_key)
        except ViewerScopesUnavailable as exc:
            if str(exc) == "user_not_found":
                raise ExplainNotFound(viewer_key) from exc
            raise
        if connector_id is not None and facts.app_id != connector_id:
            raise ExplainNotFound(record_id)
        graph_admitted = bool(await self._principals.readable(org_id, who, frozenset({record_id})))  # type: ignore[arg-type]
        walk = _walk(facts, target, app_node)
        reference = uuid.uuid4().hex[:8]
        if walk.admitted != graph_admitted:
            self._mismatch(Mismatch(reference, org_id, viewer_key, record_id, graph_admitted, walk.admitted))
            # An admit needs no reason, so it stays `granted`; a denial the walk cannot support has none to show.
            walk = _Walk(True, GRANTED) if graph_admitted else _Walk(False, UNEXPLAINED)
        elif not graph_admitted and walk.code in (NO_GRANT, RESTRICTED_CONTAINER, ITEM_RESTRICTED, PARENT_NOT_READABLE) and \
                await self._pending(org_id, facts, record_id):
            walk = _Walk(False, PENDING_REWRITE)
        container_id = walk.node_id if walk.node_id and walk.node_id != record_id else None
        chain = self._chain(facts, target, container_id)
        if container_id is None and walk.code in (NO_GRANT, NOT_LINKED):
            container_id = chain[-1] if chain else None
        named = await self._names(org_id, asker_key, facts, [*chain, *([container_id] if container_id else [])])
        container = named[container_id] if container_id else None
        return Explanation(graph_admitted, walk.code, container, tuple(named[i] for i in chain), reference)

    async def _in_org(self, org_id: str, viewer_key: str) -> bool:
        """The person is a user of this organisation (the org-scoped user lookup both stores have); one of another is not found."""
        # Without raise_on_error a failed read looks like "no such user", which would answer a store outage with a 404.
        try:
            doc = await self._provider.get_document(viewer_key, CollectionNames.USERS.value, raise_on_error=True) or {}
            email = doc.get("email")
            found = await self._provider.get_users_by_emails([email], org_id=org_id, raise_on_error=True) if email else {}
        except Exception as exc:
            raise ViewerScopesUnavailable("graph_error") from exc
        return any(u.id == viewer_key for u in found.values())

    def _mismatch(self, m: Mismatch) -> None:
        EXPLAIN_MISMATCH.inc("admit" if m.graph_admitted else "deny")
        self.mismatches.append(m)
        logger.warning("explain mismatch ref=%s org=%s viewer=%s record=%s graph=%s walk=%s",
                       m.reference, m.org_id, m.viewer_key, m.record_id, m.graph_admitted, m.walk_admitted)

    async def _pending(self, org_id: str, f: _Facts, record_id: str) -> bool:
        """A subtree of this connector awaits a rewrite and the record is under (or is) one of its roots."""
        try:
            roots = (await self._provider.permission_hierarchy_view().pending_scopes(org_id)).get(f.app_id, frozenset())
        except ViewerScopesUnavailable:
            return False
        return bool(roots) and bool(roots & {record_id, *self._chain_all(f, record_id)})

    @staticmethod
    def _chain_all(f: _Facts, start: str) -> set[str]:
        seen, frontier = set[str](), {start}
        while frontier:
            seen |= frontier
            frontier = {p for i in frontier if i in f.nodes for p in f.nodes[i].parents if p not in seen}
        return seen

    @staticmethod
    def _reaching(f: _Facts, target: StampNode, through: str) -> set[str]:
        """The target's ancestors (and the target) from which `through` can be reached upward, each node visited once: the ancestors
        are collected, then `through` is followed down the reversed edges among them."""
        ancestors, frontier = {target.id}, [target.id]
        children: dict[str, list[str]] = {}
        while frontier:
            nxt: list[str] = []
            for i in frontier:
                for p in f.nodes[i].parents:
                    if p in f.nodes:
                        children.setdefault(p, []).append(i)
                        if p not in ancestors:
                            ancestors.add(p)
                            nxt.append(p)
            frontier = nxt
        reach, frontier = {through} & ancestors, [through] if through in ancestors else []
        while frontier:
            # A set: two frontier nodes that share a child must not queue it twice, or each layer doubles the work.
            nxt = {c for i in frontier for c in children.get(i, ())} - reach
            reach |= nxt
            frontier = list(nxt)
        return reach

    @staticmethod
    def _chain(f: _Facts, target: StampNode, through: str | None = None) -> list[str]:
        """The target's ancestors, from the app down, following the first parent of each (parents sorted by id), except that a
        parent from which `through` can be reached is taken first, so the path shown runs through the container the reason names."""
        reach = ExplainService._reaching(f, target, through) if through is not None else set[str]()
        out: list[str] = []
        node = target
        for _ in range(MAX_DEPTH):
            parents = sorted(p for p in node.parents if p in f.nodes)
            if reach and through not in out:
                parents.sort(key=lambda p: p not in reach)
            if not parents or parents[0] in out:
                break
            out.append(parents[0])
            node = f.nodes[parents[0]]
        return out[::-1]

    async def _names(self, org_id: str, asker_key: str, f: _Facts, ids: list[str]) -> dict[str, ContainerRef]:
        want = frozenset(ids)
        if not want:
            return {}
        asker = await self._principals.principal(org_id, asker_key)
        readable = await self._principals.readable(org_id, asker, want)

        async def name(i: str) -> tuple[str, ContainerRef]:
            n = f.nodes.get(i)
            if i not in readable or n is None:
                return i, ContainerRef(None)
            collection = {"app": CollectionNames.APPS, "group": CollectionNames.RECORD_GROUPS}.get(n.kind, CollectionNames.RECORDS)
            doc = await self._provider.get_document(i, collection.value) or {}
            title = doc.get("groupName") or doc.get("recordName") or doc.get("name")
            return i, ContainerRef(str(title) if title else None, i if title else None)

        return dict(await asyncio.gather(*(name(i) for i in want)))
