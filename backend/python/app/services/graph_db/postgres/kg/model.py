"""What kg projects from graph.*, and how: the pure derivation (no database), shared by the projector, the backfill and the verify.

Input: the documents and edges of one org as they are stored (``graph.nodes``, ``graph.edges``). Output: the rows kg holds for a set of
*focus* nodes: their ``kg.records`` row, the non-primary hierarchy parents and extra groups in ``kg.links``, the grants on and by them in
``kg.scope_members`` and, for a user, its ``kg.principal_closure`` rows. The access stamps (``path``, ``class_id``,
``self_lit`` ...) are the stamper's and stay NULL (= legacy: readers take today's walk) until that slice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# collection -> the small code kg.graph_changes and kg.node_keys store (kg.coll_code)
MAX_ID = 256            # the longest node id the vetted writers take (w_nodes_upsert); a node with a longer one is not part of the typed tables (it is as if it were not there)
COLL_CODE = {"apps": 1, "recordGroups": 2, "records": 3, "users": 4, "groups": 5, "roles": 6, "teams": 7, "organizations": 8}
CODE_COLL = {v: k for k, v in COLL_CODE.items()}
RECORD_COLLS = ("apps", "recordGroups", "records")          # the nodes that have a kg.records row; kind = position + 1
KIND = {"apps": 1, "recordGroups": 2, "records": 3}
GROUP_LIKE = ("groups", "roles", "teams")                    # what a user is a member of (R1)
PRINCIPALS = ("users", "groups", "roles", "teams", "organizations")      # the nodes a grant can come from (main's ``access_sql.PRINCIPALS``)
HIERARCHY = ("PARENT_CHILD", "ATTACHMENT")
EDGE_COLLS = ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation", "authenticatedAs")
LINK_HIERARCHY, LINK_GROUP = 1, 2                            # kg.links.kind
UAR = "@uar"                                                 # gtype of a userAppRelation row in kg.scope_members (a permission edge's gtype is its type)
CODE_EPOCH = 3                                               # kg.projection_state.code_epoch this code writes after a verified backfill

Key = tuple[str, str]                                        # (collection, id)


@dataclass(frozen=True)
class GEdge:
    coll: str
    from_coll: str
    from_id: str
    to_coll: str
    to_id: str
    doc: dict

    @property
    def frm(self) -> Key:
        return (self.from_coll, self.from_id)

    @property
    def to(self) -> Key:
        return (self.to_coll, self.to_id)


def hierarchy_edge(e: GEdge) -> bool:
    return e.coll == "nodeRelations" and e.doc.get("relationshipType") in HIERARCHY


def edge_ends(coll: str, frm: Key, to: Key, doc: dict) -> set[Key]:
    """The end(s) of an edge whose kg rows depend on the edge: the same rule as the SQL function kg.edge_ends (a test holds them equal).
    A hierarchy link changes the child; belongs-to and inherits-permissions, the source; a grant, the node it is on or, for a membership of a group, role or team,
    the member; a user-app link, the app; an authenticated-as link, the user."""
    if coll == "nodeRelations":
        out = {to} if doc.get("relationshipType") in HIERARCHY else set()
    elif coll in ("belongsTo", "inheritPermissions", "authenticatedAs"):
        out = {frm}
    elif coll == "permission":
        out = {to} if to[0] in RECORD_COLLS else {frm} if to[0] in GROUP_LIKE else {frm, to}
    elif coll == "userAppRelation":
        out = {to}
    else:
        out = set()
    return {k for k in out if k[0] in COLL_CODE}


_TRUE_TEXT = frozenset({"true", "t", "yes", "y", "on", "1"})


def sql_true(v: object) -> bool:
    """A document value as the SQL walk's ``(doc->>'x')::boolean IS TRUE`` reads it: true, a true word, or the whole number 1 (``'1.0'`` is not a boolean to the cast)."""
    return v is True or (isinstance(v, str) and v.strip().lower() in _TRUE_TEXT) or (isinstance(v, int) and not isinstance(v, bool) and v == 1)


