"""The knowledge hub of a connector the typed graph has copied but not verified or promoted yet (a new collection, a connector whose sync keeps changes arriving): browse,
All Records and the search list what the live walk admits, the same rows, totals and arrows as once kg serves the connector, never a refusal. A connector that holds no node
lists nothing before any copy. One that holds nodes and whose copy is unfinished, or whose copy differs from the graph, is refused as not ready."""
from __future__ import annotations

import logging
import os

import asyncpg
import pytest

from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from tests.permissions.kh_layer import _Config

from .pg_support import new_org
from .test_kh_listing_flat import _admin, ctx, edge, flip, prov

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

KIDS = [f"c{i}" for i in range(6)]
READ = ["fold", "c0", "c1", "c2", "c4", "r9"]      # u1's grants: not g1, so fold and r9 are chain-tops; c4 belongs to the group that hides its children


async def world(p, org: str) -> None:
    """app1 > g1 > fold > c0..c5; hid hides its children (c4, c5); r9 belongs to g1 with no hierarchy parent."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"},
                                    {"_key": "hid", "groupName": "Hider", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL", "hideChildren": True}],
                                   "recordGroups")
        await p.batch_upsert_nodes([{"_key": "fold", "recordName": "Folder", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "mimeType": "application/vnd.folder"},
                                    {"_key": "r9", "recordName": "Loose", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"},
                                    *({"_key": k, "recordName": f"Child {k}", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k in KIDS)], "records")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD"), edge("g1", "recordGroups", "fold", "records", relationshipType="PARENT_CHILD"),
                                    *(edge("fold", "records", k, "records", relationshipType="PARENT_CHILD") for k in KIDS)], "nodeRelations")
        await p.batch_create_edges([edge("g1", "recordGroups", "app1", "apps"), edge("hid", "recordGroups", "app1", "apps"), edge("r9", "records", "g1", "recordGroups"),
                                    *(edge(k, "records", "hid" if k in ("c4", "c5") else "g1", "recordGroups") for k in KIDS)], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com", "userId": "u1"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, "records", type="USER", role="READER") for k in READ], "permission")


async def copy(db, org: str, **kw) -> None:
    """What the bootstrap does first for a new connector: drain the log, copy the org's principals and the connector. No stamps, no verify, no promotion."""
    await drain_org(db, org)
    await bf.backfill_scope(db, org, None)
    await bf.backfill_scope(db, org, "app1", **kw)


def _page(page: dict) -> tuple:
    scope = page.get("scope") or {}
    return ([(r["id"], r["hasChildren"]) for r in page["rows"]], page["total"], page["counts"], page["hasMore"], bool(page.get("totalCapped")),
            scope.get("admitted"), sorted(n["id"] for n in scope.get("nodes") or []))


async def answers(p, org: str) -> dict[str, tuple]:
    """u1's All Records of app1, the browse of the App, of g1 and of fold, and the search over every connector."""
    ask = {"org_id": org, "grantee_ids": ["u1"], "gated_app_ids": ["app1"], "granted_ids": READ, "limit": 50}
    out: dict[str, tuple] = {}
    with ctx(org):
        out["flat"] = _page(await p.get_knowledge_hub_connector_page_v3(app_id="app1", **ask, include_scope=True))
        for start, typ in (("app1", "app"), ("g1", "recordGroup"), ("fold", "folder")):
            out[start] = _page(await p.get_knowledge_hub_connector_page_v3(app_id="app1" if typ == "app" else "", **ask, flatten=False, start_id=start, start_type=typ, include_scope=True))
        found = await search_page(p, user_key="u1", user_id="u1", org_id=org, limit=50, secret=None)
        out["search"] = (sorted(r["id"] for r in found.rows), found.total, found.not_ready)
    return out


async def test_a_copied_connector_lists_before_it_is_verified_what_it_lists_once_promoted(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org)
    before = await answers(p, org)      # on main: KnowledgeHubListingNotReady (the service's 503) until the promotion
    assert sorted(i for i, _ in before["flat"][0]) == ["c0", "c1", "c2", "fold", "r9"]      # c4 is readable and hidden by its group; c3, c5 and g1 are not readable
    assert before["fold"][0] == [("c0", False), ("c1", False), ("c2", False)] and before["search"][2] == []
    assert (await flip(pg_db, org)) == (1, 1)
    assert await answers(p, org) == before


