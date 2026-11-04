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


def _ids(keys: set[Key]) -> list[str]:
    return sorted({k[1] for k in keys})


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
    rows = await tx.fetch("""/* pipeshub:kg.graph_ups */ SELECT e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.to_id = ANY($2::text[])
        AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.from_coll = ANY($4::text[])""", org, _ids(keys), list(HIERARCHY), list(RECORD_COLLS))
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
    now: dict[str, set[Key]] = {}
    for r in await tx.fetch("""/* pipeshub:kg.parent_now */ SELECT e.to_id AS child, e.from_coll, e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
            AND e.to_id = ANY($2::text[]) AND e.doc->>'relationshipType' = ANY($3::text[]) AND e.from_coll = ANY($4::text[])
            AND EXISTS (SELECT 1 FROM graph.nodes p WHERE p.org_id = e.org_id AND p.coll = e.from_coll AND p.id = e.from_id)""", org, _ids(recs), list(HIERARCHY), list(RECORD_COLLS)):
        now.setdefault(r["child"], set()).add((r["from_coll"], r["from_id"]))
    before: dict[str, set[Key]] = {}
    sks = sorted((await load_sks(tx, org, recs)).values())
    for r in await tx.fetch("""/* pipeshub:kg.parent_before */ SELECT c.id AS child, p.coll AS pcoll, p.id AS pid FROM kg.records r JOIN kg.node_keys c ON c.org_id = r.org_id AND c.sk = r.sk
            JOIN kg.node_keys p ON p.org_id = r.org_id AND p.sk = r.parent_sk WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[])
            UNION SELECT c.id, p.coll, p.id FROM kg.links l JOIN kg.node_keys c ON c.org_id = l.org_id AND c.sk = l.child_sk JOIN kg.node_keys p ON p.org_id = l.org_id AND p.sk = l.parent_sk
            WHERE l.org_id = $1 AND l.child_sk = ANY($2::bigint[]) AND l.kind = 1""", org, sks):
        before.setdefault(r["child"], set()).add((CODE_COLL[r["pcoll"]], r["pid"]))
    out: set[Key] = set()
    for k in recs:
        out |= now.get(k[1], set()) ^ before.get(k[1], set())
    return {k for k in out if k[0] in RECORD_COLLS} - recs
