"""The flat listing of a connector from the typed graph: the plans of its statements, what it refuses, what it says when it stops early, and that it reads only the session's organisation."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets

import asyncpg
import pytest

from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres import permissions as permissions_module
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import net
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from tests.support import plan_gate

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_read import _admin, flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


def prov(pg_db) -> PostgresGraphProvider:
    return PostgresGraphProvider(logging.getLogger("kh-flat"), pg_db)


async def seed(p, db, org: str, n: int, *, readable: int | None = None, promote: bool = True, hide: bool = False) -> None:
    """One connector of ``n`` records in a group; the viewer ``u1`` is granted ``readable`` of them (all by default)."""
    readable = n if readable is None else readable
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL", "hideChildren": hide}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": f"r{i:04d}", "recordName": f"R{i:04d}", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR",
                                     "sourceLastModifiedTimestamp": 1000 + i} for i in range(n)], "records")
        await p.batch_create_edges([edge(f"r{i:04d}", "records", "g1", "recordGroups") for i in range(n)], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", f"r{i:04d}", "records", type="USER", role="READER") for i in range(readable)], "permission")
    if promote:
        await flip(db, org)


def asks(org: str, **kw) -> dict:
    return {"app_id": "app1", "org_id": org, "grantee_ids": ["u1"], "gated_app_ids": ["app1"], "granted_ids": kw.pop("granted_ids", None) or [f"r{i:04d}" for i in range(kw.pop("granted", 0))], **kw}


async def walk(p, org: str, limit: int, **kw) -> tuple[list[str], list[int], dict]:
    ids, sizes, after, first = [], [], None, None
    for _ in range(500):
        with ctx(org):
            page = await p.get_knowledge_hub_connector_page_v3(**asks(org, **kw), limit=limit, after=after, include_total=first is None)
        first = first or page
        ids += [r["id"] for r in page["rows"]]
        sizes.append(len(page["rows"]))
        if not page["hasMore"]:
            return ids, sizes, first
        after = page.get("resume") or {k: page["rows"][-1][k2] for k, k2 in (("nullRank", "nullRank"), ("sortKey", "sortKey"), ("id", "id"))}
    raise AssertionError("did not end")


async def test_a_connector_listed_while_kg_does_not_serve_it_lists_the_same_rows(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=7)
    served, _, first = await walk(p, org, 4, granted=7)
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    walked, _, again = await walk(p, org, 4, granted=7)
    assert served == walked == [f"r{i:04d}" for i in range(7)] and first["total"] == again["total"] == 7


@pytest.mark.parametrize("hidden", [False, True], ids=["plain", "walked"])
async def test_a_first_page_that_reads_its_whole_listing_counts_its_total_from_it_without_a_second_read(pg_db, monkeypatch, hidden) -> None:
    """300 records, 15 readable. A search that matches ten names reads its whole listing in the page's first window: the total (5, of the readable ones) and the counts are the
    rows the window admitted, and the connector is not read again to count them (a rare word was read whole twice, once to find its rows and once to count them). A listing that
    does not end in the page's windows (no search), and a page after a boundary, still count by the count. ``walked``: a matching readable record no class decides (a restamp hides
    it) is in the total as the access check admits it, in the window as in the count."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 300, readable=15)
    if hidden:
        await _admin("UPDATE kg.records SET fence_job = 1 WHERE org_id = $1 AND id = 'r0012' AND coll = 3", org)
    counted: list[bool] = []
    real = kh_listing.KhListingMixin._kh_flat_exact

    async def spy(self, *a, **kw) -> object:
        counted.append(True)
        return await real(self, *a, **kw)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_flat_exact", spy)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=15), limit=50, filters={"search_query": "r001"})
    matched = [f"r{i:04d}" for i in range(10, 15)]
    assert [r["id"] for r in page["rows"]] == matched and page["total"] == 5 and page["counts"] == {"record": 5} and not page.get("totalCapped")
    assert not counted
    with ctx(org):
        whole = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=15), limit=3)
    assert whole["total"] == 15 and len(counted) == 1
    after = {k: whole["rows"][-1][k] for k in ("nullRank", "sortKey", "id")}
    with ctx(org):
        later = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=15), limit=50, after=after, filters={"search_query": "r001"})
    assert [r["id"] for r in later["rows"]] == matched and later["total"] == 5 and len(counted) == 2


async def test_a_connector_with_no_copy_is_refused(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 3, promote=False)
    with ctx(org):
        with pytest.raises(KnowledgeHubListingNotReady):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5)
        with pytest.raises(KnowledgeHubListingNotReady):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, flatten=False)      # a browse of the connector itself
        with pytest.raises(KnowledgeHubListingNotReady):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, max_depth=3)
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, start_id="g1", start_type="recordGroup")      # a start kg does not hold: not opened, as by a browse
    assert page["rows"] == [] and page["total"] == 0


