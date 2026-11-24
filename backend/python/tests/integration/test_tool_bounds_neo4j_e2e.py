"""Tool bounds on a real Neo4j: knowledge-hub depth, paging on a 5k folder, bounded relation reads.

Depth is the number of hierarchy hops from the start node (App -> group is 1). The
seed gives every record both a hierarchy edge and a BELONGS_TO edge to its group, as
real connectors do, so belonging to a near group must not admit a deep record.

Skips without a Neo4j: set NEO4J_IT_URI and NEO4J_IT_PASSWORD (see real_graph.py).
"""

from __future__ import annotations

import logging
import re
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
from app.services.graph_db.neo4j.neo4j_provider import FOLDER_MIME_TYPES, LISTED
from app.services.graph_db.neo4j.permission_stamps import Neo4jStampGraph
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


async def _node_count(g: Any) -> int:  # noqa: ANN401
    return (await g.client.execute_query("MATCH (n) RETURN count(n) AS n"))[0]["n"]


@pytest.fixture
async def w(monkeypatch) -> AsyncIterator[W]:
    try:
        g = await connect_neo4j(log, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    before = await _node_count(g)
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
    # Every node the seed wrote carries the test's org or App (a record's type node has its org and no connectorId), or its run suffix.
    mine = "n.orgId = $o OR n.connectorId = $a OR n.id IN [$a, $u] OR n.id ENDS WITH $sfx"
    params = {"o": w.org, "a": w.app, "u": w.user, "sfx": f"-{run}"}
    await g.client.execute_query(f"MATCH (n) WHERE {mine} DETACH DELETE n", parameters=params)
    left = await g.client.execute_query(f"MATCH (n) WHERE {mine} RETURN count(n) AS n", parameters=params)
    assert left[0]["n"] == 0, left
    # The tests run one at a time on their store (as CI runs this file), so the store is back to what it held before the seed.
    assert await _node_count(g) == before


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


def _plan_ops(plan: Any) -> Iterator[Any]:  # noqa: ANN401
    yield plan
    for child in plan.get("children") or []:
        yield from _plan_ops(child)


async def test_a_page_reads_the_listed_ids_on_the_id_index_whatever_the_plan(w) -> None:
    """A split browse's page reads the ids it decided (`LISTED`) by each label's unique id index, whatever the statistics. With the
    App and org tests in its match, a plan sought the App's connectorId index and tested the id after: every record of the App read
    once per listed id, 62M db hits and 25 s a page on this folder, past the test's timeout over its pages."""
    kids = sorted(w.n(f"c{i}") for i in range(BIG))
    async with w.g.client.driver.session(database="neo4j") as s:
        result = await s.run(f"PROFILE {LISTED.format(name='ids')} RETURN size(ids) AS n",
                             {"kh_listed": kids, "org_id": w.org, "app_id": w.app})
        n = (await result.single())["n"]
        profile = (await result.consume()).profile
    assert n == BIG
    ops = list(_plan_ops(profile))
    reads = [o for o in ops if "kh_x" in (o.get("args") or {}).get("Details", "")
             and ("Seek" in o["operatorType"] or "Scan" in o["operatorType"])]
    assert reads and all(o["operatorType"].startswith("NodeUniqueIndexSeek") for o in reads), [o["operatorType"] for o in reads]
    # Two seeks per id (one per label) and a few property reads: a plan reading the App's records per id is thousands per id.
    hits = sum(o.get("dbHits") or 0 for o in ops)
    assert hits <= 12 * BIG, hits


# A seek on an index that holds the id beside the org or App (`Record(orgId, id)`, `RecordGroup(connectorId, id)`): the planner chose it
# because the statement tests the org or App next to its match by id, and with other statistics it seeks the org or App index alone.
# A paged read (`id > $after`) on such an index is not one: only an equality or IN on the id is.
_SEEK_BESIDE_ID = re.compile(r"INDEX \w+:(?:Record|RecordGroup|App)\((?=[^)]*,)[^)]*\bid\b[^)]*\) WHERE .*\bid (?:=|IN) ")


# Nor does any read every record or group of the org or the label: these reads start from ids, an App or a group.
_SCANS = ("NodeIndexScan", "NodeByLabelScan", "UnionNodeByLabelsScan")
_RECORDS = re.compile(r"\b\w+:(?:Record|RecordGroup)\b")


def _calls(w: W) -> dict[str, Any]:
    svc, g, user = KnowledgeHubService(log, w.g), w.g, f"uid-{w.user}"
    kids = sorted(w.n(f"c{i}") for i in range(BIG))

    def browse(parent: str, ptype: str, **kw: Any) -> Any:  # noqa: ANN401
        return svc.get_nodes(user_id=user, org_id=w.org, parent_id=parent, parent_type=ptype, sort_by="name", sort_order="asc",
                             **{"flattened": False, "depth": 1, "limit": 50, **kw})
    return {
        "browse a folder": lambda: browse(w.n("BIG"), "folder"),
        "a later page": lambda: browse(w.n("BIG"), "folder", page=3),
        "browse the App": lambda: browse(w.app, "app"),
        "browse a group": lambda: browse(w.n("G3"), "recordGroup"),
        "flatten the App": lambda: browse(w.app, "app", flattened=True, depth=3, limit=100),
        "records by id": lambda: g.get_records_by_record_ids(kids, w.org),
        "selection": lambda: g.get_selection_nodes(w.org, group_ids=[w.n("G3")], record_ids=[w.n("BIG")], exact_record_ids=kids[:500],
                                                   limit=100),
        "node access": lambda: g.get_knowledge_hub_node_access(w.n("BIG"), w.user, w.org, FOLDER_MIME_TYPES),
        "stamp nodes": lambda: Neo4jStampGraph(lambda: g.client).nodes(w.org, frozenset(kids)),
        "stamp subtree": lambda: Neo4jStampGraph(lambda: g.client).subtree_vrids(w.org, [w.n("G3")]),
        "uploads under a folder": lambda: g.get_uploaded_document_ids(w.app, under_record_ids=[w.n("BIG")]),
        "purge failure": lambda: g.record_purge_failure(kids, w.org, "x"),
    }


@pytest.mark.parametrize("call", [
    "browse a folder", "a later page", "browse the App", "browse a group", "flatten the App", "records by id", "selection", "node access",
    "stamp nodes", "stamp subtree", "uploads under a folder", "purge failure"])
async def test_a_statement_seeks_nodes_by_id_on_the_unique_id_index(w, monkeypatch, call: str) -> None:
    """Every statement these reads run seeks a record or group it has the id of on the label's unique id index, never on an index that
    holds the id beside the org or App, and scans no index or label of records or groups. The planner picks such a seek only when the
    statement tests the org or App beside the match by id, and on other statistics it then seeks the org or App index alone and tests
    the id after (`LISTED`): every node of the org or App read once per id. Read from each statement's plan (EXPLAIN), so nothing is
    written."""
    ran: dict[str, tuple[str, dict[str, Any]]] = {}
    execute = w.g.client.execute_query

    async def capture(query: str, parameters: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        ran.setdefault(query, (query, parameters or {}))
        return await execute(query, parameters, *args, **kwargs)

    monkeypatch.setattr(w.g.client, "execute_query", capture)
    await _calls(w)[call]()
    monkeypatch.setattr(w.g.client, "execute_query", execute)
    assert ran
    beside: list[str] = []
    async with w.g.client.driver.session(database="neo4j") as s:
        for query, parameters in ran.values():
            if query.lstrip().upper().startswith(("CALL DBMS", "SHOW")):
                continue
            prefix, body = re.match(r"\s*((?:CYPHER [^\n]*?replan=\w+\s+)?)(.*)", query, re.S).groups()
            plan = (await (await s.run(f"{prefix}EXPLAIN {body}", parameters)).consume()).plan
            beside += [f"{' '.join(body.split())[:120]} :: {o['operatorType']} {o['args'].get('Details', '')[:120]}" for o in _plan_ops(plan)
                       if (o["operatorType"].startswith("NodeIndexSeek") and _SEEK_BESIDE_ID.search((o.get("args") or {}).get("Details", "")))
                       or (o["operatorType"].startswith(_SCANS) and _RECORDS.search((o.get("args") or {}).get("Details", "")))]
    assert not beside, beside


async def test_relations_batch_is_bounded_per_record_and_direction(w) -> None:
    full = await w.g.get_node_relations_batch([w.n("P")], ["ATTACHMENT"])
    assert sum(len(v) for v in full[w.n("P")].values()) == ATT
    out = await w.g.get_node_relations_batch([w.n("P"), w.n("a0")], ["ATTACHMENT"], limit_per_direction=150)
    assert len(out[w.n("P")]["parents"]) == 150  # outgoing edges are the anchor's "parents"
    assert all(len(v) <= 150 for r in out.values() for v in r.values())
