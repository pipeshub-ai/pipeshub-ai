"""Reading the graph for the projection: documents and edges of the focus nodes, their neighbours, and the sk of every keyed node.

All statements run in the caller's unit (the writer's own transaction for the flush, a backfill batch's for the backfill) and under the ambient org.
The reads are made after the stamping lock is taken (see project.py): a provider write that projected and acknowledged meanwhile is either
visible here or waits for the lock.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    COLL_CODE,
    HIERARCHY,
    LINK_HIERARCHY,
    RECORD_COLLS,
    GEdge,
    Key,
    Snapshot,
    app_id_of,
    hierarchy_edge,
)

if TYPE_CHECKING:
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
    keys = {k for k in keys if k[0] in COLL_CODE}
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


async def load_has_children(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The record-like keys among ``keys`` that have at least one hierarchy child that exists as a node (an edge to a node that is gone does not count)."""
    ids = sorted({k[1] for k in keys if k[0] in RECORD_COLLS})
    if not ids:
        return set()
    rows = await tx.fetch("""/* pipeshub:kg.read_has_children */ SELECT DISTINCT e.from_coll, e.from_id FROM graph.edges e
        WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_id = ANY($2::text[]) AND e.doc->>'relationshipType' = ANY($3::text[])
          AND EXISTS (SELECT 1 FROM graph.nodes c WHERE c.org_id = e.org_id AND c.id = e.to_id AND c.coll = ANY($4::text[]))""", org, ids, list(HIERARCHY), list(RECORD_COLLS))
    return {(r["from_coll"], r["from_id"]) for r in rows} & keys


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
    return Snapshot(nodes, edges_to, edges_from, sk, kids, org)