def flag(v: object) -> bool:
    """A boolean flag of a node document (isDeleted, isPlaceholder) as the PostgreSQL walk reads it, ``coalesce((doc->>'isDeleted')::boolean, false)``: true, a
    true word, or the number 1; ``"false"``, ``0`` and everything else are false. The one definition the records' columns and the stamper share."""
    return sql_true(v)


def _ms(v: object) -> int | None:
    """A number the way the listing's sort keys and the bigint columns read it. Anything else is missing (None): a boolean, text that is not a number, a non-finite number, and one outside
    int64 (which a bigint column refuses, and which would fail the write of the node)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(float(v)) if isinstance(v, str) else int(v) if isinstance(v, (int, float)) else None
    except (ValueError, OverflowError):
        return None
    return n if n is not None and -(2 ** 63) <= n < 2 ** 63 else None


def _s(v: object) -> str | None:
    return None if v is None else str(v)


def app_id_of(key: Key, doc: dict) -> str | None:
    """The connector (app) a node belongs to: an app is its own."""
    if key[0] == "apps":
        return key[1]
    c = doc.get("connectorId")
    return c if isinstance(c, str) and c else None


@dataclass
class Snapshot:
    """The graph as far as the derivation reads it: documents by key, edges by both ends, and the sk of every keyed node."""
    nodes: dict[Key, dict]
    edges_to: dict[Key, list[GEdge]]
    edges_from: dict[Key, list[GEdge]]
    sk: dict[Key, int]
    has_children: set[Key] = field(default_factory=set)                       # record-like nodes (of those loaded) with at least one existing hierarchy child
    org: str = ""                                                             # the organization read; a node whose document names another is not listed

    def leaf(self, k: Key) -> bool:
        """A record with no hierarchy children. Record groups and apps carry record-group-level literals whatever their children are, so never."""
        return k[0] == "records" and k not in self.has_children

    def exists(self, k: Key) -> bool:
        return k in self.nodes and k[0] in COLL_CODE and k in self.sk

    def app_sk(self, k: Key) -> int | None:
        doc = self.nodes.get(k)
        if doc is None:
            return None
        a = app_id_of(k, doc)
        return self.sk.get(("apps", a)) if a and ("apps", a) in self.nodes else None      # the key of an app that is gone stays; a record of it has no app


@dataclass
class Derived:
    records: dict[int, dict] = field(default_factory=dict)                    # sk -> kg.records columns written by w_records_upsert
    gone: set[int] = field(default_factory=set)                              # sks of focus nodes that no longer exist: their record row is deleted
    links: set[tuple[int, int, int]] = field(default_factory=set)            # (parent_sk, kind, child_sk) for focus children
    link_children: set[int] = field(default_factory=set)                     # the children whose links were derived (the slice to diff)
    grants: dict[tuple[int, int, str], tuple[str | None, int | None, bool]] = field(default_factory=dict)   # (principal_sk, node_sk, gtype) -> (role, app_sk, node is a leaf)
    grant_nodes: set[int] = field(default_factory=set)                       # sks whose grants-on were derived
    grant_holders: set[int] = field(default_factory=set)                     # sks whose grants-by were derived
    closure: dict[tuple[int, int, int], str] = field(default_factory=dict)   # (user_sk, principal_sk, app_sk) -> via
    closure_users: set[int] = field(default_factory=set)
    cross_apps: set[int] = field(default_factory=set)                        # sks of connectors that a derived record shares a hierarchy parent or a group with (another connector's node)


def listing_ts(doc: dict, source: str, ingest: str) -> int | None:
    """A date as a listing reads it: the source's when there is one, else the ingest date, else 0. A date that is not a number is missing, which sorts last, as in the walk."""
    v = doc.get(source)
    if v is None:
        v = doc.get(ingest) or 0
    return _ms(v)


