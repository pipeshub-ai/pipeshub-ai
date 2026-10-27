"""Records reached only through BELONGS_TO (no hierarchy edge) count one level below their group: depth 1/2/3 from the App match a BFS.

Skips without a Neo4j: set NEO4J_IT_URI and NEO4J_IT_PASSWORD (see real_graph.py).
"""
from typing import Any

import pytest

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

