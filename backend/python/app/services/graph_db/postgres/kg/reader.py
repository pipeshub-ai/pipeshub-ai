"""Reading the graph for the projection: documents and edges of the focus nodes, their neighbours, and the sk of every keyed node.

All statements run in the caller's unit (the writer's own transaction for the flush, a backfill batch's for the backfill) and under the ambient org.
The reads are made after the stamping lock is taken (see project.py): a provider write that projected and acknowledged meanwhile is either
visible here or waits for the lock.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    COLL_CODE,
    HIERARCHY,
    LINK_HIERARCHY,
    MAX_ID,
    RECORD_COLLS,
    GEdge,
    Key,
    Snapshot,
    app_id_of,
    hierarchy_edge,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.core.db import Tx

# Only the fields the derivation reads travel from the database: the projected subset of a node document (kg.proj_doc, none for a principal, whose existence is all that is
# read) and the four fields of an edge document that model.py looks at (written out in each statement: a statement is a literal).


def _doc(raw: object) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)  # type: ignore[arg-type]


def _edge(r: object) -> GEdge:
    return GEdge(r["coll"], r["from_coll"], r["from_id"], r["to_coll"], r["to_id"], _doc(r["doc"]))  # type: ignore[index]


def _split(keys: set[Key]) -> tuple[list[str], list[str]]:
    ks = sorted(keys)
    return [k[0] for k in ks], [k[1] for k in ks]


async def load_nodes(tx: Tx, org: str, keys: set[Key]) -> dict[Key, dict]:
    keys = {k for k in keys if k[0] in COLL_CODE and len(k[1]) <= MAX_ID}
    if not keys:
        return {}
    colls, ids = _split(keys)
    rows = await tx.fetch("""/* pipeshub:kg.read_nodes */ SELECT n.coll, n.id, COALESCE(kg.proj_doc(n.coll, n.doc), '{}'::jsonb) AS doc FROM graph.nodes n
        JOIN unnest($2::text[], $3::text[]) AS k(c, i) ON n.coll = k.c AND n.id = k.i WHERE n.org_id = $1""", org, colls, ids)
    return {(r["coll"], r["id"]): _doc(r["doc"]) for r in rows}


# What derive() reads of a node's edges, by the kind of node (model.py): a record, record group or app reads the grants, hierarchy links and user-app links that point at it
# and its own belongs-to links; a principal reads the grants, user-app links, belongs-to (the org) and authenticated-as links that start at it. Nothing else is fetched, so the
# cost of a projection follows the degree of the nodes that changed and never the size of a group, a folder or an app.
_INTO_RECORD = ("permission", "nodeRelations", "userAppRelation")
_OUT_PRINCIPAL = ("permission", "userAppRelation", "belongsTo", "authenticatedAs")


async def load_edges(tx: Tx, org: str, keys: set[Key]) -> tuple[list[GEdge], list[GEdge]]:
    """(edges that point at the record-like keys, edges that start at the principal keys and the belongs-to edges that start at the record-like keys)."""
    if not keys:
        return [], []
    rec_ids = sorted({k[1] for k in keys if k[0] in RECORD_COLLS})
    prin_ids = sorted({k[1] for k in keys if k[0] not in RECORD_COLLS})
    to: list[GEdge] = []
    frm: list[GEdge] = []
    if rec_ids:
        rows = await tx.fetch("""/* pipeshub:kg.read_edges_to */ SELECT e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, jsonb_strip_nulls(jsonb_build_object('type', e.doc->'type', 'role', e.doc->'role', 'relationshipType', e.doc->'relationshipType', 'connectorId', e.doc->'connectorId')) AS doc FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_id = ANY($3::text[])""", org, list(_INTO_RECORD), rec_ids)
        to = [e for e in map(_edge, rows) if e.to in keys and (e.coll != "nodeRelations" or hierarchy_edge(e))]
        rows = await tx.fetch("""/* pipeshub:kg.read_edges_belongs */ SELECT e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, jsonb_strip_nulls(jsonb_build_object('type', e.doc->'type', 'role', e.doc->'role', 'relationshipType', e.doc->'relationshipType', 'connectorId', e.doc->'connectorId')) AS doc FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_id = ANY($2::text[])""", org, rec_ids)
        frm = [e for e in map(_edge, rows) if e.frm in keys]
    if prin_ids:
        rows = await tx.fetch("""/* pipeshub:kg.read_edges_from */ SELECT e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, jsonb_strip_nulls(jsonb_build_object('type', e.doc->'type', 'role', e.doc->'role', 'relationshipType', e.doc->'relationshipType', 'connectorId', e.doc->'connectorId')) AS doc FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_id = ANY($3::text[])""", org, list(_OUT_PRINCIPAL), prin_ids)
        frm += [e for e in map(_edge, rows) if e.frm in keys]
    return to, frm


# A record's isOfType edges to 'files' and what the knowledge-base listing reads of each file (kg.proj_doc; NULL when the file node is gone), one seek of the edges' key per record.
FILES = """/* pipeshub:kg.read_files */ SELECT e.from_id, e.to_id, kg.proj_doc('files', f.doc) AS doc FROM graph.edges e
    LEFT JOIN graph.nodes f ON f.org_id = e.org_id AND f.coll = 'files' AND f.id = e.to_id
    WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_coll = 'records' AND e.to_coll = 'files' AND e.from_id = ANY($2::text[])"""


async def load_files(tx: Tx, org: str, keys: set[Key]) -> dict[Key, list[dict | None]]:
    """The isOfType targets in 'files' of the records among ``keys``, by the file's id."""
    ids = sorted(k[1] for k in keys if k[0] == "records" and len(k[1]) <= MAX_ID)
    out: dict[Key, list[tuple[str, dict | None]]] = {}
    for r in (await tx.fetch(FILES, org, ids)) if ids else []:
        out.setdefault(("records", r["from_id"]), []).append((r["to_id"], None if r["doc"] is None else _doc(r["doc"])))
    return {k: [d for _, d in sorted(v, key=lambda x: x[0])] for k, v in out.items()}