async def test_a_change_after_the_copy_is_listed_once_drained_and_access_stays_the_walks(pg_db) -> None:
    """Changes keep arriving (the bootstrap would wait RETRY_S before its verify): a record added and granted after the copy lists once the drain projects it, a grant taken away
    unlists its record at once (the walk answers access, kg has no say yet)."""
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "c9", "recordName": "Child c9", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([edge("fold", "records", "c9", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("u1", "users", "c9", "records", type="USER", role="READER")], "permission")
    await drain_org(pg_db, org)
    await _admin("DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND to_id = 'c1'", org)      # not drained: the walk reads the graph
    granted = [*(g for g in READ if g != "c1"), "c9"]      # the grants as the request reads them now
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=granted, limit=50, flatten=False,
                                                           start_id="fold", start_type="folder")
    assert [r["id"] for r in page["rows"]] == ["c0", "c2", "c9"] and page["total"] == 3


async def test_a_connector_that_holds_nothing_lists_nothing_before_any_copy(pg_db) -> None:
    """A collection opened the moment it is created: before kg has even seen it, its pages are the empty ones its copy gives."""
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Notes", "type": "KB", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
    ask = {"org_id": org, "grantee_ids": ["u1"], "gated_app_ids": ["app1"], "granted_ids": [], "limit": 50, "include_scope": True}

    async def pages() -> tuple:
        with ctx(org):
            flat = await p.get_knowledge_hub_connector_page_v3(app_id="app1", **ask)
            at_app = await p.get_knowledge_hub_connector_page_v3(app_id="app1", **ask, flatten=False, start_id="app1", start_type="app")
            found = await search_page(p, user_key="u1", user_id="u1", org_id=org, limit=50, secret=None)
        return _page(flat), _page(at_app), (found.rows, found.total, found.not_ready)
    before = await pages()
    assert before[1][:2] == ([], 0) and before[1][5] is True and before[2] == ([], 0, [])
    assert (await flip(pg_db, org)) == (1, 1)
    assert await pages() == before


async def test_a_connector_whose_copy_is_unfinished_is_refused_until_the_copy_ends(pg_db) -> None:
    """shortcut (kh_listing): an unfinished copy has no structure to list from; a refusal, never an empty page that would hide readable rows."""
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org, page=2, max_batches=1)
    with ctx(org), pytest.raises(KnowledgeHubListingNotReady):
        await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=READ, limit=50)
    await copy(pg_db, org, page=2)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=READ, limit=50)
    assert sorted(r["id"] for r in page["rows"]) == ["c0", "c1", "c2", "fold", "r9"]


async def test_the_knowledge_hub_answers_a_source_still_being_copied_as_preparing_never_a_503(pg_db) -> None:
    """The listing a person or an agent asks for (``get_nodes``): while the copy is unfinished, browsing the App or a folder they may open and All Records answer an empty
    page marked ``preparing``, with nothing to page on to; a node they may not open stays the not-found one. Once the copy ends, the rows."""
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org, page=2, max_batches=1)
    service = KnowledgeHubService(logging.getLogger("kh-preparing"), p, _Config())

    async def nodes(**kw: object):  # noqa: ANN202
        with ctx(org):
            return await service.get_nodes(user_id="u1", org_id=org, limit=50, sort_by="name", sort_order="asc", include=["counts"], **kw)
    for kw in ({"parent_id": "app1", "parent_type": "app"}, {"parent_id": "fold", "parent_type": "folder"}, {"flattened": True}):
        page = await nodes(**kw)
        assert (page.success, page.errorCode, page.preparing, page.items) == (True, None, True, []), kw      # on main: success False, errorCode 503
        assert (page.pagination.totalItems, page.pagination.hasNext, page.pagination.nextCursor, page.counts.total) == (0, False, None, 0), kw
    hidden = await nodes(parent_id="c3", parent_type="record")
    assert (hidden.success, hidden.errorCode, hidden.preparing) == (False, 404, None)
    await copy(pg_db, org, page=2)
    done = await nodes(parent_id="fold", parent_type="folder")
    assert done.preparing is None and [i.id for i in done.items] == ["c0", "c1", "c2"]


async def test_a_copy_whose_verify_finds_a_difference_is_not_listed_from(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org)
    await _admin("DELETE FROM kg.links WHERE org_id = $1", org)      # kg no longer holds what the graph does, and no change in the log explains it
    out = await bf.backfill(pg_db, org, "app1")
    assert not out.flipped and out.reason == "differs"
    state = await _state(org)
    assert state == (0, "legacy", "done"), state      # the copy keeps its cursor and loses the epoch the listing reads it by
    with ctx(org), pytest.raises(KnowledgeHubListingNotReady):
        await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=READ, limit=50)


async def _state(org: str) -> tuple:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        r = await con.fetchrow("SELECT s.code_epoch, s.mode, s.backfill_cursor FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1 "
                               "WHERE s.org_id = $1 AND k.id = 'app1'", org)
    finally:
        await con.close()
    return tuple(r)


async def test_a_finished_copy_carries_the_code_epoch(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, org)
    await copy(pg_db, org)
    assert await _state(org) == (CODE_EPOCH, "legacy", "done")
