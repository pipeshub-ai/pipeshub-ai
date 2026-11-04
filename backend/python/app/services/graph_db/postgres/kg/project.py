"""The projection: re-derive what kg holds for a set of changed nodes from the current graph, and write the difference through the vetted writers.

It is the unit of work shared by whatever finds a change (the flush of a writer's own transaction, the drain of changes other roles left, the backfill of a connector). It holds
the per-(org, app) advisory lock while it reads the graph and writes kg, so two projections of one connector never interleave, and everything kg is compared with is read
after the lock is taken.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.dependents import (
    held_keys,
    moved_parents,
    parents_of,
)
from app.services.graph_db.postgres.kg.locks import lock_apps
from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    COLL_CODE,
    RECORD_COLLS,
    Derived,
    Key,
    app_id_of,
    derive,
    edge_ends,
)
from app.services.graph_db.postgres.kg.reader import (
    _doc,
    load_nodes,
    load_sks,
    snapshot,
)
from app.services.graph_db.postgres.kg.writers import KgWriter

if TYPE_CHECKING:
    from app.core.db import Tx


@dataclass
class Stats:
    focus: int = 0
    records: int = 0
    records_deleted: int = 0
    links: int = 0
    grants: int = 0
    closure: int = 0
    apps: set[str] = field(default_factory=set)
    orphaned: set[str] = field(default_factory=set)           # connectors this call read a node of while their own node was missing (their rows were taken off the readers)
    keys: set[Key] = field(default_factory=set)               # the nodes this call took as its focus and looked at the dependants of: a change-log row naming only these is satisfied by it


async def expand(tx: Tx, org: str, focus: set[Key]) -> set[Key]:
    """Add what a change to a node's existence changes in its neighbours (a deleted folder re-parents nothing but its children's rows name it), and the users that link to
    a changed source account (their badge groups are derived through it)."""
    keys = {k for k in focus if k[0] in COLL_CODE}
    if not keys:
        return focus
    nodes = await load_nodes(tx, org, keys)
    sks = await load_sks(tx, org, keys)
    held = await held_keys(tx, org, {k for k in keys if k[0] in RECORD_COLLS})
    # A node whose existence changed for the rows that depend on it. A key is no proof that the dependents saw the node (any projection that meets a node gives it one, and a node
    # that was gone and comes back keeps it), so a record counts as present for them once kg holds its row, and a principal, which the change capture logs only when it is inserted
    # or deleted, is always a change.
    flipped = {k for k in keys if k[0] not in RECORD_COLLS or ((k in nodes) != (k in held) and (k in nodes or k in held or k in sks))}
    out = set(focus)
    gone_apps = sorted(sks[k] for k in flipped if k[0] == "apps" and k not in nodes)
    if gone_apps:          # the records of a connector that is gone lose their app: re-derive every one of them
        out |= {(CODE_COLL[r["coll"]], r["id"]) for r in await tx.fetch("/* pipeshub:kg.expand_app */ SELECT coll, id FROM kg.records WHERE org_id = $1 AND app_sk = ANY($2::bigint[])", org, gone_apps)}
    rows = []
    if flipped:
        rows += await tx.fetch("""/* pipeshub:kg.expand */ SELECT e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id,
              jsonb_strip_nulls(jsonb_build_object('type', e.doc->'type', 'role', e.doc->'role', 'relationshipType', e.doc->'relationshipType', 'connectorId', e.doc->'connectorId')) AS doc
            FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND (e.from_id = ANY($3::text[]) OR e.to_id = ANY($3::text[]))""",
                               org, ["permission", "belongsTo", "nodeRelations", "userAppRelation", "authenticatedAs"], sorted({k[1] for k in flipped}))
    accounts = sorted({k[1] for k in keys if k[0] in ("users", "groups", "roles", "teams")})                     # a source account is a user node: the users that link to it
    if accounts:
        rows += await tx.fetch("""/* pipeshub:kg.expand_accounts */ SELECT e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id,
              jsonb_strip_nulls(jsonb_build_object('type', e.doc->'type', 'role', e.doc->'role', 'relationshipType', e.doc->'relationshipType', 'connectorId', e.doc->'connectorId')) AS doc
            FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.to_id = ANY($2::text[])""", org, accounts)
    for r in rows:
        f, t = (r["from_coll"], r["from_id"]), (r["to_coll"], r["to_id"])
        for end in edge_ends(r["coll"], f, t, _doc(r["doc"])):               # the end whose rows depend on the edge, when the other end appeared or went
            if (t if end == f else f) in flipped:
                out.add(end)
        if r["coll"] == "authenticatedAs" and t in keys:                         # a source account changed: its users
            out.add(f)
    return out


async def _expanded(tx: Tx, org: str, seeds: set[Key], known: set[Key] | None = None) -> set[Key]:
    """``seeds`` and everything their neighbours' rows depend on, to a fixpoint: a node this brings in is itself looked at (:func:`expand`), so every node of the result has had its
    dependents found, which is what lets a log row that names only such nodes be acknowledged by the projection."""
    out = set(seeds)
    frontier = set(seeds)
    while frontier:
        grown = await expand(tx, org, frontier) - out - (known or set())
        out |= grown
        frontier = grown
    return out


async def current(tx: Tx, org: str, d: Derived) -> tuple[set, dict, dict]:
    """What kg holds now for the slices ``d`` derived: links of the children, grants on the nodes and by the holders, closure of the users."""
    links, grants, closure = set(), {}, {}
    if d.link_children:
        for r in await tx.fetch("""/* pipeshub:kg.cur_links */ SELECT parent_sk, kind, child_sk FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])""", org, sorted(d.link_children)):
            links.add((r["parent_sk"], r["kind"], r["child_sk"]))
    if d.grant_nodes:
        for r in await tx.fetch("""/* pipeshub:kg.cur_grants_on */ SELECT principal_sk, node_sk, gtype, role, app_sk, leaf FROM kg.scope_members WHERE org_id = $1 AND node_sk = ANY($2::bigint[])""",
                                org, sorted(d.grant_nodes)):
            grants[(r["principal_sk"], r["node_sk"], r["gtype"])] = (r["role"], r["app_sk"], r["leaf"])
    if d.grant_holders:
        for r in await tx.fetch("""/* pipeshub:kg.cur_grants_by */ SELECT principal_sk, node_sk, gtype, role, app_sk, leaf FROM kg.scope_members WHERE org_id = $1 AND principal_sk = ANY($2::bigint[])""",
                                org, sorted(d.grant_holders)):
            grants[(r["principal_sk"], r["node_sk"], r["gtype"])] = (r["role"], r["app_sk"], r["leaf"])
    if d.closure_users:
        for r in await tx.fetch("""/* pipeshub:kg.cur_closure */ SELECT user_sk, principal_sk, app_sk, via FROM kg.principal_closure WHERE org_id = $1 AND user_sk = ANY($2::bigint[])""",
                                org, sorted(d.closure_users)):
            closure[(r["user_sk"], r["principal_sk"], r["app_sk"])] = r["via"]
    return links, grants, closure


async def project(tx: Tx, org: str, focus: set[Key], *, lock_for: set[str] | None = None, expand_neighbours: bool = True) -> Stats:
    """Make kg agree with the graph for ``focus``. ``lock_for``: the app ids to lock ('' = org-wide); the default derives them from the documents. The lock comes first and
    everything kg is compared with is read after it: what a change reaches in its neighbours (:func:`expand`, ``parents_of``) is found from what kg holds, and kg is only stable
    while the lock is held. A node that is a principal, that is gone or that belongs to no app makes the org-wide key exclusive (its rows span connectors)."""
    st = Stats()
    w = KgWriter(tx)
    focus = {k for k in focus if k[0] in COLL_CODE}
    if not focus:
        return st
    docs = await load_nodes(tx, org, {k for k in focus if k[0] in RECORD_COLLS})
    if lock_for is None:
        lock_for = {app_id_of(k, doc) or "" for k, doc in docs.items()}
    org_wide = "" in lock_for or any(k[0] not in RECORD_COLLS or k not in docs for k in focus)
    await lock_apps(tx, org, lock_for, org_wide=org_wide)
    st.apps = lock_for - {""}
    leaf_only: set[Key] = set()
    if expand_neighbours:
        focus = await _expanded(tx, org, focus)
        while True:
            leaf_only = await parents_of(tx, org, focus) - focus      # a parent that gained or lost its last child: its leaf flag and its grants' flag change
            frontier = set(leaf_only)
            while frontier:                                            # and the parents that one of these, re-derived, adds or drops (its old row names the ones it drops)
                frontier = await moved_parents(tx, org, frontier) - focus - leaf_only
                leaf_only |= frontier
            there = set(await load_nodes(tx, org, leaf_only))
            fresh = there - await held_keys(tx, org, there)
            if not fresh:
                break
            focus |= await _expanded(tx, org, fresh, focus)             # this unit writes the row of a node kg has none for: it is a node that appears, and its dependents are looked at
    every = focus | leaf_only

    async def allocate(existing: set[Key]) -> dict[Key, int]:
        have = await load_sks(tx, org, existing)                         # most nodes are known already: only a new key needs the writer
        missing = existing - have.keys()
        return {**have, **(await w.nodes_upsert(missing) if missing else {})}

    snap = await snapshot(tx, org, every, allocate, known_children=expand_neighbours)
    d = derive(every, snap)
    st.focus = len(every)
    st.keys = set(focus)
    cur_links, cur_grants, cur_closure = await current(tx, org, d)

    if d.records:
        await w.records_upsert([{"sk": sk, **cols} for sk, cols in sorted(d.records.items())])
        st.records = len(d.records)
    if d.gone:
        st.records_deleted = await w.records_delete(sorted(d.gone))
    if d.cross_apps:
        done = {r["app_sk"] for r in await tx.fetch("/* pipeshub:kg.cross_read */ SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND cross_app AND app_sk = ANY($2::bigint[])",
                                                    org, sorted(d.cross_apps))}
        if d.cross_apps - done:
            await w.app_cross_set(sorted(d.cross_apps - done))          # sticky: written once, and what lets a pending change of one connector outdate the other
    gone_apps = sorted(snap.sk[k] for k in every if k[0] == "apps" and snap.sk.get(k) in d.gone)
    # A node read while its connector's node is missing is written without an app (grants too: they carry the app of the node they are on). If the node comes back before anything
    # projects the deletion, nothing re-derives those rows and the connector would stay verified over them.
    st.orphaned = {a for k, doc in snap.nodes.items() if (org_wide or k in every) and k[0] != "apps" and (a := app_id_of(k, doc)) and ("apps", a) not in snap.nodes}
    off = {*gone_apps, *(snap.sk[("apps", a)] for a in st.orphaned if ("apps", a) in snap.sk)}
    if off:
        # What was verified for a connector is the state of a node that no longer exists: it is taken off the readers in the same commit, and must be copied and verified again.
        for r in await tx.fetch("""/* pipeshub:kg.gone_app_state */ SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND app_sk = ANY($2::bigint[])
                AND (mode <> 'legacy' OR backfill_cursor IS NOT NULL)""", org, sorted(off)):
            await w.projection_set(r["app_sk"], epoch=0, mode="legacy", digest=None, cursor=None, verified_at=None)
    drop_l = sorted(cur_links - d.links)
    if drop_l:
        await w.links_delete(drop_l)
    if d.links - cur_links:
        await w.links_set(sorted(d.links - cur_links))
    st.links = len(drop_l) + len(d.links - cur_links)
    drop_g = sorted(k for k in cur_grants if k not in d.grants)
    if drop_g:
        await w.grants_delete(drop_g)
    new_g = [(*k, *v) for k, v in sorted(d.grants.items()) if cur_grants.get(k) != v]
    if new_g:
        await w.grants_set(new_g)
    st.grants = len(drop_g) + len(new_g)
    drop_c = sorted(k for k in cur_closure if k not in d.closure)
    if drop_c:
        await w.closure_delete(drop_c)
    new_c = [(*k, v) for k, v in sorted(d.closure.items()) if cur_closure.get(k) != v]
    if new_c:
        await w.closure_set(new_c)
    st.closure = len(drop_c) + len(new_c)
    return st


_CODE_STR = {str(c): coll for c, coll in CODE_COLL.items()}


def decode(rows: list) -> set[Key]:
    """The node keys of change-log rows: each row holds an array of 'code:id' strings (the id may contain ':', the code is a small integer). An element that does not decode (not a
    string, no known code, no id) is skipped."""
    keys: set[Key] = set()
    for r in rows:
        for e in r["ends"]:
            code, _, nid = e.partition(":") if isinstance(e, str) else ("", "", "")
            if nid and code in _CODE_STR:
                keys.add((_CODE_STR[code], nid))
    return keys
