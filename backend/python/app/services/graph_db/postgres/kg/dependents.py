"""What else a change to a node changes in kg: the hierarchy parents whose last child came or went (their ``leaf`` flag, and their grants'), found from the graph as it is now
and from the rows kg held before, and which nodes kg already holds a row for."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import (
    CODE_COLL,
    COLL_CODE,
    HIERARCHY,
    RECORD_COLLS,
    Key,
)
from app.services.graph_db.postgres.kg.reader import load_sks

if TYPE_CHECKING:
    from app.core.db import Tx


def _pairs(keys: set[Key]) -> tuple[list[str], list[str]]:
    """The collections and ids of ``keys`` as two parallel arrays, sorted: an edge end is matched on both, never on the id alone (a record and a record group can share one)."""
    ks = sorted(keys)
    return [k[0] for k in ks], [k[1] for k in ks]


async def held_keys(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The keys among ``keys`` that kg holds a record row for (one read of the unique key (coll, id) per 5,000)."""
    ks = sorted((COLL_CODE[k[0]], k[1]) for k in keys if k[0] in COLL_CODE)
    out: set[Key] = set()
    for i in range(0, len(ks), 5000):
        part = ks[i:i + 5000]
        rows = await tx.fetch("""/* pipeshub:kg.held */ SELECT r.coll, r.id FROM kg.records r JOIN unnest($2::smallint[], $3::text[]) AS k(c, i) ON r.coll = k.c AND r.id = k.i
            WHERE r.org_id = $1""", org, [c for c, _ in part], [x for _, x in part])
        out |= {(CODE_COLL[r["coll"]], r["id"]) for r in rows}
    return out