async def _tree(p, db, org: str) -> None:
    """app1 > g1 (group) > f1 (folder) > f2 (folder) > r3; f1 > r2; g1 > r1; g1 > dx (a deleted folder) > r5; r9 belongs to g1 with no hierarchy parent; u1 may read everything
    but r2 (and dx, which is deleted)."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FILE" if k[0] == "r" else "FOLDER", "origin": "CONNECTOR"}
                                    for k in ("f1", "f2", "r1", "r2", "r3", "r5", "r9")]
                                   + [{"_key": "dx", "recordName": "dx", "connectorId": "app1", "orgId": org, "recordType": "FOLDER", "origin": "CONNECTOR", "isDeleted": True}], "records")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD")]
                                   + [edge(a, ac, b, "records", relationshipType="PARENT_CHILD") for a, ac, b in (("g1", "recordGroups", "f1"), ("g1", "recordGroups", "r1"),
                                      ("f1", "records", "f2"), ("f1", "records", "r2"), ("f2", "records", "r3"), ("g1", "recordGroups", "dx"), ("dx", "records", "r5"))], "nodeRelations")
        await p.batch_create_edges([edge("r9", "records", "g1", "recordGroups"), edge("g1", "recordGroups", "app1", "apps")], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, kc, type="USER", role="READER") for k, kc in (("g1", "recordGroups"), ("f1", "records"), ("f2", "records"),
                                                                                                        ("r1", "records"), ("r3", "records"), ("r5", "records"), ("r9", "records"))], "permission")
    await flip(db, org)


@pytest.mark.parametrize("served", [True, False], ids=["kg", "walk"])
async def test_a_flatten_below_a_node_and_a_depth_bound_list_what_browsing_down_reaches(pg_db, served: bool) -> None:
    p, org = prov(pg_db), new_org()
    await _tree(p, pg_db, org)
    if not served:
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    every = ["g1", "f1", "f2", "r1", "r3", "r5", "r9"]

    async def ids(**kw) -> list[str] | None:
        with ctx(org):
            page = await p.get_knowledge_hub_connector_page_v3(**{**asks(org, granted_ids=every), **kw}, limit=50, include_scope=True)
        return [r["id"] for r in page["rows"]] if page["scope"] is None or page["scope"].get("admitted", True) else None
    assert await ids(app_id="", start_id="g1", start_type="recordGroup") == ["f1", "f2", "r1", "r3"]        # r2 is not readable; r9 is no hierarchy child
    assert await ids(app_id="", start_id="g1", start_type="recordGroup", max_depth=1) == ["f1", "r1"]
    assert await ids(app_id="", start_id="f1", start_type="record", max_depth=2) == ["f2", "r3"]
    assert await ids(app_id="", start_id="r2", start_type="record") is None                                # a start the viewer may not open
    assert await ids(max_depth=1) == ["g1"]
    assert await ids(max_depth=2) == ["f1", "g1", "r1", "r9"]                                              # r9 is one below its group
    assert await ids(max_depth=3) == ["f1", "f2", "g1", "r1", "r5", "r9"]                                  # r5 is three hops down, through the deleted dx (as on Neo4j)
    assert await ids() == ["f1", "f2", "g1", "r1", "r3", "r5", "r9"]


async def test_a_depth_bound_counts_a_child_met_twice_once_against_its_bound(pg_db, monkeypatch) -> None:
    """app1 > g1 > a, b; c is a child of a and of b: the third level reads c twice. Five rows fit a bound of five (the read cut at the room left is read again whole), not of four."""
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FOLDER", "origin": "CONNECTOR"} for k in ("a", "b", "c")], "records")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD")]
                                   + [edge(x, xc, y, "records", relationshipType="PARENT_CHILD") for x, xc, y in (("g1", "recordGroups", "a"), ("g1", "recordGroups", "b"),
                                                                                                                  ("a", "records", "c"), ("b", "records", "c"))], "nodeRelations")
        await p.batch_create_edges([edge("g1", "recordGroups", "app1", "apps")], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, kc, type="USER", role="READER") for k, kc in (("g1", "recordGroups"), ("a", "records"), ("b", "records"),
                                                                                                        ("c", "records"))], "permission")
    await flip(pg_db, org)
    every = ["g1", "a", "b", "c"]
    monkeypatch.setattr(kh_listing, "SCOPE_MAX", 5)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=every), limit=50, max_depth=3)
    assert [r["id"] for r in page["rows"]] == ["a", "b", "c", "g1"]
    monkeypatch.setattr(kh_listing, "SCOPE_MAX", 4)
    with ctx(org), pytest.raises(KnowledgeHubListingTooLarge):
        await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=every), limit=50, max_depth=3)


async def test_another_organisations_connector_is_never_read(pg_db) -> None:
    p, mine, theirs = prov(pg_db), new_org(), new_org()
    await seed(p, pg_db, mine, 5)
    await seed(p, pg_db, theirs, 5)
    with ctx(mine):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(theirs), limit=10)       # asks for an org that is not the session's
    assert page["rows"] == [] and not page["hasMore"]
    other = new_org()
    with ctx(other):      # the session's org holds no such connector and nothing of it: nothing of the other's is found, an empty page
        page = await p.get_knowledge_hub_connector_page_v3(**asks(other), limit=10)
    assert page["rows"] == [] and page["total"] == 0 and not page["hasMore"]


async def test_a_connector_kg_serves_has_an_exact_total_beyond_the_scan_and_one_it_does_not_has_a_lower_bound(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=9)
    monkeypatch.setattr(kh_listing, "COUNT_SCAN", 5)
    with ctx(org):
        served = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=9), limit=3)
    assert served["total"] == 9 and not served.get("totalCapped") and served["counts"] == {"record": 9}      # counted from the classes, not by the scan
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    with ctx(org):
        walked = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=9), limit=3)
    assert walked["totalCapped"] is True and 0 < walked["total"] <= 9


async def test_the_exact_total_stops_at_its_cap_and_says_so(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=9)
    monkeypatch.setattr(kh_listing, "COUNT_CAP", 4)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=9), limit=3)
    assert page["total"] == 4 and page["totalCapped"] is True and page["counts"] == {"record": 4}      # the counts by type add up to the total


async def test_a_page_that_stops_early_says_where_and_the_walk_from_there_lists_every_readable_row_once(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 30, readable=30)
    await _admin("DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND from_id = 'u1' AND to_id < 'r0027'", org)      # only the last three are readable
    monkeypatch.setattr(kh_listing, "MAX_SCANNED", 4)
    monkeypatch.setattr(kh_listing, "WINDOW_MAX", 4)
    ids, sizes, _ = await walk(p, org, 2, granted_ids=["r0027", "r0028", "r0029"])
    assert ids == ["r0027", "r0028", "r0029"] and 0 in sizes


async def test_what_a_group_that_hides_its_children_holds_is_not_listed_and_too_much_of_it_is_a_view_too_wide(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 6, hide=True)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=6), limit=10)
    assert [r["id"] for r in page["rows"]] == [] and page["total"] == 0      # the group is listed only if readable; its records are hidden
    monkeypatch.setattr(kh_listing, "HIDDEN_MAX", 3)
    with ctx(org), pytest.raises(KnowledgeHubListingTooLarge):
        await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=6), limit=10)


async def seed_folder(p, db, org: str, kids: int, *, readable_kids: list[str], hide: bool = False) -> None:
    """A readable folder ``fold`` with ``kids`` children (by the hierarchy); the viewer ``u1`` may read the folder and ``readable_kids`` only. With ``hide`` the children belong to a group that hides its children."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"},
                                    {"_key": "hid", "groupName": "Hider", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL", "hideChildren": True}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": "fold", "recordName": "Folder", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "mimeType": "application/vnd.folder"},
                                    *({"_key": f"c{i:04d}", "recordName": f"Child {i:04d}", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for i in range(kids))], "records")
        await p.batch_create_edges([edge("fold", "records", f"c{i:04d}", "records", relationshipType="PARENT_CHILD") for i in range(kids)], "nodeRelations")
        await p.batch_create_edges([edge("fold", "records", "g1", "recordGroups"), *(edge(f"c{i:04d}", "records", "hid" if hide else "g1", "recordGroups") for i in range(kids))], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", i, "records", type="USER", role="READER") for i in ["fold", *readable_kids]], "permission")
    await flip(db, org)


async def folder_arrow(p, org: str, granted: list[str]) -> bool:
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=granted), limit=50)
    return next(r for r in page["rows"] if r["id"] == "fold")["hasChildren"]


@pytest.mark.parametrize("served", [True, False], ids=["served", "not_served"])
async def test_a_folder_whose_only_readable_child_is_the_twenty_first_shows_its_arrow(pg_db, served) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 25, readable_kids=["c0024"])
    if not served:
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    assert await folder_arrow(p, org, ["fold", "c0024"]) is True


@pytest.mark.parametrize("served", [True, False], ids=["served", "not_served"])
async def test_a_folder_whose_only_readable_child_is_hidden_by_a_group_that_hides_its_children_shows_no_arrow(pg_db, served) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=["c0001"], hide=True)
    if not served:
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    assert await folder_arrow(p, org, ["fold", "c0001"]) is False


async def test_a_connector_copied_by_an_older_code_is_not_listed_from(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12)
    await _admin("UPDATE kg.projection_state SET code_epoch = code_epoch - 1 WHERE org_id = $1 AND app_sk <> 0", org)
    with ctx(org), pytest.raises(KnowledgeHubListingNotReady):
        await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=12), limit=4)


