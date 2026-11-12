"""The flat listing of a connector from the typed graph: the plans of its statements, what it refuses, what it says when it stops early, and that it reads only the session's organisation."""
from __future__ import annotations

import json
import logging

import pytest

from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.base import NotImplementedInPostgres
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.provider import PostgresGraphProvider

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


async def test_a_connector_with_no_copy_is_refused_and_a_shape_not_served_here_is_the_providers_own_error(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await seed(p, pg_db, org, 3, promote=False)
    with ctx(org):
        with pytest.raises(KnowledgeHubListingNotReady):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5)
        with pytest.raises(NotImplementedInPostgres):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, flatten=False)
        with pytest.raises(NotImplementedInPostgres):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, max_depth=3)


async def test_another_organisations_connector_is_never_read(pg_db) -> None:
    p, mine, theirs = prov(pg_db), new_org(), new_org()
    await seed(p, pg_db, mine, 5)
    await seed(p, pg_db, theirs, 5)
    with ctx(mine):
        page = await p.get_knowledge_hub_connector_page_v3(**asks(theirs), limit=10)       # asks for an org that is not the session's
    assert page["rows"] == [] and not page["hasMore"]
    other = new_org()
    with ctx(other), pytest.raises(KnowledgeHubListingNotReady):      # the session's org holds no such connector: nothing of the other's is found
        await p.get_knowledge_hub_connector_page_v3(**asks(other), limit=10)


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
    assert page["total"] == 4 and page["totalCapped"] is True


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
        arrows = kgl.Params([org, [app_sk], []])
        statements += [("arrows", kgl.arrows_sql(kgl.class_admit("c", arrows, {"plain": [1], "own": [2], "unvouched": [], "granted": ["x"], "walked": set()})), arrows.values)]
        statements += [("scan", kgl.scan_sql("", "$4"), [org, app_sk, 501, 0]), ("hiders", kgl.HAS_HIDER, [org, app_sk]), ("hidden", kgl.HIDDEN, [org, app_sk]),
                       ("children", kh_listing.CHILDREN, [org, [app_sk], 20, []]), ("links", kh_listing.LINKS, [org, [app_sk]]), ("state", kgl.STATE, [org, "app1"])]
        for name, sql, args in statements:
            await con.execute("SET enable_seqscan = off")      # a statement with an index it can use has a plan without a scan, whatever the size of the tables the planner is guessing at
            plan = json.loads(await con.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args))
            seq = [n for n in _nodes(plan) if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") in ("records", "links", "node_keys")]
            assert not seq, f"{name} ({'after' if analyzed else 'before'} ANALYZE): sequential scan of {[n['Relation Name'] for n in seq]}"
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