def _rank_parent(snap: Snapshot, e: GEdge, app: str | None) -> tuple[int, int, int]:
    """Which hierarchy parent is the record's primary one: a PARENT_CHILD parent before an ATTACHMENT one, a parent in the record's own connector before one in
    another, then the lowest sk. The walk of the graph has no primary parent (it visits all of them); the others are in ``kg.links``, and whoever reads ``parent_sk``
    reads them together."""
    pdoc = snap.nodes.get(e.frm, {})
    return (0 if e.doc.get("relationshipType") == "PARENT_CHILD" else 1, 0 if app_id_of(e.frm, pdoc) == app else 1, snap.sk[e.frm])


def record_row(k: Key, doc: dict, snap: Snapshot) -> dict:
    """The kg.records columns of a node that ``w_records_upsert`` writes (everything but the sk and the stamper's columns).

    The columns ``alien``, ``folder`` and ``hide_children`` keep their default (false) here: the listing pull request that reads them also writes them."""
    coll, nid = k
    app = app_id_of(k, doc)
    # the name a listing shows and sorts by: ``name`` first, then the record's or the group's own
    name = _s(doc.get("name") or doc.get("recordName") or doc.get("groupName"))
    parent_edges = sorted((e for e in snap.edges_to.get(k, []) if hierarchy_edge(e) and snap.exists(e.frm) and e.frm[0] in RECORD_COLLS), key=lambda e: _rank_parent(snap, e, app))
    parents = [snap.sk[e.frm] for e in parent_edges]
    group_edges = [e for e in snap.edges_from.get(k, []) if e.coll == "belongsTo" and e.to_coll == "recordGroups" and snap.exists(e.to)]
    groups = [snap.sk[e.to] for e in sorted(group_edges, key=lambda e: (0 if app_id_of(e.to, snap.nodes.get(e.to, {})) == app else 1, snap.sk[e.to]))]
    granted = any(e.coll == "permission" and e.frm[0] in PRINCIPALS and snap.exists(e.frm) for e in snap.edges_to.get(k, []))
    return {
        "coll": COLL_CODE[coll], "id": nid, "kind": KIND[coll], "app_sk": snap.app_sk(k),
        "group_sk": groups[0] if groups else None, "parent_sk": parents[0] if parents else None,
        "rule": _s(doc.get("accessRule")), "model": _s(doc.get("permissionModel")), "vrid": _s(doc.get("virtualRecordId")),
        "name": name, "name_key": name.lower() if name is not None else None,
        "updated_ms": listing_ts(doc, "sourceLastModifiedTimestamp", "updatedAtTimestamp"),
        "created_ms": listing_ts(doc, "sourceCreatedAtTimestamp", "createdAtTimestamp"),
        "size_bytes": _ms(doc.get("sizeInBytes")), "record_type": _s(doc.get("recordType")), "origin": _s(doc.get("origin")),
        "external_id": _s(doc.get("externalRecordId")), "deleted": flag(doc.get("isDeleted")), "placeholder": flag(doc.get("isPlaceholder")), "granted": granted,
        "_parents": parents[1:], "_groups": groups[1:],
    }


def _cross_apps(k: Key, snap: Snapshot) -> set[int]:
    """The connectors of ``k`` and of every hierarchy parent and group of it that belongs to another connector: a change to one can outdate the rows of the other."""
    own = app_id_of(k, snap.nodes[k])
    if own is None or ("apps", own) not in snap.sk:
        return set()
    out: set[int] = set()
    ends = [e.frm for e in snap.edges_to.get(k, []) if hierarchy_edge(e) and e.frm[0] in RECORD_COLLS]
    ends += [e.to for e in snap.edges_from.get(k, []) if e.coll == "belongsTo" and e.to_coll == "recordGroups"]
    for q in ends:
        other = app_id_of(q, snap.nodes[q]) if snap.exists(q) else None
        if other is not None and other != own and ("apps", other) in snap.sk:
            out |= {snap.sk[("apps", own)], snap.sk[("apps", other)]}
    return out