async def test_a_walk_over_rows_with_no_name_sort_ends_and_lists_each_row_once(pg_db) -> None:
    """Whatever left a sort column empty (a copy that is being rewritten), a row with no sort key sorts last and pages by its id: the keyset can never return a row at or before its own boundary."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12)
    await _admin("UPDATE kg.records SET name_sort = NULL WHERE org_id = $1", org)
    ids, _, _ = await walk(p, org, 4, granted=12)
    assert sorted(ids) == [f"r{i:04d}" for i in range(12)] and len(ids) == len(set(ids))


async def _plans(db, org: str, app_sk: int, hidden: bool) -> list[tuple[str, dict]]:
    out = []
    for field in kgl.SORTS:
        for sort_dir in ("ASC", "DESC"):
            for direction in ("next", "prev"):
                for boundary in (None, {"nullRank": 0, "sortKey": "r0100" if field == "name" else 1500, "id": "r0100"}):
                    base = kgl.Params([org, app_sk, 51])
                    where = kgl.filters_sql({}, base)
                    p = kgl.Params(list(base.values))
                    branches = kgl.after(field, sort_dir, direction, boundary, p) if boundary else None
                    out.append((f"{field} {sort_dir} {direction} {'after' if boundary else 'first'}", {"sql": kgl.page_sql(field, sort_dir, direction, where, branches), "args": p.values}))
    return out


def _nodes(plan: object) -> list[dict]:
    found = []
    stack = [plan]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            found.append(x)
            stack += list(x.values())
        elif isinstance(x, list):
            stack += x
    return found


@pytest.mark.parametrize("analyzed", [False, True], ids=["before_analyze", "after_analyze"])
async def test_every_listing_statement_reads_the_connector_by_an_index_and_never_scans_the_organisation(pg_db, analyzed) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 400, promote=True)
    for _ in range(3):      # rows of other connectors and organisations the plan must not walk
        await seed(p, pg_db, new_org(), 50, promote=False)
    import os

    from asyncpg import connect
    con = await connect(os.environ["PG_ADMIN_URL"])
    try:
        if analyzed:
            await con.execute("ANALYZE kg.records; ANALYZE kg.links; ANALYZE kg.node_keys")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        statements = [(n, q["sql"], q["args"]) for n, q in await _plans(pg_db, org, app_sk, False)]
        adm = kgl.Params([org, app_sk, 201])
        cond = f"w.r13 IS FALSE AND w.class_id = ANY({adm.add([1])}::bigint[]) AND w.id = ANY({adm.add(['x'])}::text[])"
        statements += [(f"admitted {s} {d}", kgl.page_sql("name", s, d, "", None, cond), adm.values) for s in ("ASC", "DESC") for d in ("next", "prev")]
        sc = kgl.Params([org, app_sk, 51])      # a scoped page (a depth bound, a start below the connector): the classes admit in the read
        inside = f" AND r.sk = ANY({sc.add([app_sk + 1, app_sk + 2])}::bigint[]) AND " + kgl.class_admit("r", sc, {"plain": [1], "own": [2], "unvouched": [], "granted": ["x"], "walked": set()}, raw=True)
        statements += [(f"scoped admitted {s} {d}", kgl.page_sql("name", s, d, inside, None, "true"), sc.values) for s in ("ASC", "DESC") for d in ("next", "prev")]
        arrows = kgl.Params([org, [app_sk], []])
        statements += [("arrows", kgl.arrows_sql(kgl.class_admit("c", arrows, {"plain": [1], "own": [2], "unvouched": [], "granted": ["x"], "walked": set()})), arrows.values)]
        statements += [("scan", kgl.scan_sql("", "$4"), [org, app_sk, 501, 0]), ("hiders", kgl.HAS_HIDER, [org, app_sk]), ("hidden", kgl.HIDDEN, [org, app_sk]),
                       ("children", kgl.children_of_sql(""), [org, [app_sk], 20, []]), ("links", kh_listing.LINKS, [org, [app_sk]]), ("state", kgl.STATE, [org, "app1"]),
                       ("unplaced", kgl.UNPLACED, [org, [app_sk], "app1", 20]), ("hierarchy children", kgl.HIER_CHILDREN, [org, [app_sk], 20])]
        for name, sql, args in statements:
            await con.execute("SET enable_seqscan = off")      # a statement with an index it can use has a plan without a scan, whatever the size of the tables the planner is guessing at
            plan = json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args))
            seq = [n for n in _nodes(plan) if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") in ("records", "links", "node_keys")]
            assert not seq, f"{name} ({'after' if analyzed else 'before'} ANALYZE): sequential scan of {[n['Relation Name'] for n in seq]}"
            if name.startswith("scoped"):      # a class test inside the read must not turn it into a read of the class across the organisation
                loose = plan_gate.unkeyed(plan, "records", r"org_id = .*\bsk = |org_id = .*app_sk = ")
                assert not loose, f"{name} ({'after' if analyzed else 'before'} ANALYZE): {loose}"
    finally:
        await con.close()


@pytest.mark.parametrize("unvouched", [[], [1]], ids=["every_class_vouched", "a_class_unvouched"])
async def test_the_rows_kg_does_not_decide_are_read_by_their_own_index_never_by_a_range_of_the_connector(pg_db, unvouched) -> None:
    """A page of a connector kg serves asks for the few rows kg does not decide. Read as a range of the connector (records_app) it read every row at 1M records to find none.
    Judged with statistics, as kg's tables are once copied (kg_0016): with statistics older than an organisation's rows the planner may take any index of the org."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 400, promote=True)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute("ANALYZE kg.records")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        await con.execute("SET enable_seqscan = off")      # the planner's guess at a table this small is a scan; the gate is about which index bounds the read
        plan = json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + kgl.undecided_sql("", "$4"), org, app_sk, 201, unvouched))
        used = {name for s in plan_gate.scans(plan, "records") for name, _ in plan_gate.bounds(s)}
        # the classes' read may take any range of the connector when a class holds most of it (as in this world); never the organisation
        loose = plan_gate.unkeyed(plan, "records", r"app_sk")
        assert "records_undecided" in used and not loose, f"undecided rows read by {used}: {loose}"
    finally:
        await con.close()


def _joined_reads(node: dict, relation: str, probe: bool = False) -> list[str]:
    """The reads of ``relation`` in a plan that are not inside a SubPlan (a per-row probe): part of the plan's joins."""
    probe = probe or node.get("Parent Relationship") == "SubPlan"
    own = [f"{node['Node Type']} on {node['Relation Name']}"] if not probe and str(node.get("Relation Name", "")).startswith(relation) else []
    return own + [x for c in node.get("Plans", []) for x in _joined_reads(c, relation, probe)]


