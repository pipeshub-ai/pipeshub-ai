"""Records reached only through BELONGS_TO (no hierarchy edge) count one level below their group: depth 1/2/3 from the App match a BFS."""
import os
from typing import Any

import pytest

import tests.integration.test_tool_bounds_neo4j_e2e as tb
from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.test_tool_bounds_neo4j_e2e import (  # noqa: F401
    APP,
    REC,
    RG,
    _rec,
    bfs,
    nodes,
    w,
)

tb.BIG = int(os.environ.get("TOOL_BOUNDS_BIG_FOLDER", "300"))
tb.ATT = int(os.environ.get("TOOL_BOUNDS_ATTACHMENTS", "100"))

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


async def _seed_unplaced(w) -> None:  # noqa: F811
    now = get_epoch_timestamp_in_ms()
    st = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    ids = [w.n("U1"), w.n("U2")]
    await w.g.batch_upsert_records([_rec(w, i) for i in ids])
    def ed(a, ac, b, bc, **x) -> dict[str, Any]:
        return {"from_id": a, "from_collection": ac, "to_id": b, "to_collection": bc, **st, **x}
    await w.g.batch_create_edges([ed(ids[0], REC, w.n("G1"), RG), ed(ids[1], REC, w.app, APP)], collection=CollectionNames.BELONGS_TO.value)
    await w.g.batch_create_edges([ed(w.user, "users", i, REC, role="READER", type="USER") for i in ids], collection=CollectionNames.PERMISSION.value)
    w.hier.setdefault(w.n("G1"), []).append(ids[0])   # depth(U1) = 2
    w.hier.setdefault(w.app, []).append(ids[1])       # depth(U2) = 1


@pytest.mark.parametrize("depth", [1, 2, 3])
async def test_unplaced(w, depth) -> None:  # noqa: F811
    await _seed_unplaced(w)
    items, _, _ = await nodes(w, w.app, "app", flattened=True, depth=depth, limit=1000)
    got = {i.id for i in items}
    allowed = set(bfs(w, w.app, depth))
    assert got == allowed, (sorted(got - allowed)[:5], sorted(allowed - got)[:5], depth)


async def test_profile(w) -> None:  # noqa: F811
    cap = []
    orig = w.g.client.execute_query
    async def spy(q, parameters=None, **kw) -> list[dict[str, Any]]:
        cap.append((q, parameters))
        return await orig(q, parameters=parameters, **kw)
    w.g.client.execute_query = spy
    await nodes(w, w.app, "app", flattened=True, depth=2, limit=100)
    w.g.client.execute_query = orig
    big = [c for c in cap if "BELONGS_TO" in c[0] and "kh_wr" in c[0]]
    assert big, "bounded query not seen"
    q, p = big[0]
    from neo4j import AsyncGraphDatabase
    drv = AsyncGraphDatabase.driver(os.environ["NEO4J_IT_URI"], auth=("neo4j", os.environ["NEO4J_IT_PASSWORD"]))
    async with drv.session() as s:
        res = await s.run("PROFILE " + q, p)
        await res.consume()
        summ = await res.consume()
    def walk(pl) -> int:
        return (pl.get('dbHits') or 0) + sum(walk(c) for c in (pl.get('children') or []))
    ops=[]
    def col(pl) -> None:
        ops.append((pl.get('dbHits') or 0, pl['operatorType'], (pl['args'].get('Details') or '')[:110]))
        for c in pl.get('children') or []:
            col(c)
    col(summ.profile)
    for o in sorted(ops, reverse=True)[:8]:
        print("OP", o)
    print("PROFILE_DBHITS", walk(summ.profile), "records", 400 + 100 + 6)
    await drv.close()