def derive(focus: set[Key], snap: Snapshot) -> Derived:
    """Everything kg holds for ``focus`` (one of each slice per node, see :class:`Derived`)."""
    if not snap.org:
        raise ValueError("a snapshot carries the organisation it was read for: the derivation gates a connector on it")
    d = Derived()
    for k in focus:
        if k[0] not in COLL_CODE or k not in snap.sk:
            continue
        sk = snap.sk[k]
        present = k in snap.nodes
        if k[0] in RECORD_COLLS:
            if not present:
                d.gone.add(sk)
                d.link_children.add(sk)
                d.grant_nodes.add(sk)
                continue
            row = record_row(k, snap.nodes[k], snap)
            d.records[sk] = {c: v for c, v in row.items() if not c.startswith("_")}
            d.cross_apps |= _cross_apps(k, snap)
            d.link_children.add(sk)
            d.links |= {(p, LINK_HIERARCHY, sk) for p in row["_parents"]} | {(g, LINK_GROUP, sk) for g in row["_groups"]}
            d.grant_nodes.add(sk)
            for e in snap.edges_to.get(k, []):
                if e.coll == "permission" and e.frm[0] in PRINCIPALS and snap.exists(e.frm):
                    d.grants[(snap.sk[e.frm], sk, str(e.doc.get("type") or ""))] = (_s(e.doc.get("role")), snap.app_sk(k), snap.leaf(k))
                elif e.coll == "userAppRelation" and k[0] == "apps" and e.frm[0] in PRINCIPALS and snap.exists(e.frm):
                    d.grants[(snap.sk[e.frm], sk, UAR)] = (None, sk, False)
        if k[0] in ("users", "groups", "roles", "teams", "organizations") and present:
            d.grant_holders.add(sk)
            for e in snap.edges_from.get(k, []):
                if e.to_coll in RECORD_COLLS and snap.exists(e.to):
                    if e.coll == "permission":
                        d.grants[(sk, snap.sk[e.to], str(e.doc.get("type") or ""))] = (_s(e.doc.get("role")), snap.app_sk(e.to), snap.leaf(e.to))
                    elif e.coll == "userAppRelation" and e.to_coll == "apps":
                        d.grants[(sk, snap.sk[e.to], UAR)] = (None, snap.sk[e.to], False)
        if k[0] == "users":
            d.closure_users.add(sk)
            if present:
                d.closure[(sk, sk, 0)] = "self"
                for e in snap.edges_from.get(k, []):
                    if e.coll == "permission" and e.doc.get("type") == "USER" and e.to_coll in GROUP_LIKE and snap.exists(e.to):
                        d.closure[(sk, snap.sk[e.to], 0)] = "membership"
                    elif e.coll == "belongsTo" and e.to_coll == "organizations" and snap.exists(e.to):
                        d.closure[(sk, snap.sk[e.to], 0)] = "org"
                    elif e.coll == "authenticatedAs":
                        # as the walk reads it: an edge to a user, in a connector of this organisation (an app of another organisation is no gate)
                        cid = e.doc.get("connectorId")
                        app = snap.sk.get(("apps", cid)) if isinstance(cid, str) else None
                        if app is None or e.to_coll != "users" or (snap.org and snap.nodes.get(("apps", cid), {}).get("orgId") != snap.org):
                            continue
                        if not snap.exists(e.to):                    # an account that is not a node has no badges: an edge written for it after it was deleted must not give them
                            continue
                        d.closure[(sk, snap.sk[e.to], app)] = "link"
                        for b in snap.edges_from.get(e.to, []):
                            if b.coll == "permission" and b.doc.get("type") == "USER" and b.to_coll in GROUP_LIKE and snap.exists(b.to):
                                d.closure[(sk, snap.sk[b.to], app)] = "link_badge"
    return d