@pytest.mark.parametrize("analyzed", [False, True], ids=["before_analyze", "after_analyze"])
@pytest.mark.parametrize("admitted", [False, True], ids=["walked", "by_class"])
async def test_the_indexing_status_filter_probes_each_rows_node_and_never_joins_the_organisations_nodes(pg_db, analyzed, admitted) -> None:
    """A window filtered by indexing status reads each row's node by its key, in the window's order. As a join the plan read every node of the organisation
    (a sequential scan of its partition: 4.2 s at 1M records) to keep one window."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 400, promote=True)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        if analyzed:
            await con.execute("ANALYZE kg.records; ANALYZE graph.nodes")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        base = kgl.Params([org, app_sk, None])
        where = kgl.filters_sql({"record_types": ["FILE"], "indexing_status": ["COMPLETED"]}, base)
        q = kgl.Params(list(base.values))
        q.values[2] = 204
        admit = kgl.class_admit("w", q, {"unvouched": [], "plain": [1], "own": [], "granted": [], "walked": set()}) if admitted else None
        plan = json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + kgl.page_sql("updatedAt", "DESC", "next", where, None, admit), *q.values))
        joined = _joined_reads(plan_gate.root(plan), "nodes")
        assert plan_gate.scans(plan, "nodes") and not joined, f"indexing status ({'after' if analyzed else 'before'} ANALYZE) joins the organisation's nodes: {joined}"
    finally:
        await con.close()


def _rows_read(plan: object, relation: str) -> int:
    """The rows the plan's reads of ``relation`` looked at: what each returned and what its filter removed, over every loop."""
    return sum(int((n.get("Actual Rows", 0) + n.get("Rows Removed by Filter", 0)) * n.get("Actual Loops", 1)) for n in plan_gate.scans(plan, relation))


@pytest.mark.parametrize(("sort_dir", "direction"), [("ASC", "next"), ("ASC", "prev"), ("DESC", "next"), ("DESC", "prev")])
async def test_a_name_ordered_window_past_a_boundary_reads_the_rows_up_to_it_not_the_connector(pg_db, sort_dir, direction) -> None:
    """A page by name past a boundary, read as the listing's reader is, under row-level security: a condition on the name prefix (substring(), not leakproof) is never an
    index condition there. Read as one disjoint read per part of the boundary, each read was a scan of the connector (or of the organisation, the planner's pick at 1M records)
    to find a handful of rows; read as one walk of the name index it looks at the rows up to the boundary and the window."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 600, promote=True)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute("ANALYZE kg.records")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
    finally:
        await con.close()
    at = "r0010" if (sort_dir == "ASC") == (direction == "next") else "r0589"      # ten rows from the start of the walk in the page's direction
    q = kgl.Params([org, app_sk, 20])
    sql = kgl.page_sql("name", sort_dir, direction, "", kgl.after("name", sort_dir, direction, {"nullRank": 0, "sortKey": at, "id": at}, q))

    async def read(tx) -> tuple[list[str], object]:
        # A connector this small is read whole and sorted, to the planner; at scale the window's LIMIT makes the walk of the name index the cheap plan, which these
        # settings leave as the only one. The gate is about how far that walk reads.
        for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            await tx.execute(f"SET LOCAL {setting} = off")
        return [r["id"] for r in await tx.fetch(sql, *q.values)], json.loads(await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *q.values))
    with ctx(org):
        ids, plan = await pg_db.run(read, custom_plan=True)
    n = int(at[1:])
    want = [f"r{i:04d}" for i in (range(n + 1, n + 21) if (sort_dir == "ASC") == (direction == "next") else range(n - 1, n - 21, -1))]
    assert ids == want
    reads = [(n["Node Type"], plan_gate.bounds(n), n.get("Actual Rows"), n.get("Rows Removed by Filter")) for n in plan_gate.scans(plan, "records")]
    assert _rows_read(plan, "records") <= 60, f"{sort_dir} {direction}: looked at {_rows_read(plan, 'records')} rows of a 600-row connector for a window of 20: {reads}"


async def test_the_records_a_viewers_classes_admit_are_read_within_the_connector_never_the_organisation(pg_db) -> None:
    """Search's record-id path reads the records a connector kg serves admits by class, within the connector (with statistics, as kg's tables are once copied:
    kg_0016): never a read of the organisation by records_id or records_pkey."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 400, promote=True)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute("ANALYZE kg.records")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        await con.execute("SET enable_seqscan = off")      # as above: the gate is about which index bounds the read
        plan = json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + permissions_module._KG_ADMITTED, org, app_sk, [], [1, 2], [3], ["r0001"]))
        loose = plan_gate.unkeyed(plan, "records", r"app_sk")
        assert plan_gate.scans(plan, "records") and not loose, f"admitted records read by {loose}"
    finally:
        await con.close()


@pytest.mark.parametrize(("kind", "kept"), [("KB", ["g1"]), ("DRIVE", ["g1", "g2"])])
async def test_the_record_group_filter_narrows_a_collections_groups_only(pg_db, kind: str, kept: list[str]) -> None:
    """As the other stores do: ``record_group_ids`` keeps only the listed record groups of a collection; a connector's groups, and every record, pass."""
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "A", "type": kind, "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": g, "groupName": g.upper(), "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}
                                    for g in ("g1", "g2")], "recordGroups")
        await p.batch_upsert_nodes([{"_key": f"r{g}", "recordName": f"R{g}", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for g in ("g1", "g2")], "records")
        await p.batch_create_edges([edge(f"r{g}", "records", g, "recordGroups") for g in ("g1", "g2")], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", t, c, type="USER", role="READER") for t, c in (("g1", "recordGroups"), ("g2", "recordGroups"), ("rg1", "records"), ("rg2", "records"))], "permission")
    await flip(pg_db, org)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=["g1", "g2", "rg1", "rg2"]), limit=20, filters={"record_group_ids": ["g1"]})
    ids = {r["id"] for r in page["rows"]}
    assert sorted(i for i in ids if i.startswith("g")) == kept and {"rg1", "rg2"} <= ids


# an index condition that names the node (or nodes) the statement asks about: a parent, a key, a group, a child, an id (not merely the organisation, a kind or a class)
NODE_COND = re.compile(r"\b(parent_sk|sk|child_sk|id|app_sk|group_sk)\b\)?(::text)?\s*=")


async def _generic_plan(con, sql: str) -> object:
    """``EXPLAIN (GENERIC_PLAN)`` of a statement with its ``$n`` unbound. The extended protocol would bind the parameters, so the EXPLAIN runs in plpgsql
    (``EXECUTE`` with no ``USING``) and hands its text back through a session setting."""
    assert "$kh_q$" not in sql
    await con.execute(f"DO $kh_do$ DECLARE r text; BEGIN EXECUTE $kh_q$EXPLAIN (GENERIC_PLAN, FORMAT JSON) {sql}$kh_q$ INTO r; PERFORM set_config('kh.plan', r, false); END $kh_do$")
    return json.loads(await con.fetchval("SELECT current_setting('kh.plan')"))


