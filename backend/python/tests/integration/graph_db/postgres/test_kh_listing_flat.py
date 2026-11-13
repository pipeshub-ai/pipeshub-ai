"""The flat listing of a connector from the typed graph: the plans of its statements, what it refuses, what it says when it stops early, and that it reads only the session's organisation."""
from __future__ import annotations

import json
import logging
import re
import secrets

import pytest

from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingNotReady,
    KnowledgeHubListingTooLarge,
)
from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.base import NotImplementedInPostgres
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import net
from app.services.graph_db.postgres.kg import read as kg_read
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
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, start_id="g1", start_type="recordGroup")      # a flatten below a node
        with pytest.raises(KnowledgeHubListingNotReady):
            await p.get_knowledge_hub_connector_page_v3(**asks(org), limit=5, flatten=False)      # a browse of the connector itself
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
                       ("children", kgl.children_of_sql(""), [org, [app_sk], 20, []]), ("links", kh_listing.LINKS, [org, [app_sk]]), ("state", kgl.STATE, [org, "app1"])]
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
                loose = [n["Index Name"] for n in _nodes(plan) if analyzed and str(n.get("Index Name")).startswith(("records_", "links_")) and not NODE_COND.search(n.get("Index Cond") or "")]
                assert not loose, f"{at}: reads {loose} without the node in its index condition: {[n.get('Index Cond') for n in _nodes(plan) if n.get('Index Name') in loose]}"
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
    name and then the id. Names of thousands of characters are copied and listed among names that share their prefix: the longest the copy takes (4,000 characters, ``w_records_upsert``),
    here outside the BMP so 16,000 bytes of key that do not compress, and names of multi-byte characters inside the BMP."""
    import random
    rng = random.Random(5713)
    stem = "x" * (kgl.NAME_PREFIX // 2)      # code units of UTF-16: exactly the indexed prefix
    names: dict[str, str] = {}
    for i in range(40):
        tail = "".join(rng.choice("abé\U0001f600") for _ in range(rng.randint(0, 3)))
        names[f"r{i:04d}"] = rng.choice([stem + tail, stem[:-1] + tail, stem[:-2] + tail, tail or "a"])
    names["r0040"] = names["r0001"]           # the same name: the id decides
    names["r0041"] = "".join(chr(rng.randint(0x20000, 0x2A6DF)) for _ in range(4000))
    names["r0042"] = names["r0041"][:-1] + "a"       # shares the prefix (and all but the last character) with r0041
    names["r0043"] = names["r0041"][:2000]           # shares the prefix and ends before r0041 does
    names["r0044"] = "".join(chr(rng.randint(0x4E00, 0x9FFF)) for _ in range(3000))
    names["r0045"] = names["r0044"][:2500] + "\u00e9"
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