async def _keys_of(tx: Tx, org: str, sks: set[int]) -> set[Key]:
    if not sks:
        return set()
    rows = await tx.fetch("/* pipeshub:kg.keys_of */ SELECT coll, id FROM kg.node_keys WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, sorted(sks))
    return {(CODE_COLL[r["coll"]], r["id"]) for r in rows}


async def _kg_parents(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The parents and groups of ``keys`` in the rows kg held, which can still name an edge the graph has since lost (an index lookup on the sks of the keys)."""
    sks = sorted((await load_sks(tx, org, {k for k in keys if k[0] in COLL_CODE})).values())
    if not sks:
        return set()
    found: set[int] = set()
    found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.ups_parent */ SELECT parent_sk AS x FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[]) AND parent_sk IS NOT NULL", org, sks)}
    found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.ups_group */ SELECT group_sk AS x FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[]) AND group_sk IS NOT NULL", org, sks)}
    found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.ups_links */ SELECT parent_sk AS x FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])", org, sks)}
    return await _keys_of(tx, org, found)


async def _graph_parents(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    colls, ids = _pairs(keys)
    rows = await tx.fetch("""/* pipeshub:kg.graph_ups */ SELECT e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
        AND EXISTS (SELECT 1 FROM unnest($2::text[], $5::text[]) AS k(c, i) WHERE k.c = e.to_coll AND k.i = e.to_id)
        AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.from_coll = ANY($4::text[])""", org, colls, list(HIERARCHY), list(RECORD_COLLS), ids)
    return {(r[0], r[1]) for r in rows}


async def parents_of(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The hierarchy parents of ``keys`` now and in the rows kg held before: a node whose last child went (or came) changes its ``leaf`` and its grants' ``leaf``."""
    recs = {k for k in keys if k[0] in RECORD_COLLS}
    if not recs:
        return set()
    return {k for k in await _graph_parents(tx, org, recs) | await _kg_parents(tx, org, recs) if k[0] in RECORD_COLLS} - recs


async def moved_parents(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The record-like hierarchy parents that re-deriving ``keys`` adds or drops: for each key, the parents the graph holds now (existing nodes) against the parents kg holds
    (``parent_sk`` and the hierarchy links). Read under the lock, so kg's rows are the ones the write replaces. A node that is projected only because one of its children changed
    has its row rewritten, and a parent that row stops naming has lost a child nothing else names, one it starts naming has gained one: either is re-flagged in the same unit."""
    recs = {k for k in keys if k[0] in RECORD_COLLS}
    if not recs:
        return set()
    now: dict[Key, set[Key]] = {}
    rcolls, rids = _pairs(recs)
    for r in await tx.fetch("""/* pipeshub:kg.parent_now */ SELECT e.to_coll AS ccoll, e.to_id AS child, e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
            AND EXISTS (SELECT 1 FROM unnest($2::text[], $5::text[]) AS k(c, i) WHERE k.c = e.to_coll AND k.i = e.to_id)
            AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.from_coll = ANY($4::text[])
            AND EXISTS (SELECT 1 FROM graph.nodes p WHERE p.org_id = e.org_id AND p.coll = e.from_coll AND p.id = e.from_id)""", org, rcolls, list(HIERARCHY), list(RECORD_COLLS), rids):
        now.setdefault((r["ccoll"], r["child"]), set()).add((r["from_coll"], r["from_id"]))
    before: dict[Key, set[Key]] = {}
    sks = sorted((await load_sks(tx, org, recs)).values())
    for r in await tx.fetch("""/* pipeshub:kg.parent_before */ SELECT c.coll AS ccoll, c.id AS child, p.coll AS pcoll, p.id AS pid FROM kg.records r JOIN kg.node_keys c ON c.org_id = r.org_id AND c.sk = r.sk
            JOIN kg.node_keys p ON p.org_id = r.org_id AND p.sk = r.parent_sk WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])
            UNION SELECT c.coll, c.id, p.coll, p.id FROM kg.links l JOIN kg.node_keys c ON c.org_id = l.org_id AND c.sk = l.child_sk JOIN kg.node_keys p ON p.org_id = l.org_id AND p.sk = l.parent_sk
            WHERE l.org_id = $1 AND l.child_sk = ANY($2::bigint[]) AND l.kind = 1""", org, sks):
        before.setdefault((CODE_COLL[r["ccoll"]], r["child"]), set()).add((CODE_COLL[r["pcoll"]], r["pid"]))
    out: set[Key] = set()
    for k in recs:
        out |= now.get(k, set()) ^ before.get(k, set())
    return {k for k in out if k[0] in RECORD_COLLS} - recs


async def children_of(tx: Tx, org: str, keys: set[Key]) -> set[Key]:
    """The hierarchy children and group members of ``keys`` now (the graph) and in the rows kg held before (which can still name an edge the graph has since lost)."""
    recs = {k for k in keys if k[0] in RECORD_COLLS}
    if not recs:
        return set()
    colls, ids = _pairs(recs)
    out: set[Key] = set()
    for r in await tx.fetch("""/* pipeshub:kg.graph_kids */ SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
            AND EXISTS (SELECT 1 FROM unnest($2::text[], $5::text[]) AS k(c, i) WHERE k.c = e.from_coll AND k.i = e.from_id)
            AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.to_coll = ANY($4::text[])""", org, colls, list(HIERARCHY), list(RECORD_COLLS), ids):
        out.add((r[0], r[1]))
    for r in await tx.fetch("""/* pipeshub:kg.graph_members */ SELECT e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
            AND EXISTS (SELECT 1 FROM unnest($2::text[], $4::text[]) AS k(c, i) WHERE k.c = e.to_coll AND k.i = e.to_id)
            AND e.from_coll = ANY($3::text[])""", org, colls, list(RECORD_COLLS), ids):
        out.add((r[0], r[1]))
    sks = sorted((await load_sks(tx, org, recs)).values())
    if sks:
        found: set[int] = set()
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.kids_parent */ SELECT sk AS x FROM kg.records WHERE org_id = $1 AND parent_sk = ANY($2::bigint[])", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.kids_group */ SELECT sk AS x FROM kg.records WHERE org_id = $1 AND group_sk = ANY($2::bigint[])", org, sks)}
        found |= {r["x"] for r in await tx.fetch("/* pipeshub:kg.kids_links */ SELECT child_sk AS x FROM kg.links WHERE org_id = $1 AND parent_sk = ANY($2::bigint[])", org, sks)}
        out |= await _keys_of(tx, org, found)
    return {k for k in out if k[0] in RECORD_COLLS}


async def held_apps(tx: Tx, org: str, keys: set[Key]) -> dict[Key, str | None]:
    """The connector kg holds each of ``keys`` under (None: a row with no connector); a key kg holds no row for is not in the result."""
    ks = sorted((COLL_CODE[k[0]], k[1]) for k in keys if k[0] in COLL_CODE)
    out: dict[Key, str | None] = {}
    for i in range(0, len(ks), 5000):
        part = ks[i:i + 5000]
        rows = await tx.fetch("""/* pipeshub:kg.held_apps */ SELECT r.coll, r.id, a.id AS app FROM kg.records r JOIN unnest($2::smallint[], $3::text[]) AS k(c, i) ON r.coll = k.c AND r.id = k.i
            LEFT JOIN kg.node_keys a ON a.org_id = r.org_id AND a.sk = r.app_sk WHERE r.org_id = $1""", org, [c for c, _ in part], [x for _, x in part])
        out |= {(CODE_COLL[r["coll"]], r["id"]): r["app"] for r in rows}
    return out