@pytest.mark.parametrize("analyzed", [False, True], ids=["before_analyze", "after_analyze"])
async def test_the_hidden_rows_the_arrows_and_their_sample_read_by_key_and_never_walk_the_organisation(pg_db, analyzed) -> None:
    """On an organisation whose rows mostly lie elsewhere, with groups that hide their children and children that hang by links: the hidden rows, the arrows and the arrows' sample
    read the nodes they ask about by their keys, in the plan for the values and in the generic one, so their cost is the nodes', never the organisation's."""
    import os

    from asyncpg import connect
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 400, readable_kids=[], hide=True)
    for _ in range(2):
        await seed_folder(p, pg_db, new_org(), 50, readable_kids=[])
    await _admin("INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT r.org_id, g.sk, k, r.sk FROM kg.records r JOIN kg.node_keys g ON g.org_id = r.org_id AND g.coll = 2 "
                 "AND g.id = 'g1' CROSS JOIN (VALUES (1), (2)) AS kinds(k) WHERE r.org_id = $1 AND r.parent_sk IS NOT NULL", org)
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, parent_sk, group_sk, class_id) SELECT $1, 60000000 + g, 3, 'other' || g, 3, a.sk, h.sk, h.sk, g % 3 FROM generate_series(1, 4000) g "
                 "JOIN kg.node_keys a ON a.org_id = $1 AND a.coll = 1 AND a.id = 'app1' JOIN kg.node_keys h ON h.org_id = $1 AND h.coll = 2 AND h.id = 'g1'", org)
    con = await connect(os.environ["PG_ADMIN_URL"])
    try:
        if analyzed:
            await con.execute("ANALYZE kg.records; ANALYZE kg.links; ANALYZE kg.node_keys")
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        sks = [r["sk"] for r in await con.fetch("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND id = ANY($2::text[])", org, ["fold", "g1", "hid"])]
        arrows = kgl.Params([org, sks, []])
        statements = [("hiders", kgl.HAS_HIDER, [org, app_sk]), ("hidden", kgl.HIDDEN, [org, app_sk]),
                      ("arrows", kgl.arrows_sql(kgl.class_admit("c", arrows, {"plain": [1], "own": [2], "unvouched": [], "granted": ["x"], "walked": set()})), arrows.values),
                      ("children", kgl.children_of_sql(), [org, sks, 200, []])]
        for name, sql, args in statements:
            await con.execute("SET enable_seqscan = off")
            for kind, plan in (("custom", json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args))), ("generic", await _generic_plan(con, sql))):
                at = f"{name} {kind} ({'after' if analyzed else 'before'} ANALYZE)"
                seq = [n["Relation Name"] for n in _nodes(plan) if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") in ("records", "links", "node_keys")]
                assert not seq, f"{at}: sequential scan of {seq}"
                # once analyzed (with no statistics the planner may take a wider range of a small table: the plan without a scan above is what holds then)
                loose = plan_gate.unkeyed(plan, ("records", "links"), NODE_COND.pattern) if analyzed else []
                assert not loose, f"{at}: reads without the node in its index condition: {loose}"
                if name == "hiders":      # the connector's hiders by their own index, not a pass over the connector's rows
                    assert {n.get("Index Name") for n in _nodes(plan)} & {"records_hiders"}, f"{at}: {plan}"
    finally:
        await con.close()


async def _folder_with_a_linked_child_after(p, db, org: str, dead: int) -> None:
    """A granted folder ``fold`` and a granted record ``far`` beside it (both under ``g1``); ``far`` also hangs under ``fold`` by a hierarchy link, after ``dead`` links of ``fold`` to
    deleted rows (in the order of the links' key)."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k.upper(), "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", **extra}
                                    for k, extra in (("fold", {"mimeType": "application/vnd.folder"}), ("far", {}))], "records")
        await p.batch_create_edges([edge("g1", "recordGroups", k, "records", relationshipType="PARENT_CHILD") for k in ("fold", "far")], "nodeRelations")
        await p.batch_create_edges([edge(k, "records", "g1", "recordGroups") for k in ("fold", "far")], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, "records", type="USER", role="READER") for k in ("fold", "far")], "permission")
    await flip(db, org)
    # the deleted rows come first in the links' key order (negative keys), then far
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, deleted) SELECT $1, -g, 3, 'gone' || g, 3, a.sk, true FROM generate_series(1, $2::int) g "
                 "JOIN kg.node_keys a ON a.org_id = $1 AND a.coll = 1 AND a.id = 'app1'", org, dead)
    await _admin("INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT $1, f.sk, 1, c.sk FROM kg.node_keys f, kg.records c WHERE f.org_id = $1 AND f.coll = 3 AND f.id = 'fold' "
                 "AND c.org_id = $1 AND (c.sk < 0 OR c.id = 'far') AND c.coll = 3", org)


@pytest.mark.parametrize("served", [True, False], ids=["served", "not_served"])
@pytest.mark.parametrize("dead", [200, 5_001])
async def test_a_folder_whose_only_readable_child_is_linked_after_deleted_links_shows_its_arrow(pg_db, served, dead) -> None:
    """An arrow reads a row's linked children up to the first that is live, not hidden and open, however many links come before it: no budget of raw links cuts it off (the exact
    statement of a connector kg serves, and the sample of one it does not)."""
    p, org = prov(pg_db), new_org()
    await _folder_with_a_linked_child_after(p, pg_db, org, dead)
    if not served:
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    assert await folder_arrow(p, org, ["fold", "far"]) is True


async def _open_to_all(org: str, rec: str) -> None:
    """Point ``rec`` at a class that admits every viewer (an empty clause): kg then admits a row the walk denies, as a copy that differs from the graph does."""
    await _admin("INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT $1, 990001, k.sk, NULL, '[[]]'::jsonb, $2 FROM kg.node_keys k "
                 "WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org, secrets.token_bytes(16))
    await _admin("UPDATE kg.records SET class_id = 990001 WHERE org_id = $1 AND id = $2 AND coll = 3", org, rec)


@pytest.mark.parametrize("wide", [False, True], ids=["by_class", "by_window"])
async def test_a_connector_this_process_distrusts_is_listed_and_counted_by_the_walk(pg_db, monkeypatch, wide) -> None:
    """Once the walk has disagreed with kg about a connector, its listing is the walk's (as ``read.decide``), whatever the mode still says: the page, the total and the rows left to the
    access check (``by_window``: too many classes for the statement, so every window is asked)."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=7)
    await _open_to_all(org, "r0010")
    if wide:
        monkeypatch.setattr(kh_listing, "CLASS_MAX", 0)
    readable = [f"r{i:04d}" for i in range(7)]
    ids, _, first = await walk(p, org, 4, granted=7)
    assert ids == [*readable, "r0010"] and first["total"] == 8                # trusted: kg's answer, which differs from the graph
    monkeypatch.setattr(net, "DISTRUSTED", {(org, "app1"): None})
    asked: list[list[str]] = []
    real = kg_read.decide

    async def spy(tx, o, i, a) -> object:
        asked.append(list(i))
        return await real(tx, o, i, a)
    monkeypatch.setattr(kg_read, "decide", spy)
    ids, _, first = await walk(p, org, 4, granted=7)
    assert ids == readable and first["total"] == 7 and not first.get("totalCapped") and first["counts"] == {"record": 7}
    assert not {i for a in asked for i in a if i.startswith("r")}            # the records are asked of the live check directly, not through kg


