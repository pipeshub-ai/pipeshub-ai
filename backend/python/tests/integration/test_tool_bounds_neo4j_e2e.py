"""Tool bounds on a real Neo4j: knowledge-hub depth, paging on a 5k folder, bounded relation reads.

Depth is the number of hierarchy hops from the start node (App -> group is 1). The
seed gives every record both a hierarchy edge and a BELONGS_TO edge to its group, as
real connectors do, so belonging to a near group must not admit a deep record.

Skips without a Neo4j: set NEO4J_IT_URI and NEO4J_IT_PASSWORD (see real_graph.py).
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.models.entities import FileRecord, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
log = logging.getLogger("tool-bounds-e2e")
BIG, ATT = 5000, 1200
RG, REC, USR, APP = (CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value,
                     CollectionNames.USERS.value, CollectionNames.APPS.value)


class W:
    pass


def _rec(w, rid, folder=False) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    return FileRecord(
        id=rid, org_id=w.org, record_name=rid, record_type=RecordType.FILE, external_record_id=f"ext-{rid}",
        version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=w.app,
        mime_type="application/vnd.folder" if folder else "application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value, is_file=not folder,
        extension=None if folder else "pdf", size_in_bytes=0 if folder else 10,
        created_at=now, updated_at=now, source_created_at=now, source_updated_at=now)


def _chunks(xs, n=1000) -> Iterator[list[Any]]:
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


@pytest.fixture
async def w(monkeypatch) -> AsyncIterator[W]:
    try:
        g = await connect_neo4j(log, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    run = uuid.uuid4().hex[:8]
    w = W()
    w.g, w.run, w.org, w.app = g, run, f"org-{run}", f"app-{run}"
    w.user = f"user-{run}"
    def n(s) -> str:
        return f"{s}-{run}"

    w.n = n
    now = get_epoch_timestamp_in_ms()
    st = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    groups = ["G1", "G1a", "G2", "G3"]
    folders = ["F1", "F2", "BIG", "P"]  # P: record with ATT attachments
    smalls = ["R_both", "R_deep", "R_g2"]
    bigkids = [f"c{i}" for i in range(BIG)]
    atts = [f"a{i}" for i in range(ATT)]
    all_recs = folders + smalls + bigkids + atts
    await g.batch_upsert_nodes([{"id": w.user, "userId": f"uid-{w.user}", "orgId": w.org, "email": f"{w.user}@x.com",
                                 "fullName": "u", "isActive": True, **st}], collection=USR)
    await g.batch_upsert_nodes([{"id": w.app, "name": "Drive", "type": Connectors.GOOGLE_DRIVE.value,
                                 "appGroup": "Google Workspace", "scope": "team", "isActive": True,
                                 "orgId": w.org, **st}], collection=APP)
    await g.batch_upsert_nodes([{"id": n(x), "orgId": w.org, "groupName": x, "externalGroupId": f"e-{n(x)}",
                                 "groupType": "DRIVE", "connectorName": Connectors.GOOGLE_DRIVE.value,
                                 "connectorId": w.app, "sourceCreatedAtTimestamp": now,
                                 "sourceLastModifiedTimestamp": now, **st} for x in groups], collection=RG)
    for ch in _chunks([_rec(w, n(x), x in ("F1", "F2", "BIG")) for x in all_recs]):
        await g.batch_upsert_records(ch)

    def e(a, ac, b, bc, **x) -> dict[str, Any]:
        return {"from_id": a, "from_collection": ac, "to_id": b, "to_collection": bc, **st, **x}
    belongs, hier, grants = [], [], []
    w.hier = {}  # parent -> children, for ground truth

    def h(pn, pc, cn, cc) -> None:
        hier.append(e(pn, pc, cn, cc, relationshipType="PARENT_CHILD"))
        w.hier.setdefault(pn, []).append(cn)
    for x in ("G1", "G2", "G3"):
        belongs.append(e(n(x), RG, w.app, APP))
        h(w.app, APP, n(x), RG)
    belongs.append(e(n("G1a"), RG, n("G1"), RG))
    h(n("G1"), RG, n("G1a"), RG)
    # record -> group membership (all in G1 except G2/G3 ones)
    grp_of = {"F1": "G1", "F2": "G1", "R_both": "G1", "R_deep": "G1", "R_g2": "G2", "BIG": "G3", "P": "G3"}
    for r in bigkids:
        grp_of[r] = "G3"
    for r in atts:
        grp_of[r] = "G3"
    for r, gname in grp_of.items():
        belongs.append(e(n(r), REC, n(gname), RG))
    h(n("G1"), RG, n("F1"), REC)          # depth 2 from app
    h(n("F1"), REC, n("F2"), REC)         # depth 3
    h(n("G1"), RG, n("R_both"), REC)      # depth 2 (arm 1)
    h(n("F2"), REC, n("R_both"), REC)     # depth 4 (arm 2): also under F2
    h(n("F2"), REC, n("R_deep"), REC)     # depth 4 only
    h(n("G2"), RG, n("R_g2"), REC)
    h(n("G3"), RG, n("BIG"), REC)         # depth 2
    h(n("G3"), RG, n("P"), REC)
    for r in bigkids:
        h(n("BIG"), REC, n(r), REC)       # depth 3
    for r in atts:
        h(n("P"), REC, n(r), REC)         # depth 3 (hierarchy edge)
    # attachments as ATTACHMENT relation too (what enrichment reads)
    att_edges = [e(n("P"), REC, n(r), REC, relationshipType="ATTACHMENT") for r in atts]
    grants.extend(e(w.user, USR, n(x), RG, role="READER", type="USER") for x in groups)
    grants.extend(e(w.user, USR, n(r), REC, role="READER", type="USER") for r in all_recs)
    for ch in _chunks(belongs):
        assert await g.batch_create_edges(ch, collection=CollectionNames.BELONGS_TO.value)
    for ch in _chunks(hier):
        assert await g.batch_create_edges(ch, collection=CollectionNames.NODE_RELATIONS.value)
    for ch in _chunks(att_edges):
        assert await g.batch_create_edges(ch, collection=CollectionNames.NODE_RELATIONS.value)
    for ch in _chunks(grants):
        assert await g.batch_create_edges(ch, collection=CollectionNames.PERMISSION.value)
    assert await g.batch_create_edges(
        [e(w.user, USR, w.app, APP, syncState="COMPLETED", lastSyncUpdate=now, isExternalUser=False)],
        collection=CollectionNames.USER_APP_RELATION.value)
    yield w
    await g.client.execute_query("MATCH (n) WHERE n.connectorId = $a OR n.id = $a OR n.id = $u DETACH DELETE n",
                                 parameters={"a": w.app, "u": w.user})


def bfs(w, start, d) -> dict[str, int]:
    seen, q = {start: 0}, deque([start])
    while q:
        x = q.popleft()
        if seen[x] == d:
            continue
        for c in w.hier.get(x, []):
            if c not in seen:
                seen[c] = seen[x] + 1
                q.append(c)
    seen.pop(start)
    return seen


async def nodes(w, start, ptype, **kw) -> tuple[list[Any], int, Any]:
    svc = KnowledgeHubService(log, w.g)
    out, cursor = [], None
    pages = 0
    while True:
        r = await svc.get_nodes(user_id=f"uid-{w.user}", org_id=w.org, parent_id=start, parent_type=ptype,
                                sort_by="name", sort_order="asc", cursor=cursor, **{"limit": 100, **kw})
        assert r.success, r.error
        out += r.items
        pages += 1
        cursor = r.pagination.nextCursor
        if not r.pagination.hasNext or pages > 100:
            return out, pages, r
        if not cursor:
            kw = {**kw, "page": pages + 1}  # no cursor secret in this harness: page by number


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("start,ptype", [("APP", "app"), ("F1", "folder"), ("G1", "recordGroup")])
async def test_flatten_returns_exactly_the_nodes_within_the_depth(w, start, ptype, depth) -> None:
    sid = w.app if start == "APP" else w.n(start)
    items, _, _ = await nodes(w, sid, ptype, flattened=True, depth=depth, limit=1000)
    got = {i.id for i in items}
    allowed = set(bfs(w, sid, depth))
    assert got == allowed, (sorted(got - allowed)[:5], sorted(allowed - got)[:5])


@pytest.mark.parametrize("start,ptype", [("APP", "app"), ("F1", "folder"), ("G1", "recordGroup")])
async def test_browse_depth1_direct_children(w, start, ptype) -> None:
    sid = w.app if start == "APP" else w.n(start)
    items, _, _ = await nodes(w, sid, ptype, flattened=False, depth=1, limit=1000)
    got = {i.id for i in items}
    assert got <= set(bfs(w, sid, 1))


async def test_big_folder_pages(w) -> None:
    r = await KnowledgeHubService(log, w.g).get_nodes(
        user_id=f"uid-{w.user}", org_id=w.org, parent_id=w.n("BIG"), parent_type="folder",
        flattened=False, depth=1, limit=50, sort_by="name", sort_order="asc")
    assert r.success, r.error
    assert len(r.items) == 50 and r.pagination.hasNext
    assert r.pagination.totalItems == BIG
    items, pages, _ = await nodes(w, w.n("BIG"), "folder", flattened=False, depth=1, limit=1000)
    assert len({i.id for i in items}) == BIG, len(items)


async def test_relations_batch_is_bounded_per_record_and_direction(w) -> None:
    full = await w.g.get_node_relations_batch([w.n("P")], ["ATTACHMENT"])
    assert sum(len(v) for v in full[w.n("P")].values()) == ATT
    out = await w.g.get_node_relations_batch([w.n("P"), w.n("a0")], ["ATTACHMENT"], limit_per_direction=150)
    assert len(out[w.n("P")]["parents"]) == 150  # outgoing edges are the anchor's "parents"
    assert all(len(v) <= 150 for r in out.values() for v in r.values())
