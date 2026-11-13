"""The browse of one node on PostgreSQL: the children by a hierarchy edge that the viewer may open, their order, total and arrows, the bound on how many it holds, the plans of its statements and the
organisation it reads."""
from __future__ import annotations

import asyncio
import json
import os
import re

import pytest

from app.exceptions.graph_db_exceptions import KnowledgeHubListingTooLarge
from app.services.graph_db.postgres import kh_browse, kh_listing
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import net

from .pg_support import new_org
from .test_kh_listing_flat import (
    _admin,
    _nodes,
    _open_to_all,
    ctx,
    edge,
    flip,
    prov,
    seed_folder,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def flat_arrows(p, org: str, granted: list[str]) -> dict[str, bool]:
    """The arrows of All Records for the connector: each group's and folder's is on exactly when a browse of it lists something."""
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=granted, limit=50)
    return {r["id"]: r["hasChildren"] for r in page["rows"]}


async def browse(p, org: str, start: str, granted: list[str], *, start_type: str = "folder", **kw) -> dict:
    with ctx(org):
        return await p.get_knowledge_hub_connector_page_v3(
            app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=granted, limit=kw.pop("limit", 50), flatten=False, start_id=start, start_type=start_type,
            include_scope=True, **kw)


KIDS = [f"c{i:04d}" for i in range(8)]


async def test_a_browse_lists_the_children_the_viewer_may_open_with_their_total_and_arrows(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 8, readable_kids=KIDS[1:6])
    page = await browse(p, org, "fold", ["fold", *KIDS[1:6]])
    assert [r["id"] for r in page["rows"]] == KIDS[1:6] and page["total"] == 5 and page["counts"] == {"record": 5}
    assert page["scope"]["admitted"] is True and not page["hasMore"] and all(r["hasChildren"] is False for r in page["rows"])


async def test_a_browse_pages_by_keyset_in_every_sort_and_both_directions(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 8, readable_kids=KIDS)
    for field in ("name", "updatedAt", "createdAt", "sizeInBytes"):
        for order in ("ASC", "DESC"):
            seen, after = [], None
            for _ in range(10):
                page = await browse(p, org, "fold", ["fold", *KIDS], limit=3, sort_field=field, sort_dir=order, after=after)
                seen += [r["id"] for r in page["rows"]]
                if not page["hasMore"]:
                    break
                last = page["rows"][-1]
                after = {"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]}
            assert sorted(seen) == KIDS and len(seen) == len(set(seen)), (field, order)
            tail = page["rows"][-1]
            back = await browse(p, org, "fold", ["fold", *KIDS], limit=3, sort_field=field, sort_dir=order, after={"nullRank": tail["nullRank"], "sortKey": tail["sortKey"], "id": tail["id"]}, direction="prev")
            assert [r["id"] for r in back["rows"]] == seen[-4:-1], (field, order)      # the three rows just before the boundary, in page order


async def test_a_browse_holds_at_most_its_bound_of_children(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 6, readable_kids=KIDS[:6])
    monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 6)
    assert len((await browse(p, org, "fold", ["fold", *KIDS[:6]]))["rows"]) == 6          # at the bound
    monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 5)
    with pytest.raises(KnowledgeHubListingTooLarge):                                       # one past it, in a sort the index does not serve
        await browse(p, org, "fold", ["fold", *KIDS[:6]], sort_field="sizeInBytes")
    assert len((await browse(p, org, "fold", ["fold", *KIDS[:6]]))["rows"]) == 6          # one past it, by name: read in index order


async def test_a_node_the_viewer_may_not_open_or_that_is_another_organisations_is_not_browsed(pg_db) -> None:
    p, mine, theirs = prov(pg_db), new_org(), new_org()
    await seed_folder(p, pg_db, mine, 3, readable_kids=[])
    await seed_folder(p, pg_db, theirs, 3, readable_kids=["c0001"])
    closed = await browse(p, mine, "c0001", ["fold"], start_type="record")
    assert closed["rows"] == [] and closed["scope"]["admitted"] is False
    with ctx(mine):
        other = await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=theirs, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=["fold", "c0001"], flatten=False,
                                                            start_id="fold", start_type="folder", include_scope=True)
    assert other["rows"] == [] and other["scope"]["admitted"] is False
    with ctx(mine):      # the session's organisation holds no such node: nothing of the other's is read
        assert (await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=mine, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=["fold"], flatten=False, start_id="fold",
                                                            start_type="folder", include_scope=True))["scope"]["admitted"] is True
    only_theirs = new_org()
    with ctx(only_theirs):
        assert (await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=only_theirs, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=["fold"], flatten=False, start_id="fold",
                                                            start_type="folder", include_scope=True))["scope"]["admitted"] is False


async def test_the_same_children_are_listed_when_kg_does_not_serve_the_connector(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 8, readable_kids=KIDS[2:7])
    served = await browse(p, org, "fold", ["fold", *KIDS[2:7]])
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    walked = await browse(p, org, "fold", ["fold", *KIDS[2:7]])
    assert [r["id"] for r in served["rows"]] == [r["id"] for r in walked["rows"]] == KIDS[2:7] and served["total"] == walked["total"] == 5