async def test_a_folder_of_a_connector_this_process_distrusts_shows_the_walks_arrow(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await seed_folder(p, pg_db, org, 3, readable_kids=[])
    await _open_to_all(org, "c0001")
    assert await folder_arrow(p, org, ["fold"]) is True                      # trusted: kg's class opens c0001
    monkeypatch.setattr(net, "DISTRUSTED", {(org, "app1"): None})
    assert await folder_arrow(p, org, ["fold"]) is False


async def _seed_named(p, db, org: str, names: dict[str, str]) -> None:
    """One connector whose records are named as ``names`` says (id -> name), every one granted to ``u1``."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": k, "recordName": n, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k, n in names.items()], "records")
        await p.batch_create_edges([edge(k, "records", "g1", "recordGroups") for k in names], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", k, "records", type="USER", role="READER") for k in names], "permission")
    await flip(db, org)


@pytest.mark.parametrize("sort_dir", ["ASC", "DESC"])
async def test_names_longer_than_the_indexed_prefix_page_in_the_order_of_the_whole_name_both_ways(pg_db, sort_dir) -> None:
    """The name indexes key on a prefix of the name: names that share it, end inside it, or split a surrogate pair at its edge still page, forward and back, in the order of the whole
    name and then the id. Names of thousands of characters are copied whole and listed among names that share their prefix: 5,000 characters outside the BMP (20,000 bytes of key that do
    not compress), its first 4,000 and 4,001 (the bound the copy once had), and names of multi-byte characters inside the BMP."""
    import random
    rng = random.Random(5713)
    stem = "x" * (kgl.NAME_PREFIX // 2)      # code units of UTF-16: exactly the indexed prefix
    names: dict[str, str] = {}
    for i in range(40):
        tail = "".join(rng.choice("abé\U0001f600") for _ in range(rng.randint(0, 3)))
        names[f"r{i:04d}"] = rng.choice([stem + tail, stem[:-1] + tail, stem[:-2] + tail, tail or "a"])
    names["r0040"] = names["r0001"]           # the same name: the id decides
    names["r0041"] = "".join(chr(rng.randint(0x20000, 0x2A6DF)) for _ in range(5000))
    names["r0042"] = names["r0041"][:-1] + "a"       # shares the prefix (and all but the last character) with r0041
    names["r0043"] = names["r0041"][:2000]           # shares the prefix and ends before r0041 does
    names["r0044"] = "".join(chr(rng.randint(0x4E00, 0x9FFF)) for _ in range(3000))
    names["r0045"] = names["r0044"][:2500] + "\u00e9"
    names["r0046"] = names["r0041"][:4000]
    names["r0047"] = names["r0041"][:4001]
    p, org = prov(pg_db), new_org()
    await _seed_named(p, pg_db, org, names)

    def key(i: str) -> bytes:
        return names[i].lower().encode("utf-16-be", "surrogatepass")
    want = sorted(sorted(names), key=key, reverse=sort_dir == "DESC")       # a stable sort: equal names stay in id order, as the listing's tie-break
    got, _, first = await walk(p, org, 3, granted_ids=sorted(names), sort_dir=sort_dir)
    assert got == want and first["total"] == len(names)
    back, after = [], {"nullRank": 0, "sortKey": names[want[-1]].lower(), "id": want[-1]}
    for _ in range(100):
        with ctx(org):
            page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=sorted(names)), limit=3, after=after, direction="prev", sort_dir=sort_dir, include_total=False)
        back = [r["id"] for r in page["rows"]] + back
        if not page["hasMore"]:
            break
        after = page.get("resume") or {k: page["rows"][0][k] for k in ("nullRank", "sortKey", "id")}
    assert back == want[:-1]


async def test_a_record_named_longer_than_the_copy_once_took_is_copied_whole_and_its_connector_is_listed(pg_db) -> None:
    """One record whose name is 5,000 characters outside the BMP, beside a record with a short name: the batch is copied (the records writer once refused any name over 4,000
    characters, which failed the batch and left the whole connector not ready), kg holds the name whole, and the listing serves both rows in the order of the whole name."""
    long = "".join(chr(0x20000 + i) for i in range(5000))
    names = {"r0000": long, "r0001": "Short"}
    p, org = prov(pg_db), new_org()
    await _seed_named(p, pg_db, org, names)
    got, _, first = await walk(p, org, 5, granted_ids=sorted(names))
    assert got == ["r0001", "r0000"] and first["total"] == 2 and [r["name"] for r in first["rows"]] == ["Short", long]
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        stored = await con.fetchval("SELECT name FROM kg.records WHERE org_id = $1 AND id = 'r0000' AND coll = 3", org)
    finally:
        await con.close()
    assert stored == long


async def test_a_connectors_classes_are_decided_from_a_row_of_classes_and_a_row_of_literals_as_their_clauses_say(pg_db) -> None:
    """3,000 classes of random clauses over the literals of a tree (a group, folders with children, a record with none, a key kg does not hold, ``@self``): each class is met with
    no grant of the row's own, met with it, or not vouched for, as the clauses say. The classes and the literals kg vouches for reach the listing as one row each, not one row per
    class or literal: a connector has tens of thousands of each, and their decoding held the event loop every page of the knowledge hub shares (about 0.2 s a connector at 1M records)."""
    import random
    p, org = prov(pg_db), new_org()
    await _tree(p, pg_db, org)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        app_sk = await con.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        lit = {r["id"]: r["sk"] for r in await con.fetch("SELECT id, sk FROM kg.records WHERE org_id = $1 AND id = ANY($2::text[])", org, ["g1", "f1", "f2", "r1"])}
        pool = [*lit.values(), 99_999_999, kg_read.SELF]
        rng = random.Random(6051)
        made = [[rng.sample(pool, rng.randint(0, 3)) for _ in range(rng.randint(0, 3))] for _ in range(3000)]
        await con.execute("INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT $1, 980000 + i, $2, NULL, c::jsonb, decode(md5($1 || i), 'hex') "
                          "FROM unnest($3::text[]) WITH ORDINALITY AS q(c, i)", org, app_sk, [json.dumps(m) for m in made])
        classes = {r["id"]: json.loads(r["clauses"]) for r in await con.fetch("SELECT id, clauses::text FROM kg.scope_classes WHERE org_id = $1 AND app_sk = $2", org, app_sk)}
        # what kg vouches for, by the rule of kg_read.LITERALS: a node kg holds that is not flagged a leaf and, for a record, has a child
        seen = {r["sk"] for r in await con.fetch("SELECT r.sk FROM kg.records r WHERE r.org_id = $1 AND r.sk = ANY($2::bigint[]) AND r.leaf IS FALSE AND (r.kind <> 3 OR EXISTS "
                                                    "(SELECT 1 FROM kg.records c WHERE c.org_id = r.org_id AND c.parent_sk = r.sk) OR EXISTS (SELECT 1 FROM kg.links l "
                                                    "WHERE l.org_id = r.org_id AND l.parent_sk = r.sk AND l.kind = 1))", org, pool[:-1])}
    finally:
        await con.close()
    assert seen == {lit["g1"], lit["f1"], lit["f2"]}          # the tree gives one literal of each kind: r1 has no child, the last key is not held
    granted = {"g1", "f2", "r1"}
    live = {lit[i] for i in granted} & seen

    def verdict(cl: list[list]) -> tuple[bool, bool] | None:
        if any(x != kg_read.SELF and x not in seen for clause in cl for x in clause):
            return None
        met = [all(x in live for x in clause if x != kg_read.SELF) for clause in cl]
        return (any(m and kg_read.SELF not in c for m, c in zip(met, cl, strict=True)), any(m and kg_read.SELF in c for m, c in zip(met, cl, strict=True)))
    want = {c: verdict(cl) for c, cl in classes.items()}

    class Counted:
        def __init__(self, tx) -> None:
            self.tx, self.rows = tx, 0

        async def fetch(self, *a) -> list:
            out = await self.tx.fetch(*a)
            self.rows += len(out)
            return out

        async def fetchrow(self, *a) -> object:
            self.rows += 1
            return await self.tx.fetchrow(*a)

        async def fetchval(self, *a) -> object:
            return await self.tx.fetchval(*a)

    async def go(tx) -> tuple[dict, int]:
        c = Counted(tx)
        return await kh_listing.class_sets(c, org, app_sk, granted), c.rows
    with ctx(org):
        sets, rows = await pg_db.run(go)
    assert sets["plain"] == {c for c, v in want.items() if v and v[0]}
    assert sets["own"] == {c for c, v in want.items() if v and v[1] and not v[0]}
    assert sets["unvouched"] == {c for c, v in want.items() if v is None}
    assert sets["plain"] and sets["own"] and sets["unvouched"]
    assert rows <= 2, rows


async def test_a_change_to_the_whole_connector_that_commits_after_the_check_is_not_read_by_the_page(pg_db, monkeypatch) -> None:
    """kg serves the connector when the page checks. Before the page reads its rows, a change to the connector as a whole commits: it hides the connector (an ``app`` job, no row
    marks) and a stamp written under that hiding opens r0010 to everyone. The page reads the snapshot the check read, so the hidden stamp admits nothing; the next page is the walk's."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=7)
    await _admin("INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT $1, 990001, k.sk, NULL, '[[]]'::jsonb, $2 FROM kg.node_keys k "
                 "WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org, secrets.token_bytes(16))
    real, fired = kg_read.class_verdicts, []

    async def change_commits_now(tx, o, found, granted) -> object:
        if not fired:
            fired.append(True)
            await _admin("INSERT INTO kg.restamp_queue (org_id, id, app_sk, kind, reason, fenced, enqueued_ms) SELECT $1, 990002, k.sk, 'app', 'test', true, 0 FROM kg.node_keys k "
                         "WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org)
            await _admin("UPDATE kg.records SET class_id = 990001 WHERE org_id = $1 AND id = 'r0010' AND coll = 3", org)
        return await real(tx, o, found, granted)
    monkeypatch.setattr(kg_read, "class_verdicts", change_commits_now)
    readable = [f"r{i:04d}" for i in range(7)]
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=7), limit=50)
    assert fired and [r["id"] for r in page["rows"]] == readable and page["total"] == 7
    with ctx(org):
        again = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=7), limit=50)
    assert [r["id"] for r in again["rows"]] == readable and again["total"] == 7