async def load_sks(tx: Tx, org: str, keys: set[Key]) -> dict[Key, int]:
    """The sk of keys that already have one."""
    keys = {k for k in keys if k[0] in COLL_CODE}
    if not keys:
        return {}
    ks = sorted(keys)
    rows = await tx.fetch("""/* pipeshub:kg.read_keys */ SELECT n.coll, n.id, n.sk FROM kg.node_keys n
        JOIN unnest($2::smallint[], $3::text[]) AS k(c, i) ON n.coll = k.c AND n.id = k.i WHERE n.org_id = $1""",
                          org, [COLL_CODE[k[0]] for k in ks], [k[1] for k in ks])
    return {(CODE_COLL[r["coll"]], r["id"]): r["sk"] for r in rows}  # type: ignore[misc]


# The settings under which a read keyed per row is a seek whatever the statistics say (as hierarchy.PROFILE): with sorts (incremental ones too), bitmap and sequential scans
# off, the only plan left is the index whose order the read asks for. A leaf analysed before the org's rows came puts the org at one row, and every index on the org then looks as
# cheap as the seek; once the org is analysed, a bitmap read of a large parent and a sort look cheaper than stopping at its first child. JIT is off: a disabled node's cost would
# compile the statement. They are put back after the read, which runs inside a writer's unit; a read that fails takes them with its unit or savepoint.
SEEK_READ = ("/* pipeshub:kg.seek_read */ SELECT current_setting('enable_sort'), current_setting('enable_incremental_sort'), current_setting('enable_bitmapscan'), "
             "current_setting('enable_seqscan'), current_setting('jit')")
SEEK_SET = ("/* pipeshub:kg.seek_set */ SELECT set_config('enable_sort', $1, true), set_config('enable_incremental_sort', $2, true), set_config('enable_bitmapscan', $3, true), "
            "set_config('enable_seqscan', $4, true), set_config('jit', $5, true)")


