"""What a fresh projection of an organisation's graph holds, for comparing with what the incremental projection (flush, drain) left.

The graph of ``org`` is copied into a new organisation through the provider (every id and document naming the organisation is renamed), projected whole there, and read back with
``held``; ``same`` then compares the two with the organisation's own node called the same.
"""
from __future__ import annotations

import json

from .pg_support import new_org
from .test_kg_derivation import ctx, held
from .test_kg_projection import run, same


async def graph_rows(p, org: str) -> tuple[list, list]:
    async def go(tx) -> tuple[list, list]:
        ns = await tx.fetch("SELECT coll, id, doc FROM graph.nodes WHERE org_id = $1 ORDER BY coll, id", org)
        es = await tx.fetch("SELECT coll, from_coll, from_id, to_coll, to_id, doc FROM graph.edges WHERE org_id = $1 ORDER BY coll, from_id, to_id", org)
        return list(ns), list(es)
    with ctx(org):
        return await p._q(go, None)


def _doc(raw: object) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)  # type: ignore[arg-type]


async def fresh(p, org: str) -> tuple[dict, str]:
    """(what a whole projection of the graph holds, the organisation it was projected in)."""
    nodes, edges = await graph_rows(p, org)
    other = new_org()

    def ren(text: str) -> str:
        return text.replace(org, other)
    by_coll: dict[str, list[dict]] = {}
    for r in nodes:
        doc = json.loads(ren(json.dumps(_doc(r["doc"]))))
        by_coll.setdefault(r["coll"], []).append({**doc, "_key": ren(r["id"])})
    with ctx(other):
        for coll, docs in by_coll.items():
            await p.batch_upsert_nodes(docs, coll)
        by_edge: dict[str, list[dict]] = {}
        for e in edges:
            by_edge.setdefault(e["coll"], []).append({**json.loads(ren(json.dumps(_doc(e["doc"])))), "from_id": ren(e["from_id"]), "from_collection": e["from_coll"],
                                                     "to_id": ren(e["to_id"]), "to_collection": e["to_coll"]})
        for coll, docs in by_edge.items():
            await p.batch_create_edges(docs, coll)
    keys = {(r["coll"], ren(r["id"])) for r in nodes}
    await run(p, other, keys)
    return await held(p, other), other


async def differences(p, org: str) -> bool:
    """True when what is held for ``org`` is not what a whole projection of its graph holds."""
    want, other = await fresh(p, org)
    return not same(await held(p, org), org, want, other)