@pytest.mark.parametrize("mode", ["served", "by_window", "not_served"])
async def test_concurrent_pages_never_wait_for_a_second_pooled_connection(pg_db, monkeypatch, mode) -> None:
    """A page holds one pooled connection (its snapshot) and reads everything on it, the access checks included (``by_window``: every window asked of the access check;
    ``not_served``: the arrows' sample too). With a pool of two and four pages at once, a page that waited for a second connection would wait as long as the pool lets it:
    every connection would be held by a page waiting the same way."""
    import asyncio
    import dataclasses

    from app.core.db import DbConfig, KernelDB
    p0, org = prov(pg_db), new_org()
    await seed_folder(p0, pg_db, org, 25, readable_kids=["c0024"])
    if mode == "not_served":
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    if mode == "by_window":
        monkeypatch.setattr(kh_listing, "CLASS_MAX", 0)
    small = KernelDB(dataclasses.replace(DbConfig.from_env("connectors"), pool_max=2))
    await small.start()
    try:
        p = prov(small)

        async def one() -> dict:
            with ctx(org):
                return await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=["fold", "c0024"]), limit=50)
        pages = await asyncio.wait_for(asyncio.gather(*(one() for _ in range(4))), 30)
    finally:
        await small.close()
    for page in pages:
        assert {r["id"] for r in page["rows"]} == {"fold", "c0024"} and page["total"] == 2
        assert next(r for r in page["rows"] if r["id"] == "fold")["hasChildren"] is True


async def test_a_page_in_a_callers_transaction_never_admits_by_a_stamp_hidden_after_its_check(pg_db, monkeypatch) -> None:
    """In a caller's own transaction (read committed: each statement reads a new commit) the page's statements are not one snapshot. A change to the connector as a whole
    that commits just before the page reads its rows (it hides the connector and a stamp written under that hiding opens r0010 to everyone) must not admit r0010: such a
    page asks the access check about its rows, not the classes."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 12, readable=7)
    await _admin("INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT $1, 990001, k.sk, NULL, '[[]]'::jsonb, $2 FROM kg.node_keys k "
                 "WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org, secrets.token_bytes(16))
    real_sql, real_kq, armed, fired = kgl.page_sql, kh_listing.KhListingMixin._kq, [], []

    def page_sql(*a, **kw) -> str:       # the next read unit is the page statement
        armed.append(True)
        return real_sql(*a, **kw)

    async def change_commits_now(self, fn, transaction) -> object:
        if armed and not fired:
            fired.append(True)
            await _admin("INSERT INTO kg.restamp_queue (org_id, id, app_sk, kind, reason, fenced, enqueued_ms) SELECT $1, 990002, k.sk, 'app', 'test', true, 0 FROM kg.node_keys k "
                         "WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org)
            await _admin("UPDATE kg.records SET class_id = 990001 WHERE org_id = $1 AND id = 'r0010' AND coll = 3", org)
        return await real_kq(self, fn, transaction)
    monkeypatch.setattr(kgl, "page_sql", page_sql)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kq", change_commits_now)
    with ctx(org):
        tid = await p.begin_transaction([], [])
        try:
            page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted=7), limit=50, transaction=tid)
        finally:
            await p.rollback_transaction(tid)
    assert fired and [r["id"] for r in page["rows"]] == [f"r{i:04d}" for i in range(7)] and page["total"] == 7


# The rows of kg.records a backend has read and not yet reported: those its sequential scans read and those its index and bitmap scans fetched from the table (inside a
# function too). Counts of earlier transactions may still be pending, so a page's reads are the difference across it.
KG_RECORDS_READ = """SELECT coalesce((SELECT seq_tup_read FROM pg_stat_xact_user_tables WHERE relid = 'kg.records'::regclass), 0)
    + pg_stat_get_xact_tuples_fetched('kg.records'::regclass)"""


async def _page_and_reads(p, org: str, **kw) -> tuple[dict, int]:
    with ctx(org):
        snap = await p.begin_snapshot()
        try:
            before = await p._q(lambda tx: tx.fetchval(KG_RECORDS_READ), snap)
            page = await p.get_knowledge_hub_connector_page_v3(**asks(org, **kw), transaction=snap)
            return page, int(await p._q(lambda tx: tx.fetchval(KG_RECORDS_READ), snap) - before)
        finally:
            await p.rollback_transaction(snap)


async def test_a_search_for_a_rare_word_reads_its_matches_not_the_connector(pg_db) -> None:
    """A connector of 20,300 rows (300 records and 20,000 rows of other names that sort before them) and a search whose one match sorts last: the page walked the
    connector's name index from its start, filtering every row by the search, to find it (a rare word on a large connector read the connector). By the trigram index of
    the graph nodes' names (graph.records_by_name) it finds the match's node, and the page's statements read that row by its key."""
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 300)
    # the other rows straight into kg (live listing rows of the connector, no graph node, never matched by the search; ASCII names, so their UTF-16 key is a zero byte
    # before each), then the statistics the analyze job would take
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, name, name_key, name_sort) SELECT $1, 9000000000 + i, 3, 'f' || i, 3, k.sk, n, n, "
                 "decode(regexp_replace(encode(convert_to(n, 'UTF8'), 'hex'), '(..)', '00\\1', 'g'), 'hex') FROM kg.node_keys k, generate_series(1, 20000) i, LATERAL (SELECT 'a filler ' || i AS n) x WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'app1'", org)
    await _admin("ANALYZE kg.records")
    page, read = await _page_and_reads(p, org, granted=300, limit=50, filters={"search_query": "r0299"})
    assert [r["id"] for r in page["rows"]] == ["r0299"] and page["total"] == 1
    assert read <= 200, f"read {read} rows of kg.records for one match in a 20,300-row connector"
    # as the service runs it, under its row policy: each node's row by its key (a condition that is not leakproof would have made it a scan of the organisation per node)
    with ctx(org):
        assert [r["node_id"] for r in await p._kq(lambda tx: tx.fetch(kgl.NAME_NODES, "app1", kgl.name_pattern("r0299"), 10001), None)] == ["r0299"]
        plan = json.loads(await p._kq(lambda tx: tx.fetchval("EXPLAIN (FORMAT JSON) " + kgl.NAME_ROWS, org, [3], ["r0299"]), None))
    reads = [(n["Node Type"], n.get("Index Name"), n.get("Index Cond", "")) for n in _nodes(plan) if n.get("Relation Name") == "records"]
    assert reads and all(t == "Index Scan" and i == "records_id" and "coll = g.coll" in c and "id = g.id" in c for t, i, c in reads), reads