@asynccontextmanager
async def seeks(tx: Tx) -> AsyncIterator[None]:
    was = tuple(await tx.fetchrow(SEEK_READ))
    await tx.fetchval(SEEK_SET, "off", "off", "off", "off", "off")
    yield
    await tx.fetchval(SEEK_SET, *was)


# A connector's records in byte order of id after a cursor, a seek on the connector's index (nodes_record_connector_c). The role's own statement cannot use that index under
# row-level security (the connector is read through a function that is not leakproof), so it would read every record of the org after the cursor for each page; the vetted read can.
RECORDS_AFTER = "/* pipeshub:kg.records_after */ SELECT node_id FROM graph.records_page_by_connector($1, $2, $3)"
# Beyond the vetted read's bounds (a connector id or a cursor of more than 200 characters): the same page, slowly.
RECORDS_AFTER_LONG = """/* pipeshub:kg.records_after_long */ SELECT n.id AS node_id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'connectorId' = $2
    AND n.id COLLATE "C" > $3::text COLLATE "C" ORDER BY n.id COLLATE "C" LIMIT $4"""
# A cursor in a connector's records in that order is written with this collection. A cursor of "records" was written by code that paged them in the database's collation, where
# the records after it are not those after it in byte order: what it was paging goes on from the first record (copying or stamping a node again writes the same rows).
RECORDS_BYTE_ORDER = "records:c"


async def records_after(tx: Tx, org: str, app_id: str, after: str, limit: int) -> list[str]:
    """The ids of the connector's next records after ``after`` ('' from the first) in byte order: at most 1,000, whatever ``limit`` asks. A page costs its own rows."""
    n = min(limit, 1000)
    async with seeks(tx):       # the vetted read plans its statement under the caller's settings
        if 0 < len(app_id) <= 200 and len(after) <= 200:
            rows = await tx.fetch(RECORDS_AFTER, app_id, after, n)
        else:
            rows = await tx.fetch(RECORDS_AFTER_LONG, org, app_id, after, n)
    return [r["node_id"] for r in rows]


# One probe per parent that stops at its first child that exists: a batch costs the parents it touches, not their children, which for an app or a large folder is the whole
# connector. LIMIT 1 keeps the lateral a loop over the parents. Under ``seeks`` the probe reads the relations' index by parent (edges_by_neighbour_out) in its own order, the parent
# matched in byte order (the column's own equality, which is deterministic), so it stops at the first child that exists. The child's node is a scalar subquery, a seek per edge.
HAS_CHILDREN = """/* pipeshub:kg.read_has_children */ SELECT p.c AS from_coll, p.i AS from_id FROM unnest($2::text[], $3::text[]) AS p(c, i)
    CROSS JOIN LATERAL (SELECT 1 FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = p.c AND e.from_id COLLATE "C" = p.i COLLATE "C"
      AND e.doc->>'relationshipType' = ANY($4::text[]) AND e.to_coll = ANY($5::text[])
      AND (SELECT true FROM graph.nodes c WHERE c.org_id = e.org_id AND c.coll = e.to_coll AND c.id = e.to_id LIMIT 1)
      ORDER BY e.from_coll, e.from_id COLLATE "C", e.to_coll COLLATE "C", e.to_id COLLATE "C" LIMIT 1) x"""