async def test_a_placeholder_is_listed_only_on_the_way_to_something_the_viewer_may_read(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        recs = [{"_key": "fold", "recordName": "Folder", "mimeType": "application/vnd.folder"}, {"_key": "stubA", "recordName": "A", "isPlaceholder": True}, {"_key": "leaf", "recordName": "Leaf"},
                {"_key": "stubB", "recordName": "B", "isPlaceholder": True}, {"_key": "nope", "recordName": "Nope"}]
        await p.batch_upsert_nodes([{**r, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for r in recs], "records")
        await p.batch_create_edges([edge("fold", "records", "stubA", "records", relationshipType="PARENT_CHILD"), edge("stubA", "records", "leaf", "records", relationshipType="PARENT_CHILD"),
                                    edge("fold", "records", "stubB", "records", relationshipType="PARENT_CHILD"), edge("stubB", "records", "nope", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge(r["_key"], "records", "g1", "recordGroups") for r in recs], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", i, "records", type="USER", role="READER") for i in ("fold", "stubA", "stubB", "leaf")], "permission")
    await flip(pg_db, org)
    page = await browse(p, org, "fold", ["fold", "stubA", "stubB", "leaf"])
    rows = {r["id"]: r for r in page["rows"]}
    assert set(rows) == {"stubA"} and rows["stubA"]["isPlaceholder"] is True and rows["stubA"]["hasChildren"] is True      # stubB leads to a node the viewer may not read: not listed
    assert [r["id"] for r in (await browse(p, org, "stubA", ["fold", "stubA", "stubB", "leaf"], start_type="record"))["rows"]] == ["leaf"]
    assert (await flat_arrows(p, org, ["fold", "stubA", "stubB", "leaf"]))["fold"] is True      # in All Records too: the folder's only listed child is the stub


async def test_a_group_that_hides_its_children_lists_none(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=KIDS[:3])
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "hid", "groupName": "Hider", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL", "hideChildren": True}], "recordGroups")
        await p.batch_create_edges([edge("hid", "recordGroups", "c0000", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await flip(pg_db, org)
    page = await browse(p, org, "hid", ["fold", *KIDS[:3]], start_type="recordGroup")
    assert page["rows"] == [] and page["total"] == 0


async def test_a_node_wider_than_the_bound_is_read_in_index_order_for_the_name_and_updated_sorts_and_refused_for_the_others(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    kids = [f"c{i:04d}" for i in range(12)]
    await seed_folder(p, pg_db, org, 12, readable_kids=kids)
    monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 5)
    for field in ("name", "updatedAt"):
        for order in ("ASC", "DESC"):
            seen, after = [], None
            for _ in range(10):
                page = await browse(p, org, "fold", ["fold", *kids], limit=3, sort_field=field, sort_dir=order, after=after)
                seen += [r["id"] for r in page["rows"]]
                if not page["hasMore"]:
                    break
                last = page["rows"][-1]
                after = {"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]}
            assert sorted(seen) == kids and len(seen) == len(set(seen)), (field, order)
    assert page["total"] == 12      # counted over the first children of the node, all of them here
    with pytest.raises(KnowledgeHubListingTooLarge):
        await browse(p, org, "fold", ["fold", *kids], sort_field="sizeInBytes")


async def test_a_wide_node_counts_its_children_exactly_when_kg_serves_the_connector_and_says_at_least_otherwise(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    kids = [f"c{i:04d}" for i in range(12)]
    await seed_folder(p, pg_db, org, 12, readable_kids=kids[:9])
    monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 5)
    monkeypatch.setattr(kh_browse, "COUNT_SCAN", 4)
    served = await browse(p, org, "fold", ["fold", *kids[:9]], limit=3)
    assert served["total"] == 9 and served["counts"] == {"record": 9} and "totalCapped" not in served
    monkeypatch.setattr(kh_browse, "UNDECIDED_MAX", -1)      # more undecided children than the count hands to the check: counted as when kg does not serve the connector
    assert (await browse(p, org, "fold", ["fold", *kids[:9]], limit=3))["totalCapped"] is True
    monkeypatch.setattr(kh_browse, "UNDECIDED_MAX", 200)
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    walked = await browse(p, org, "fold", ["fold", *kids[:9]], limit=3)
    assert walked["total"] <= 4 and walked["totalCapped"] is True      # the listed ones among the first COUNT_SCAN children (in no order): a lower bound
    assert [r["id"] for r in served["rows"]] == [r["id"] for r in walked["rows"]] == kids[:3]


async def _group_with_a_folder(p, db, org: str) -> None:
    """A group ``top`` holding a folder ``f`` (granted) whose child ``k`` inherits from it, and a group ``side`` under ``top`` that ``k`` also belongs to (a second group: a link, no hierarchy edge)."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": g, "groupName": g.upper(), "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"} for g in ("top", "side")], "recordGroups")
        await p.batch_upsert_nodes([{"_key": i, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "recordName": i.upper(), **extra}
                                    for i, extra in (("f", {"mimeType": "application/vnd.folder"}), ("k", {}))], "records")
        await p.batch_create_edges([edge("top", "recordGroups", "f", "records", relationshipType="PARENT_CHILD"), edge("top", "recordGroups", "side", "recordGroups", relationshipType="PARENT_CHILD"),
                                    edge("f", "records", "k", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("k", "records", "f", "records")], "inheritPermissions")
        await p.batch_create_edges([edge("f", "records", "top", "recordGroups"), edge("k", "records", "top", "recordGroups"), edge("k", "records", "side", "recordGroups")], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "top", "recordGroups", type="USER", role="READER"), edge("u1", "users", "side", "recordGroups", type="USER", role="READER"),
                                    edge("u1", "users", "f", "records", type="USER", role="READER"), edge("u1", "users", "k", "records", type="USER", role="READER")], "permission")
    await flip(db, org)


async def test_an_arrow_counts_hierarchy_children_only_and_a_child_whose_class_kg_cannot_vouch_for_is_asked_of_the_check(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await _group_with_a_folder(p, pg_db, org)
    granted = ["top", "side", "f", "k"]
    rows = {r["id"]: r["hasChildren"] for r in (await browse(p, org, "top", granted, start_type="recordGroup"))["rows"]}
    assert rows == {"f": True, "side": False}      # k belongs to side but hangs under f: opening side lists nothing
    assert {i: a for i, a in (await flat_arrows(p, org, granted)).items() if i in ("f", "side")} == {"f": True, "side": False}
    assert (await browse(p, org, "side", granted, start_type="recordGroup"))["rows"] == []
    await _admin("UPDATE kg.records SET leaf = NULL WHERE org_id = $1 AND coll = 3 AND id = 'f'", org)      # k's class names f: kg can no longer vouch for it
    rows = {r["id"]: r["hasChildren"] for r in (await browse(p, org, "top", granted, start_type="recordGroup"))["rows"]}
    assert rows["f"] is True and [r["id"] for r in (await browse(p, org, "f", granted, start_type="record"))["rows"]] == ["k"]


async def test_a_browse_reads_the_viewers_grants_in_the_connector_with_those_of_the_source_accounts_they_authenticated_as(pg_db) -> None:
    """Asked with the viewer's key and no grants, the browse reads the viewer's grants in the node's connector itself: a grant held by a source account the viewer authenticated as
    inside that connector counts, as in ``get_knowledge_hub_access_v3``."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=["c0001"])
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "src", "orgId": org, "email": "src@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "src", "users", connectorId="app1", orgId=org)], "authenticatedAs")
        await p.batch_create_edges([edge("src", "users", "c0002", "records", type="USER", role="READER")], "permission")
    await flip(pg_db, org)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=None, user_key="u1", flatten=False,
                                                            start_id="fold", start_type="folder", include_scope=True)
        assert [r["id"] for r in page["rows"]] == ["c0001", "c0002"]
        assert "c0002" in (await p.get_knowledge_hub_access_v3("u1", org))["by_connector"]["app1"]      # the same grant the whole-access read gives


async def browse_app(p, org: str, app: str, granted: list[str], gated: list[str] | None = None, **kw) -> dict:
    with ctx(org):
        return await p.get_knowledge_hub_connector_page_v3(app_id=app, org_id=org, grantee_ids=["u1"], gated_app_ids=gated or [app], granted_ids=granted, limit=kw.pop("limit", 50),
                                                            flatten=False, start_id=app, start_type="app", include_scope=True, **kw)


async def test_a_browse_of_the_connector_lists_the_nodes_that_hang_from_it_the_viewer_may_open(pg_db, monkeypatch) -> None:
    """The App is the hierarchy parent of its top nodes in kg too, so it is browsed by its own key. A node with no hierarchy parent at all, or whose parent is another connector's
    node, does not hang from the App and is not listed there."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=["c0001"])
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k.upper(), "connectorId": c, "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}
                                    for k, c in (("top2", "app2"), ("under", "app2"), ("loose", "app1"))], "records")
        await p.batch_create_edges([edge("app1", "apps", "fold", "records", relationshipType="PARENT_CHILD"), edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD"),
                                    edge("app2", "apps", "top2", "records", relationshipType="PARENT_CHILD"), edge("fold", "records", "under", "records", relationshipType="PARENT_CHILD")],
                                   "nodeRelations")
        await p.batch_create_edges([edge("u1", "users", "app2", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, "records", type="USER", role="READER") for k in ("top2", "under", "loose")], "permission")
    await flip(pg_db, org)
    granted = ["fold", "c0001", "loose"]
    page = await browse_app(p, org, "app1", granted)
    assert [(r["id"], r["hasChildren"], r["parentId"]) for r in page["rows"]] == [("fold", True, "app1")] and page["total"] == 1      # g1: not open to the viewer; loose: hangs from nothing
    assert page["scope"]["admitted"] is True and page["scope"]["currentNode"]["id"] == "app1"
    assert [r["id"] for r in (await browse_app(p, org, "app2", ["top2", "under"]))["rows"]] == ["top2"]      # under hangs from app1's folder
    monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 1)      # more children than a browse holds: read by the children index in the name order
    assert [r["id"] for r in (await browse_app(p, org, "app1", granted))["rows"]] == ["fold"]
    with pytest.raises(KnowledgeHubListingTooLarge):
        await browse_app(p, org, "app1", granted, sort_field="sizeInBytes")
    with ctx(org):      # a connector the viewer is not gated to, and one of another organisation, are not browsed
        assert (await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app2"], granted_ids=granted, flatten=False,
                                                            start_id="app1", start_type="app", include_scope=True))["scope"]["admitted"] is False
    with ctx(new_org()):
        assert (await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=granted, flatten=False,
                                                            start_id="app1", start_type="app", include_scope=True))["rows"] == []



@pytest.mark.parametrize("wide", [False, True], ids=["held", "wide"])
async def test_a_browse_of_a_connector_this_process_distrusts_is_the_walks(pg_db, monkeypatch, wide) -> None:
    """Once the walk has disagreed with kg about a connector, a browse of it is the walk's (as ``read.decide``), whatever the mode still says: the children, the total and the arrows,
    for a node held in memory and one read in windows (``wide``)."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=[])
    with ctx(org):
        await p.batch_create_edges([edge("app1", "apps", "fold", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await flip(pg_db, org)
    await _open_to_all(org, "c0001")
    if wide:
        monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 1)
    page = await browse(p, org, "fold", ["fold"])
    assert [r["id"] for r in page["rows"]] == ["c0001"] and page["total"] == 1                                       # trusted: kg's answer, which differs from the graph
    assert [(r["id"], r["hasChildren"]) for r in (await browse_app(p, org, "app1", ["fold"]))["rows"]] == [("fold", True)]
    monkeypatch.setattr(net, "DISTRUSTED", {(org, "app1"): None})
    page = await browse(p, org, "fold", ["fold"])
    assert page["rows"] == [] and page["total"] == 0 and not page.get("totalCapped")
    assert [(r["id"], r["hasChildren"]) for r in (await browse_app(p, org, "app1", ["fold"]))["rows"]] == [("fold", False)]

async def test_a_filter_never_tells_a_node_the_viewer_may_not_open_from_one_that_does_not_exist(pg_db) -> None:
    """The connector and origin filters apply after the start is admitted: a node the viewer may not open is the same not-found as a missing one, whatever the filters."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=[])
    for start in ("c0001", "no-such-id"):
        for flt in (None, {"connector_ids": ["nope"]}, {"origins": ["COLLECTION"]}):
            assert (await browse(p, org, start, ["fold"], start_type="record", filters=flt))["scope"]["admitted"] is False, (start, flt)
    assert (await browse(p, org, "fold", ["fold"], filters={"connector_ids": ["nope"]}))["scope"]["admitted"] is True      # one they may open: admitted, and empty


async def test_the_breadcrumbs_name_no_connector_the_viewer_is_not_gated_to(pg_db) -> None:
    """A record of app2 under a folder of app1, for a viewer gated to app2 only: app1 is never named, the trail ends at app2."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 1, readable_kids=[])
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "under", "recordName": "UNDER", "connectorId": "app2", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([edge("app1", "apps", "fold", "records", relationshipType="PARENT_CHILD"), edge("fold", "records", "under", "records", relationshipType="PARENT_CHILD")],
                                   "nodeRelations")
        await p.batch_create_edges([edge("u1", "users", "app2", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "under", "records", type="USER", role="READER")], "permission")
    await flip(pg_db, org)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["app2"], granted_ids=["under"], flatten=False,
                                                            start_id="under", start_type="record", include_scope=True)
    assert [c["id"] for c in page["scope"]["breadcrumbs"]] == ["app2", "under"], page["scope"]


async def test_a_placeholder_that_leads_through_a_placeholder_listed_elsewhere_is_listed(pg_db) -> None:
    """fold -> S1 (stub) -> P (stub) -> L, and P also hangs from fold: S1 leads to L through P, so S1 is listed under fold exactly as opening it lists P."""
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        recs = [{"_key": "fold", "recordName": "Folder", "mimeType": "application/vnd.folder"}, {"_key": "S1", "recordName": "S1", "isPlaceholder": True},
                {"_key": "P", "recordName": "P", "isPlaceholder": True}, {"_key": "L", "recordName": "Leaf"}]
        await p.batch_upsert_nodes([{**r, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for r in recs], "records")
        await p.batch_create_edges([edge("fold", "records", "S1", "records", relationshipType="PARENT_CHILD"), edge("fold", "records", "P", "records", relationshipType="PARENT_CHILD"),
                                    edge("S1", "records", "P", "records", relationshipType="PARENT_CHILD"), edge("P", "records", "L", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge(r["_key"], "records", "g1", "recordGroups") for r in recs], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", i, "records", type="USER", role="READER") for i in ("fold", "S1", "P", "L")], "permission")
    await flip(pg_db, org)
    granted = ["fold", "S1", "P", "L"]
    under_fold = {r["id"]: r["hasChildren"] for r in (await browse(p, org, "fold", granted))["rows"]}
    assert under_fold == {"S1": True, "P": True}
    assert [r["id"] for r in (await browse(p, org, "S1", granted, start_type="record"))["rows"]] == ["P"]


async def test_a_node_of_a_connector_kg_has_not_copied_is_not_ready_and_one_not_gated_is_not_found(pg_db) -> None:
    from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady

    from .test_kh_listing_flat import seed
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 3, promote=False)
    for start, typ in (("g1", "recordGroup"), ("r0000", "record")):
        with pytest.raises(KnowledgeHubListingNotReady):
            await browse(p, org, start, ["r0000"], start_type=typ)
        with ctx(org):      # not gated to its connector, or no such node: not found
            assert (await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["other"], granted_ids=["r0000"], flatten=False,
                                                                start_id=start, start_type=typ, include_scope=True))["scope"]["admitted"] is False
    assert (await browse(p, org, "no-such-id", ["r0000"], start_type="record"))["scope"]["admitted"] is False


async def test_children_the_classes_do_not_decide_are_asked_of_the_check_a_window_at_a_time(pg_db, monkeypatch) -> None:
    """Kg not serving the connector (or too many children it does not decide): a page asks the live check about the window it lists, not every child the node holds; the total is
    the readable ones among the first COUNT_SCAN, a lower bound."""
    from app.services.graph_db.postgres import access
    p, org = prov(pg_db), new_org()
    kids = [f"c{i:04d}" for i in range(300)]
    await seed_folder(p, pg_db, org, 300, readable_kids=kids)
    served = await browse(p, org, "fold", ["fold", *kids], limit=3)
    asked: list[str] = []
    real = access.accessible_rows

    async def counted(tx, org_id, ids, vrids, acc) -> list:
        asked.extend(ids)
        return await real(tx, org_id, ids, vrids, acc)
    monkeypatch.setattr(access, "accessible_rows", counted)
    monkeypatch.setattr(kh_browse, "COUNT_SCAN", 10)
    monkeypatch.setattr(kh_browse, "UNDECIDED_MAX", -1)      # served, but more undecided children than a read asks about at once
    mid = await browse(p, org, "fold", ["fold", *kids], limit=3)
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    asked.clear()
    walked = await browse(p, org, "fold", ["fold", *kids], limit=3)
    assert [r["id"] for r in served["rows"]] == [r["id"] for r in mid["rows"]] == [r["id"] for r in walked["rows"]] == kids[:3]
    assert len(set(asked)) <= 120, len(set(asked))      # the window (100) and the counted ones (10), not the 300 children
    assert walked["total"] <= 10 and walked["totalCapped"] is True and mid["totalCapped"] is True


async def test_a_node_reads_its_linked_children_once_and_refuses_more_than_it_reads(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 4, readable_kids=KIDS[:4])
    with ctx(org):
        await p.batch_create_edges([edge("u1", "users", "g1", "recordGroups", type="USER", role="READER")], "permission")
    await flip(pg_db, org)
    # the folder's children also hang under g1 by a hierarchy link
    await _admin("INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT r.org_id, g.sk, 1, r.sk FROM kg.records r JOIN kg.node_keys g ON g.org_id = r.org_id AND g.coll = 2 "
                 "AND g.id = 'g1' WHERE r.org_id = $1 AND r.id = ANY($2::text[])", org, KIDS[:3])
    granted = ["fold", "g1", *KIDS[:4]]
    first = await browse(p, org, "g1", granted, start_type="recordGroup")
    assert [r["id"] for r in first["rows"]] == KIDS[:3], first["scope"]
    monkeypatch.setattr(kh_browse.kgl, "LINKS_MAX", 2)
    with pytest.raises(KnowledgeHubListingTooLarge):
        await browse(p, org, "g1", granted, start_type="recordGroup")
    assert (await browse(p, org, "g1", ["fold"], start_type="recordGroup"))["scope"]["admitted"] is False      # not open to the viewer: the 404, not the 422


async def test_a_node_counts_only_live_linked_children_against_its_bound_and_lists_one_behind_deleted_links(pg_db, monkeypatch) -> None:
    """The bound on a node's linked children counts the ones that are live and not hidden: links to deleted rows before a readable child neither refuse the browse nor hide the
    child, and the child's parent shows its arrow (exact when kg serves the connector, the sample when it does not)."""
    from .test_kh_listing_flat import _folder_with_a_linked_child_after
    p, org = prov(pg_db), new_org()
    await _folder_with_a_linked_child_after(p, pg_db, org, 300)
    monkeypatch.setattr(kh_browse.kgl, "LINKS_MAX", 250)
    for served in (True, False):
        if not served:
            await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
        assert [r["id"] for r in (await browse(p, org, "fold", ["fold", "far"]))["rows"]] == ["far"], served
        assert (await flat_arrows(p, org, ["fold", "far"]))["fold"] is True, served


def _generic_plan(sql: str) -> object:
    """``EXPLAIN (GENERIC_PLAN)`` of a statement with its ``$n`` unbound: sent as a simple query (a client-side cursor), which the extended protocol of asyncpg cannot."""
    import psycopg
    with psycopg.connect(os.environ["PG_ADMIN_URL"], autocommit=True) as c, psycopg.ClientCursor(c) as cur:
        cur.execute("SET enable_seqscan = off")
        cur.execute("EXPLAIN (GENERIC_PLAN, FORMAT JSON) " + sql.replace("%", "%%"))
        plan = cur.fetchone()[0]
    return json.loads(plan) if isinstance(plan, str) else plan


# the statements checked for the plan of their values only (the page's snapshot plans each for its values): the windows of a wide node and its total
CUSTOM_ONLY = ("wide ", "children ", "undecided ", "count ")


# an index condition that names the node (or nodes) the statement asks about: a parent, a key, a child, an id (not merely the org, a kind or a class)
NODE_COND = re.compile(r"\b(parent_sk|sk|child_sk|id|app_sk|group_sk)\b\)?(::text)?\s*=")


@pytest.mark.parametrize("analyzed", [False, True], ids=["before_analyze", "after_analyze"])
async def test_every_browse_statement_reads_by_an_index_and_never_scans_the_organisation(pg_db, analyzed) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 400, readable_kids=[])
    for _ in range(2):
        await seed_folder(p, pg_db, new_org(), 50, readable_kids=[])
    # every child also hangs under a second parent (a hierarchy link), so a read of the links that is not by the parent walks all of them
    await _admin("INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT r.org_id, g.sk, 1, r.sk FROM kg.records r JOIN kg.node_keys g ON g.org_id = r.org_id AND g.coll = 2 AND g.id = 'g1' "
                 "WHERE r.org_id = $1 AND r.parent_sk IS NOT NULL", org)
    # and most of the organisation's rows hang elsewhere (another node's children, some with no name or date), so a read of the organisation's range costs what it would
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, parent_sk, name_sort, updated_ms) SELECT $1, 60000000 + g, 3, 'other' || g, 3, a.sk, h.sk, "
                 "CASE WHEN g % 7 = 0 THEN NULL ELSE convert_to('other' || g, 'UTF8') END, CASE WHEN g % 5 = 0 THEN NULL ELSE g END FROM generate_series(1, 4000) g "
                 "JOIN kg.node_keys a ON a.org_id = $1 AND a.coll = 1 AND a.id = 'app1' JOIN kg.node_keys h ON h.org_id = $1 AND h.coll = 2 AND h.id = 'hid'", org)
    from asyncpg import connect
    con = await connect(os.environ["PG_ADMIN_URL"])
    try:
        if analyzed:
            await con.execute("ANALYZE kg.records; ANALYZE kg.links; ANALYZE kg.node_keys")
        sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 3 AND id = 'fold'", org)
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        g1 = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 2 AND id = 'g1'", org)
        hide = [await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 3 AND id = 'c0001'", org)]
        statements = []
        # a node's children, the App's, and a node whose children are all linked; each with and without the filters and the hidden rows a request carries
        for start, key in (("node", sk), ("app", app_sk), ("linked", g1)):
            linked = [r["child_sk"] for r in await con.fetch(kgl.LINKED_CHILDREN, org, key, kgl.LINKS_MAX + 1, [])]
            for shaped, (flt, hid) in (("plain", ({}, [])), ("filtered", ({"search_query": "child", "updated_at": {"gte": 0}}, hide))):
                def params(window: int, flt: dict = flt, hid: list = hid, key: int = key, linked: list = linked) -> tuple[kgl.Params, str, str | None]:
                    base, where = kh_listing.where_of(org, app_sk, {}, flt, hid)
                    base.values[1], base.values[2] = key, window
                    return base, where, (base.add(linked) if linked else None)
                at = f"{start} {shaped}"
                for field in ("name", "updatedAt"):
                    for order in ("ASC", "DESC"):
                        for direction in ("next", "prev"):
                            for boundary in (None, {"nullRank": 0, "sortKey": "child 0100" if field == "name" else 1500, "id": "c0100"}):
                                pp, where, links = params(51)
                                branches = kgl.after(field, order, direction, boundary, pp) if boundary else None
                                statements.append((f"wide {at} {field} {order} {direction} {'after' if boundary else 'first'}",
                                                   kgl.page_sql(field, order, direction, where, branches, None, kgl.browse_scopes(links)), pp.values))
                ch, where, links = params(5001)
                held, _, _ = params(5001)
                und, _, und_links = params(201)
                cnt, _, cnt_links = params(10_001)
                statements += [(f"children {at}", kgl.children_sql(where, links), ch.values), (f"children_held {at}", kgl.children_held_sql(where, links), held.values),
                               (f"undecided {at}", kgl.browse_undecided_sql(where, und.add([3]), und.add(app_sk), und_links), und.values),
                               (f"count {at}", kgl.browse_count_sql(where, cnt.add([1]), cnt.add([2]), cnt.add(["x"]), cnt.add([3]), cnt_links), cnt.values)]
        q = kgl.Params([org, [sk, g1], hide])
        admit = kgl.class_admit("c", q, {"plain": [1], "own": [2], "unvouched": [], "granted": ["x"], "walked": set()})
        arrows = kgl.arrows_sql(admit, kgl.undecided_child(q, [3], app_sk))      # as the listing asks it
        statements += [("children_of", kgl.children_of_sql(""), [org, [sk, g1], 200, hide]), ("children_of_bounded", kgl.children_of_sql(total=True), [org, [sk, g1], 1000, hide, 5000]),
                       ("held", kgl.HELD, [org, [g1], ["c0001", "fold"]]), ("chain_granted", kgl.CHAIN_GRANTED, [org, ["c0001", "fold"], "app1"]),
                       ("chain_rows", kgl.CHAIN_ROWS, [org, ["c0001", "fold"]]), ("chain_by_sk", kgl.CHAIN_BY_SK, [org, [sk, g1]]), ("app_key", kgl.APP_KEY, [org, "app1"]),
                       ("arrows", arrows, q.values), ("linked_children", kgl.LINKED_CHILDREN, [org, g1, 5001, hide]),
                       ("start", kgl.BROWSE_START, [org, "fold"]), ("start_connector", kh_browse.START_CONNECTOR, [org, "fold"]), ("up", kgl.BROWSE_UP, [org, [sk]]),
                       ("state", kgl.STATE, [org, "app1"]), ("hiders", kgl.HAS_HIDER, [org, app_sk]), ("hidden", kgl.HIDDEN, [org, app_sk]), ("classes", kgl.CLASSES, [org, app_sk, 50_001]),
                       ("links", kh_listing.LINKS, [org, [sk]]), ("keys", kh_listing.KEYS, [org, [sk]]), ("docs", kh_listing.DOCS, [org, ["records"], ["fold"]]),
                       ("files", kh_listing.FILES, [org, ["fold"]])]
        for name, sql, args in statements:
            await con.execute("SET enable_seqscan = off")
            # the plan for these values, and for the statements of a held node also the generic plan (a margin: a plan that does not depend on the values asked)
            plans = [("custom", json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args)))]
            if not name.startswith(CUSTOM_ONLY):
                plans.append(("generic", await asyncio.to_thread(_generic_plan, sql)))
            for kind, plan in plans:
                at = f"{name} {kind} ({'after' if analyzed else 'before'} ANALYZE)"
                seq = [n["Relation Name"] for n in _nodes(plan) if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") in ("records", "links", "node_keys")]
                assert not seq, f"{at}: sequential scan of {seq}"
                # once analyzed, every read of the org's rows and links is bounded by the nodes it asks about, never a walk of the org's index (with no statistics the planner may
                # still pick a wider range of a small table: the plan without a scan above is what holds then)
                loose = [n["Index Name"] for n in _nodes(plan) if analyzed and str(n.get("Index Name")).startswith(("records_", "links_")) and not NODE_COND.search(n.get("Index Cond") or "")]
                assert not loose, f"{at}: reads {loose} without the node in its index condition: {[n.get('Index Cond') for n in _nodes(plan) if n.get('Index Name') in loose]}"
                if name.startswith("wide"):      # the parent's children are read by an index on the parent (the children index, in its order, once the node is wide: the latency run's plans)
                    used = {n.get("Index Name") for n in _nodes(plan)}
                    assert any(str(u).startswith(("records_children_", "records_parent")) for u in used), f"{at}: {used}"
    finally:
        await con.close()


@pytest.mark.parametrize("mode", ["held", "by_window", "not_served", "wide"])
async def test_concurrent_browses_never_wait_for_a_second_pooled_connection(pg_db, monkeypatch, mode) -> None:
    """A browse reads everything on its page's one snapshot connection: the start, the children, the access checks, the arrows and the total (``by_window``: the children asked of
    the check a window at a time; ``not_served``: the walk; ``wide``: a node read in windows of the children index). With a pool of two and four browses at once, a browse that
    held one connection and waited for another would wait as long as the pool lets it: every connection would be held by a browse waiting the same way."""
    import dataclasses

    from app.core.db import DbConfig, KernelDB
    p0, org = prov(pg_db), new_org()
    await seed_folder(p0, pg_db, org, 8, readable_kids=KIDS[1:6])
    if mode == "not_served":
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    if mode == "by_window":
        monkeypatch.setattr(kh_listing, "CLASS_MAX", 0)
    if mode == "wide":
        monkeypatch.setattr(kh_browse, "BROWSE_MEMORY_MAX", 5)
    small = KernelDB(dataclasses.replace(DbConfig.from_env("connectors"), pool_max=2))
    await small.start()
    try:
        p = prov(small)
        pages = await asyncio.wait_for(asyncio.gather(*(browse(p, org, "fold", ["fold", *KIDS[1:6]]) for _ in range(4))), 30)
    finally:
        await small.close()
    for page in pages:
        assert [r["id"] for r in page["rows"]] == KIDS[1:6] and page["total"] == 5 and page["scope"]["admitted"] is True


async def test_a_browse_in_a_callers_transaction_asks_the_check_and_never_admits_by_class(pg_db, monkeypatch) -> None:
    """A caller's own transaction (read committed) is not one snapshot, so the classes it would read may be older than the rows: the browse asks the access check about every
    row, as the flat listing's page does, and lists the same children."""
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 8, readable_kids=KIDS[1:6])
    calls = []
    real = kh_listing.class_sets

    async def spy(*a, **kw) -> object:
        calls.append(True)
        return await real(*a, **kw)
    monkeypatch.setattr(kh_listing, "class_sets", spy)
    with ctx(org):
        tid = await p.begin_transaction([], [])
        try:
            page = await p.get_knowledge_hub_connector_page_v3(app_id="", org_id=org, grantee_ids=["u1"], gated_app_ids=["app1"], granted_ids=["fold", *KIDS[1:6]], limit=50,
                                                               flatten=False, start_id="fold", start_type="folder", include_scope=True, transaction=tid)
        finally:
            await p.rollback_transaction(tid)
    assert not calls and [r["id"] for r in page["rows"]] == KIDS[1:6] and page["total"] == 5
    assert [r["id"] for r in (await browse(p, org, "fold", ["fold", *KIDS[1:6]]))["rows"]] == KIDS[1:6] and calls      # in the page's own snapshot the classes admit


async def test_the_placeholder_walk_reads_at_most_its_budget_of_rows_however_wide_its_nodes_and_pages_the_same(pg_db, monkeypatch) -> None:
    """Twenty granted placeholders of 60 children each, the first of each granted: one level of the walk below them would read 1,200 rows. Each walk reads at most LEAD_NODES
    rows in all (the level is cut in SQL at what is left of the budget), a placeholder it did not get to is not listed (never a leak), and the browse lists the same rows
    whether read in one page or paged by cursor, each once."""
    p, org = prov(pg_db), new_org()
    stubs = [f"s{i:02d}" for i in range(20)]
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        recs = [{"_key": "fold", "recordName": "Folder", "mimeType": "application/vnd.folder"}, *({"_key": s, "recordName": f"Stub {s}", "isPlaceholder": True} for s in stubs),
                *({"_key": f"{s}k{j:02d}", "recordName": f"Kid {s} {j:02d}"} for s in stubs for j in range(60))]
        await p.batch_upsert_nodes([{**r, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for r in recs], "records")
        await p.batch_create_edges([*(edge("fold", "records", s, "records", relationshipType="PARENT_CHILD") for s in stubs),
                                    *(edge(s, "records", f"{s}k{j:02d}", "records", relationshipType="PARENT_CHILD") for s in stubs for j in range(60))], "nodeRelations")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        granted = ["fold", *stubs, *(f"{s}k00" for s in stubs)]
        await p.batch_create_edges([edge("u1", "users", i, "records", type="USER", role="READER") for i in granted], "permission")
    await flip(pg_db, org)
    monkeypatch.setattr(kh_listing, "LEAD_NODES", 100)
    walks: list[int] = []
    real_leads, real_kq = kh_listing.KhListingMixin._kh_leads, kh_listing.KhListingMixin._kq

    async def leads(self, *a, **kw) -> set[int]:
        walks.append(0)
        return await real_leads(self, *a, **kw)

    async def kq(self, fn, transaction) -> object:
        got = await real_kq(self, fn, transaction)
        if walks and isinstance(got, list) and got and "parent" in got[0].keys():      # a level of the placeholder walk
            walks[-1] += len(got)
        return got
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_leads", leads)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kq", kq)
    whole = [r["id"] for r in (await browse(p, org, "fold", granted, limit=50))["rows"]]
    assert walks and max(walks) <= 100, walks
    assert whole and set(whole) < set(stubs), whole      # the walk reached some placeholders and stopped at its budget for the rest
    seen, after = [], None
    for _ in range(20):
        page = await browse(p, org, "fold", granted, limit=3, after=after)
        seen += [r["id"] for r in page["rows"]]
        if not page["hasMore"]:
            break
        last = page["rows"][-1]
        after = {"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]}
    assert seen == whole and max(walks) <= 100, (seen, whole, walks)
