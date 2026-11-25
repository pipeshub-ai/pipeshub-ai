"""The projection: re-derive what kg holds for a set of changed nodes from the current graph, and write the difference through the vetted writers.

It is the unit of work shared by whatever finds a change (the flush of a writer's own transaction, the drain of changes other roles left, the backfill of a connector). It holds
the per-(org, app) advisory lock while it reads the graph and writes kg, so two projections of one connector never interleave, and everything kg is compared with is read
after the lock is taken.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import grant_marks
from app.services.graph_db.postgres.kg.dependents import (
    children_of,
    held_apps,
    held_keys,
    moved_parents,
    parents_of,
)
from app.services.graph_db.postgres.kg.locks import (
    MAX_ROUNDS,
    NeedLocks,
    bump,
    lock_apps,
)
from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    CODE_EPOCH,
    COLL_CODE,
    MAX_ID,
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
from app.services.graph_db.postgres.kg.stamp import StampStats, stamp
from app.services.graph_db.postgres.kg.writers import (
    GRANT_TYPE_LENGTH,
    KgWriter,
    record_refusals,
)
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import Tx

logger = logging.getLogger(__name__)
REFUSED = METRICS_BACKEND.counter("pipeshub_kg_refused_rows_total", "Rows with a value a kg writer refuses (a node written without it, a grant left out), and their connector taken off the readers", ["coll"])
TRASH_HELD = "/* pipeshub:kg.trash_held */ SELECT sk, deleted_ms, delete_batch, pc_top FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])"
# What kg's rows say of whether a node is granted before the unit writes them, and whether the row is under a verified copy (the stamper reads the flag from kg there).
GRANTED_HELD = """/* pipeshub:kg.granted_held */ SELECT r.sk, r.granted, r.app_sk, s.app_sk IS NOT NULL AS verified FROM kg.records r
    LEFT JOIN kg.projection_state s ON s.org_id = r.org_id AND s.app_sk = r.app_sk AND s.mode IN ('shadow', 'enforce') AND s.code_epoch = $3
    WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])"""


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
    refused: set[Key] = field(default_factory=set)            # nodes with a value the records writer refuses: written without it, and their connector taken off the readers
    stamps: StampStats = field(default_factory=StampStats)    # what the stamping after the structural rows did
    wide: bool = False                                        # the unit held the organization-wide lock
    keys: set[Key] = field(default_factory=set)               # the nodes this call took as its focus and looked at the dependants of: a change-log row naming only these is satisfied by it
    off: set[int] = field(default_factory=set)                # the connectors (app sks) this call took off the readers: their copy is not exact and must be copied and verified again
    shallow: set[Key] = field(default_factory=set)            # nodes whose grants alone changed and stayed granted (or not): restamped alone, not what is below them


async def expand(tx: Tx, org: str, focus: set[Key], reached: set[Key] | None = None) -> set[Key]:
    """Add what a change to a node's existence changes in its neighbours (a deleted folder re-parents nothing but its children's rows name it), and the users that link to
    a changed source account (their badge groups are derived through it). ``reached``: given, it collects every node this adds, those already in ``focus`` too."""
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
    out: set[Key] = set()
    gone_apps = sorted(sks[k] for k in flipped if k[0] == "apps" and k not in nodes)
    if gone_apps:          # the records of a connector that is gone lose their app: re-derive every one of them
        out |= {(CODE_COLL[r["coll"]], r["id"]) for r in await tx.fetch("/* pipeshub:kg.expand_app */ SELECT coll, id FROM kg.records WHERE org_id = $1 AND app_sk = ANY($2::bigint[])", org, gone_apps)}
    app_ids = sorted(k[1] for k in keys if k[0] == "apps")
    if app_ids:
        # the users whose source accounts are linked in the connector: the link is a gate on the connector's organisation and existence, and it is named by the edge's
        # document, not by an edge to the app
        out |= {("users", r["from_id"]) for r in await tx.fetch("""/* pipeshub:kg.expand_linked_users */ SELECT e.from_id FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.doc->>'connectorId' = ANY($2::text[])""", org, app_ids)}
    appeared = sorted(k[1] for k in keys if k[0] == "apps" and k in nodes and k not in held)
    if appeared:      # the records that name a connector that is (back) there lose their NULL app: no edge links them, their document does
        out |= {(r["coll"], r["id"]) for r in await tx.fetch("""/* pipeshub:kg.expand_appeared */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1
            AND n.coll = ANY($2::text[]) AND n.doc->>'connectorId' = ANY($3::text[])""", org, ["records", "recordGroups"], appeared)}
    here = {k for k in keys if k[0] in RECORD_COLLS and k[0] != "apps" and k in nodes}
    if here:          # a record or group that moved to another connector: its children's primary parent and group, and the connector flag, depend on it
        before = await held_apps(tx, org, here)
        moved = {k for k, old in before.items() if old != app_id_of(k, nodes[k])}
        if moved:
            out |= await children_of(tx, org, moved)
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
    if reached is not None:
        reached |= out
    return focus | out


async def _expanded(tx: Tx, org: str, seeds: set[Key], known: set[Key] | None = None, reached: set[Key] | None = None) -> set[Key]:
    """``seeds`` and everything their neighbours' rows depend on, to a fixpoint: a node this brings in is itself looked at (:func:`expand`), so every node of the result has had its
    dependents found, which is what lets a log row that names only such nodes be acknowledged by the projection."""
    out = set(seeds)
    frontier = set(seeds)
    while frontier:
        grown = await expand(tx, org, frontier, reached) - out - (known or set())
        out |= grown
        frontier = grown
    return out


async def _held(tx: Tx, org: str, every: set[Key], locked: set[str], *, org_wide: bool) -> None:
    """The locks held cover every row the unit is about to write, or the unit starts again with more (:class:`NeedLocks`, raised before anything is written)."""
    if org_wide:
        return
    recs = {k for k in every if k[0] in RECORD_COLLS}
    docs = await load_nodes(tx, org, recs)
    needed = {app_id_of(k, doc) or "" for k, doc in docs.items()} | {a for a in (await held_apps(tx, org, recs)).values() if a}
    wide = any(k[0] not in RECORD_COLLS or k not in docs for k in every) or "" in needed
    missing = needed - {""} - locked
    if missing or wide:
        raise NeedLocks(missing, wide=wide)


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


async def project(tx: Tx, org: str, focus: set[Key], *, lock_for: set[str] | None = None, expand_neighbours: bool = True, stamps: bool = True,
                  grant_only: frozenset[Key] = frozenset()) -> Stats:
    """Make kg agree with the graph for ``focus``. ``grant_only``: nodes of the focus that the change log names only for a change of a grant on them (rows marked
    ``grant_only``, kg_0021). ``lock_for``: connector ids to lock besides those of the focus ('' = org-wide). The lock comes first and everything kg is
    compared with is read after it. What a change reaches in its neighbours (:func:`expand`, ``parents_of``) is only known once it is read under the lock: when it reaches a
    connector the unit holds no lock for (a hierarchy parent or child of another connector, or a node that spans the organisation), the unit rolls back to its savepoint, which
    releases its locks, and starts again with the larger set taken in one ascending pass, so no unit waits for a lock below one it holds. After ``MAX_ROUNDS`` it raises ``RuntimeError`` with nothing written: a caller must treat that as "retry later" (leave the work
    queued), never as success, and never swallow it."""
    extra: set[str] = set()
    wide = False
    for _ in range(MAX_ROUNDS):
        try:
            async with tx.savepoint("kg_project"):
                st = await _project(tx, org, focus, lock_for=lock_for, expand_neighbours=expand_neighbours, stamps=stamps, extra=extra, wide=wide, grant_only=grant_only)
            bump(org, st.apps, wide=st.wide)
            return st
        except NeedLocks as e:
            extra |= e.apps
            wide = wide or e.wide
    raise RuntimeError("the projection kept reaching connectors it had not locked")


async def _project(tx: Tx, org: str, focus: set[Key], *, lock_for: set[str] | None, expand_neighbours: bool, stamps: bool, extra: set[str], wide: bool,
                   grant_only: frozenset[Key]) -> Stats:
    st = Stats()
    w = KgWriter(tx)
    focus = {k for k in focus if k[0] in COLL_CODE and len(k[1]) <= MAX_ID}
    if not focus:
        return st
    docs = await load_nodes(tx, org, {k for k in focus if k[0] in RECORD_COLLS})
    # the connectors of the focus now, and those kg holds its records under (a record that moved is rewritten in both), whatever ``lock_for`` says
    old_apps = {a for a in (await held_apps(tx, org, set(docs))).values() if a}
    locked = {*(lock_for or set()), *(app_id_of(k, doc) or "" for k, doc in docs.items()), *old_apps, *extra}
    org_wide = wide or "" in locked or any(k[0] not in RECORD_COLLS or k not in docs for k in focus) or not locked
    await lock_apps(tx, org, locked, org_wide=org_wide)
    st.apps = locked - {""}
    st.wide = org_wide
    leaf_only: set[Key] = set()
    logged_apps = {k for k in focus if k[0] == "apps"}
    reached: set[Key] = set()                                          # what the expansion added, for a reason other than being logged
    if expand_neighbours:
        focus = await _expanded(tx, org, focus, reached=reached)
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
            focus |= await _expanded(tx, org, fresh, focus, reached)    # this unit writes the row of a node kg has none for: it is a node that appears, and its dependents are looked at
    focus = {k for k in focus if len(k[1]) <= MAX_ID}              # what the expansion brought in: a node whose id the writers cannot take is not part of kg
    leaf_only = {k for k in leaf_only if len(k[1]) <= MAX_ID}
    every = focus | leaf_only
    await _held(tx, org, every, locked, org_wide=org_wide)

    async def allocate(existing: set[Key]) -> dict[Key, int]:
        have = await load_sks(tx, org, existing)                         # most nodes are known already: only a new key needs the writer
        missing = existing - have.keys()
        return {**have, **(await w.nodes_upsert(missing) if missing else {})}

    snap = await snapshot(tx, org, every, allocate, known_children=expand_neighbours)
    d = derive(every, snap)
    # A value the writer refuses would fail the whole unit, and with it every other row of the batch, every time it is retried. The row is written without it (the column empty) and
    # everything else of the node is projected as usual, so no row is left as it was (under a connector the node has left, or with a parent, a group or stamps it no longer has);
    # the connector the row is now under is taken off the readers below, since its copy of this node is not exact, and a verify finds the difference while the value stays.
    for k in sorted(every):
        row = d.records.get(snap.sk.get(k))
        if row is not None and (bad := record_refusals(row)):
            st.refused.add(k)
            REFUSED.inc(k[0])
            logger.error("kg: the records writer refuses the %s column of %s %s; the row is written without it and its connector is taken off the readers", ", ".join(bad), *k)
            row.update(dict.fromkeys(bad))
    # A grant's type is part of its key, so a grant whose type the writer refuses cannot be stored at all. It is left out (no row of its key can exist to go stale) and the connector of
    # the node it is on is taken off the readers below: the walk reads the graph and sees it, and a verify finds it missing, so the copy is not served again while the type stays.
    refused_g_apps: set[int] = set()
    for g in sorted(g for g in d.grants if not GRANT_TYPE_LENGTH[0] <= len(g[2]) <= GRANT_TYPE_LENGTH[1]):
        REFUSED.inc("permission")
        logger.error("kg: the grants writer refuses the type (%d characters) of the grant of principal %s on node %s; it is left out and its connector is taken off the readers",
                     len(g[2]), g[0], g[1])
        if (app := d.grants.pop(g)[1]) is not None:
            refused_g_apps.add(app)
    if d.gone and not org_wide:      # gone since `_held`: its key can already be another connector's, and only the whole org holds that connector off
        raise NeedLocks(set(), wide=True)
    st.focus = len(every)
    st.keys = {k for k in focus if k[0] != "apps" or k in logged_apps}       # an app only the expansion brought in is not restamped (below): a change of its own waits for its own projection
    cur_links, cur_grants, cur_closure = await current(tx, org, d)
    flips: set[Key] = set()
    if stamps:
        flips, st.shallow = await _grant_flags(tx, org, leaf_only, {k for k in grant_only & focus if k[0] != "apps"} - reached, snap.sk, d)

    # the nodes whose rows the marks of grants read change here (``grant_marks``): read before the rows are written
    marked = await grant_marks.changed(tx, org, sorted(d.records), d.records)
    if d.records:
        await w.records_upsert([{"sk": sk, **cols} for sk, cols in sorted(d.records.items())])
        await w.listing_set([(sk, c["coll"], c["id"], c["kind"], c["folder"], c["hide_children"], c["alien"], c["name_sort"]) for sk, c in sorted(d.records.items())])
        # Only the rows whose trash columns or pc_top change: a live record (both NULL) in a folder (pc_top false, the column's default) is no further tuple to write,
        # nor a statement for the counters' trigger.
        held = {r["sk"]: (r["deleted_ms"], r["delete_batch"], r["pc_top"]) for r in await tx.fetch(TRASH_HELD, org, sorted(d.records))}
        await w.trash_set([(sk, c["coll"], c["id"], c["kind"], c["deleted_ms"], c["delete_batch"]) for sk, c in sorted(d.records.items())
                           if held.get(sk, (None, None, False))[:2] != (c["deleted_ms"], c["delete_batch"])])
        await w.pc_top_set([(sk, c["coll"], c["id"], c["kind"], c["pc_top"]) for sk, c in sorted(d.records.items()) if held.get(sk, (None, None, False))[2] != c["pc_top"]])
        st.records = len(d.records)
    if d.gone:
        st.records_deleted = await w.records_delete(sorted(d.gone))
        await w.scopes_delete(sorted(d.gone))              # the access list of a node that is gone goes with it
    if d.cross_apps:
        done = {r["app_sk"] for r in await tx.fetch("/* pipeshub:kg.cross_read */ SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND cross_app AND app_sk = ANY($2::bigint[])",
                                                    org, sorted(d.cross_apps))}
        if d.cross_apps - done:
            await w.app_cross_set(sorted(d.cross_apps - done))          # sticky: written once, and what lets a pending change of one connector outdate the other
    gone_apps = sorted(snap.sk[k] for k in every if k[0] == "apps" and snap.sk.get(k) in d.gone)
    # A node read while its connector's node is missing is written without an app (grants too: they carry the app of the node they are on). If the node comes back before anything
    # projects the deletion, nothing re-derives those rows and the connector would stay verified over them.
    st.orphaned = {a for k, doc in snap.nodes.items() if (org_wide or k in every) and k[0] != "apps" and (a := app_id_of(k, doc)) and ("apps", a) not in snap.nodes}
    refused_under = {d.records[snap.sk[k]]["app_sk"] for k in st.refused} - {None}      # the connectors the refused rows were just written under
    off = {*gone_apps, *refused_under, *refused_g_apps, *(snap.sk[("apps", a)] for a in st.orphaned if ("apps", a) in snap.sk)}
    st.off = off
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
    # after the rows they read: the grants on what this unit wrote, and on the children of the nodes whose rows or grants changed
    regranted = {g[1] for g in drop_g} | {g[1] for g in new_g}
    await grant_marks.refresh(tx, org, set(d.records) | regranted, marked | set(d.gone) | regranted)
    drop_c = sorted(k for k in cur_closure if k not in d.closure)
    if drop_c:
        await w.closure_delete(drop_c)
    new_c = [(*k, v) for k, v in sorted(d.closure.items()) if cur_closure.get(k) != v]
    if new_c:
        await w.closure_set(new_c)
    st.closure = len(drop_c) + len(new_c)
    if stamps:      # after the structural rows: a stamp reads the rows of its parents and groups from kg. ``stamps=False`` is the backfill, which restamps a connector whole once it has copied it.
        # An app among the seeds is restamped whole. One that only the expansion brought in (a principal linked to it or granted on it is in the focus, as for any membership)
        # changed no stamp: no stamp reads who links to an app, and what records' stamps do read of their app (its facts) is compared whenever one of them is stamped.
        # A node whose grants alone changed and whose flag stays is restamped alone: no stamp below it reads anything else of a grant on it. A node whose flag kg flips is
        # restamped with everything below it, even when only its leaf flag brought it here: the records below were compiled with kg's flag (stampgraph).
        seeds = ({k for k in focus if k[0] in RECORD_COLLS and (k[0] != "apps" or k in logged_apps)} - st.shallow) | (leaf_only & flips)
        st.stamps = await stamp(tx, org, seeds, also=(leaf_only - flips) | st.shallow, locked=locked, org_wide=org_wide)
    return st


async def _grant_flags(tx: Tx, org: str, leaf_only: set[Key], candidates: set[Key], sk: dict[Key, int], d: Derived) -> tuple[set[Key], set[Key]]:
    """(the ``leaf_only`` nodes whose ``granted`` this unit flips in kg, the ``candidates`` that may be restamped alone). A candidate qualifies when kg held its row under a verified
    copy of the same connector and the flag stays: every stamp below it was compiled with kg's flag (stampgraph), and a grant reaches a stamp through nothing else. A row of a copy
    that is not verified may differ from the graph with nothing in the log to say so, and the stamps below it were compiled from the graph: those restamp as before."""
    new = {k: d.records[sk[k]] for k in leaf_only | candidates if sk.get(k) in d.records}
    sks = sorted(sk[k] for k in new)
    held: dict[int, dict] = {}
    for i in range(0, len(sks), 5000):
        held |= {r["sk"]: r for r in await tx.fetch(GRANTED_HELD, org, sks[i:i + 5000], CODE_EPOCH)}
    was = {k: held[sk[k]] for k in new if sk[k] in held}
    flips = {k for k, h in was.items() if h["granted"] != new[k]["granted"]}
    alone = {k for k, h in was.items() if k in candidates and k not in flips and h["verified"] and h["app_sk"] == new[k]["app_sk"]}
    return flips & leaf_only, alone


_CODE_STR = {str(c): coll for c, coll in CODE_COLL.items()}


def grant_only(rows: list) -> frozenset[Key]:
    """The keys that change-log rows name only in rows marked ``grant_only`` (a change of a grant on a record or record group, kg_0021)."""
    return frozenset(decode([r for r in rows if r["grant_only"]]) - decode([r for r in rows if not r["grant_only"]]))


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