async def load_has_children(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The record-like keys among ``keys`` that have at least one hierarchy child that exists as a node (an edge to a node that is gone does not count)."""
    colls, ids = _split({k for k in keys if k[0] in RECORD_COLLS})
    if not ids:
        return set()
    async with seeks(tx):
        rows = await tx.fetch(HAS_CHILDREN, org, colls, ids, list(HIERARCHY), list(RECORD_COLLS))
    return {(r["from_coll"], r["from_id"]) for r in rows}


async def load_known_children(tx: Tx, org: str, keys: set[Key], focus: set[Key], nodes: dict[Key, dict], edges_to: dict[Key, list[GEdge]], sk: dict[Key, int]) -> set[Key]:
    """The record-like keys among ``keys`` that have a hierarchy child *kg knows of*: a child whose row names the parent (``parent_sk`` or a hierarchy link) or one of ``focus``,
    whose rows this unit is about to write, that is derived to name it. A parent's ``leaf`` then rests on rows kg holds, and whoever changes a child finds the parent from the child's
    old row: a child that came and went before anything projected it left nothing that could tell its parent, so it must not have been counted. The children of ``focus`` are not read
    from kg (their rows are about to be replaced)."""
    out = {e.frm for k in focus if k[0] in RECORD_COLLS and k in nodes for e in edges_to.get(k, []) if hierarchy_edge(e) and e.frm[0] in RECORD_COLLS and e.frm in nodes and e.frm in sk}
    by_sk = {sk[k]: k for k in keys if k[0] in RECORD_COLLS and k in sk}
    if by_sk:
        rows = await tx.fetch("""/* pipeshub:kg.read_known_children */ SELECT p.sk FROM unnest($2::bigint[]) AS p(sk) WHERE
            EXISTS (SELECT 1 FROM kg.records r WHERE r.org_id = $1 AND r.parent_sk = p.sk AND r.sk <> ALL($3::bigint[]))
            OR EXISTS (SELECT 1 FROM kg.links l WHERE l.org_id = $1 AND l.parent_sk = p.sk AND l.kind = $4 AND l.child_sk <> ALL($3::bigint[]))""",
                              org, sorted(by_sk), sorted(sk[k] for k in focus if k in sk), LINK_HIERARCHY)
        out |= {by_sk[r["sk"]] for r in rows}
    return out & keys


async def snapshot(tx: Tx, org: str, focus: set[Key], allocate, *, known_children: bool = False) -> Snapshot:  # noqa: ANN001
    """The graph around ``focus``: their documents, their edges, the endpoint documents, and for a source account the edges of its own (the
    badge groups). ``allocate(keys)`` returns the sks of the keys that exist as nodes, creating them (the vetted node-key writer). ``known_children``: a parent's ``leaf`` counts the
    children kg knows of (:func:`load_known_children`) instead of those the graph holds; the projection of changes, which finds a parent from its child's rows, asks for it, a
    backfill and a verify read the graph."""
    focus = {k for k in focus if k[0] in COLL_CODE}
    nodes = await load_nodes(tx, org, focus)
    to, frm = await load_edges(tx, org, focus)
    ends = {e.frm for e in to} | {e.to for e in to} | {e.frm for e in frm} | {e.to for e in frm}
    # a user's source accounts: their out-edges give the badge groups
    sources = {e.to for e in frm if e.coll == "authenticatedAs"}
    s_to, s_frm = await load_edges(tx, org, sources - focus) if sources - focus else ([], [])
    ends |= {e.to for e in s_frm} | {e.frm for e in s_frm} | {e.frm for e in s_to} | {e.to for e in s_to}
    ends |= {("apps", c) for e in frm if e.coll == "authenticatedAs" and isinstance(c := e.doc.get("connectorId"), str)}      # the connector a source account is linked in
    nodes.update(await load_nodes(tx, org, ends - set(nodes)))
    apps = {("apps", c) for k, d in nodes.items() if k[0] in RECORD_COLLS and (c := app_id_of(k, d))}        # the app of every node: its sk is the node's app_sk
    nodes.update(await load_nodes(tx, org, apps - set(nodes)))
    ends |= apps
    edges_to: dict[Key, list[GEdge]] = {}
    edges_from: dict[Key, list[GEdge]] = {}
    for e in [*to, *s_to]:
        edges_to.setdefault(e.to, []).append(e)
    for e in [*frm, *s_frm]:
        edges_from.setdefault(e.frm, []).append(e)
    existing = {k for k in (focus | ends) if k in nodes}
    sk = await allocate(existing)
    sk.update(await load_sks(tx, org, (focus | ends) - set(sk)))      # keys of nodes that are gone stay (never deleted)
    kids = await (load_known_children(tx, org, set(nodes), focus, nodes, edges_to, sk) if known_children else load_has_children(tx, org, set(nodes)))
    return Snapshot(nodes, edges_to, edges_from, sk, kids, org, await load_files(tx, org, focus))