NAMED = {"n00": "Printer-Jam.pdf", "n01": "printer jam notes", "n02": "printer_jam_tray.2", "n03": "ΟΣ.Α report", "n04": "νομος.txt", "n05": "Νομοσ x",
         "n06": "100%_done\\x", "n07": "a\\%b_c%d", "n08": "Café crème", "n09": "item 1234 handle", "n10": "item 12345 handle", "n11": "item 1234-handle",
         "n12": "xx--yy", "n13": "Report", "n14": "re port", "n15": "REPORT final", "n16": "rePort.v2", "n17": "jam printer",
         "n18": "\u212aelvin plan", "n19": "\u0130tem 99 list", "n20": "STATE \u017ftate"}
SEARCHES = ["printer-jam", "Printer Jam", "jam.pdf", "ΟΣ Α", "νομος", "100%_done", "\\%b", "b_c%d", "café", "item 1234 handle", "item-1234.handle", "1234 h",
            "xx-yy", "rep", "report", "PORT", "re po", "ab", "%", "nothing here", "jam",
            "kelvin plan", "elvin", "item 99", "tem 99 l", "state"]


@pytest.mark.parametrize("mode", ["served", "not_served", "too_many"])
async def test_a_name_search_by_the_trigram_index_lists_what_the_walk_listed(pg_db, monkeypatch, mode) -> None:
    """Every search, on names with separators, sigmas, wildcard characters, accents and repeated words, lists the same rows and total by the trigram index as by the walk
    of the sort order, for a viewer who may read some of the records: kg deciding by class (``served``), the live check (``not_served``), and a search that matches more
    rows than the index read keeps (``too_many``: the walk). The access check is asked about the rows either way; the index only chooses which rows it is asked about."""
    p, org = prov(pg_db), new_org()
    await _seed_named(p, pg_db, org, NAMED)
    readable = sorted(NAMED)[::2]
    if mode == "not_served":
        await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    if mode == "too_many":
        monkeypatch.setattr(kh_listing, "NAME_HITS_MAX", 1)
    read: list[object] = []
    real = kh_listing.KhListingMixin._kh_name_hits

    async def spy(self, *a, **kw) -> object:
        got = await real(self, *a, **kw)
        read.append(got)
        return got
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_name_hits", spy)

    async def walked(self, *a, **kw) -> None:
        return None
    for q in SEARCHES:
        for sort_dir in ("ASC", "DESC"):
            kw = {"granted_ids": readable, "sort_dir": sort_dir, "filters": {"search_query": q}}
            read.clear()
            by_index, _, first = await walk(p, org, 2, **kw)
            used = read[0] is not None
            with monkeypatch.context() as m:
                m.setattr(kh_listing.KhListingMixin, "_kh_name_hits", walked)
                by_walk, _, old = await walk(p, org, 2, **kw)
            assert by_index == by_walk and first["total"] == old["total"], (q, sort_dir, by_index, by_walk)
            assert used == (mode != "too_many" and kgl.name_pattern(q) is not None) or (mode == "too_many" and len(read[0] or []) <= 1), (q, read)
    with ctx(org):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(org, granted_ids=sorted(NAMED)), limit=50, filters={"search_query": "printer jam"})
    assert [r["id"] for r in page["rows"]] == ["n01", "n00", "n02"]      # the separators read as spaces, as before (" " sorts before "-")


async def test_a_name_search_lists_what_the_walk_lists_while_a_rename_waits_for_the_drain(pg_db, monkeypatch) -> None:
    """A record renamed in the graph whose change the drain has not projected yet: kg still holds the old name, and the listing's search condition reads kg's.
    The walk lists it for the old name and not for the new one until the drain; the trigram lookup, which reads the graph's names, must list the same (the
    connector's rows that wait for the drain are candidates too), and both list the new name once it is drained."""
    from app.services.graph_db.postgres.kg.drainer import drain_org
    p, org = prov(pg_db), new_org()
    await _seed_named(p, pg_db, org, {"n00": "alpha report", "n01": "other report", "n02": "alphabet soup"})
    # a writer whose change the projection has not taken yet (as one by the query or indexing role, which no inline flush follows)
    await _admin("UPDATE graph.nodes SET doc = jsonb_set(doc, '{recordName}', '\"bravo report\"') WHERE org_id = $1 AND coll = 'records' AND id = 'n00'", org)

    async def walked(self, *a, **kw) -> None:
        return None

    async def both(q: str) -> tuple[list[str], list[str]]:
        kw = {"granted_ids": ["n00", "n01", "n02"], "filters": {"search_query": q}}
        by_index, _, first = await walk(p, org, 2, **kw)
        with monkeypatch.context() as m:
            m.setattr(kh_listing.KhListingMixin, "_kh_name_hits", walked)
            by_walk, _, old = await walk(p, org, 2, **kw)
        assert first["total"] == old["total"], (q, first["total"], old["total"])
        return by_index, by_walk
    for q, want in (("alpha", ["n00", "n02"]), ("alpha report", ["n00"]), ("bravo", []), ("report", ["n00", "n01"])):
        by_index, by_walk = await both(q)
        assert by_index == by_walk == want, (q, by_index, by_walk)
    await drain_org(pg_db, org)
    for q, want in (("alpha", ["n02"]), ("bravo", ["n00"]), ("report", ["n00", "n01"])):
        by_index, by_walk = await both(q)
        assert by_index == by_walk == want, (q, by_index, by_walk)
