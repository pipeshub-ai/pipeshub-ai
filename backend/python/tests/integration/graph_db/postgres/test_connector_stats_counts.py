"""The kept record counts of ``get_connector_stats`` (graph_0021, ``connector_stats``) against the walk, after every kind of change, read before and after the projection.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

import asyncpg
import pytest

from app.config.constants.arangodb import FOLDER_MIME_TYPES, Connectors
from app.core.db.uow import Tx
from app.services.graph_db.postgres import connector_stats
from app.services.graph_db.postgres.kg.locks import LOCK_CLASS
from app.services.graph_db.postgres.kg.reader import seeks
from app.services.graph_db.postgres.lookups import STATS_CONNECTOR, STATS_KB
from tests.support import plan_gate

from .test_access import APP, World, _org
from .test_access import w as w
from .test_grants import in_org
from .test_kg_read import _admin

pytestmark = pytest.mark.integration

KB_APP = "kb1"
BASE = {"recordType": "FILE", "indexingStatus": "QUEUED", "extractionStatus": "NOT_STARTED"}


async def walk(w: World, app: str) -> Counter:
    is_kb = await w.p._lrows("SELECT 1 FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'apps' AND n.id = $2 AND n.doc->>'type' = $3", w.org, app,
                             Connectors.KNOWLEDGE_BASE.value)
    sql = STATS_KB if is_kb else STATS_CONNECTOR
    rows = await w.p._lrows(sql, w.org, app, FOLDER_MIME_TYPES)
    return Counter({(r["recordType"], r["indexingStatus"], r["extractionStatus"], r["entitiesStatus"]): r["cnt"] for r in rows})


async def kept(w: World, app: str) -> Counter | None:
    rows = await w.p._q(lambda tx: connector_stats.read(tx, w.org, app), None, isolation="repeatable_read")
    return None if rows is None else Counter({(r["recordType"], r["indexingStatus"], r["extractionStatus"], r["entitiesStatus"]): r["cnt"] for r in rows})


async def settle(w: World, pg_db) -> None:
    while await connector_stats.run(pg_db, w.org):
        pass


async def same(w: World, pg_db, *, logged_whole: bool = False) -> None:
    """Both apps: before the projection the read corrects for the log (or, while a group or an app is logged, answers None); after it, the counts alone match."""
    for app in (APP, KB_APP):
        got = await kept(w, app)
        assert (got is None) if logged_whole else (got == await walk(w, app)), (app, got)
    await settle(w, pg_db)
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app
    assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_changes WHERE org_id = $1", w.org)) == 0


async def world(w: World, pg_db) -> None:
    await w.nodes("apps", {"_key": KB_APP, "type": Connectors.KNOWLEDGE_BASE.value})
    await w.nodes("recordGroups", *({"_key": g} for g in ("top", "mid", "low", "cut", "side")))
    await w.edge("belongsTo", ("recordGroups", "top"), ("apps", APP))
    await w.edge("belongsTo", ("recordGroups", "mid"), ("recordGroups", "top"))
    await w.edge("belongsTo", ("recordGroups", "low"), ("recordGroups", "mid"))
    await w.edge("belongsTo", ("recordGroups", "side"), ("apps", APP))
    await w.edge("belongsTo", ("recordGroups", "cut"), ("recordGroups", "gone"))
    await w.nodes("records", {"_key": "a", **BASE, "virtualRecordId": "va", "contentRev": "1"}, {"_key": "b", **BASE, "virtualRecordId": "vb", "contentRev": "1"},
                  {"_key": "twice", **BASE}, {"_key": "deep", **BASE, "recordType": "MAIL"}, {"_key": "stray", **BASE},
                  {"_key": "folder", **BASE, "mimeType": FOLDER_MIME_TYPES[0]}, {"_key": "k1", **BASE}, {"_key": "k2", **BASE, "isDeleted": True})
    await w.nodes("stageStates", {"_key": "va:1:entities", "status": "COMPLETED"}, {"_key": "vb:1:other", "status": "COMPLETED"})
    for r, g in (("a", "top"), ("b", "mid"), ("twice", "top"), ("twice", "side"), ("deep", "low"), ("stray", "cut"), ("folder", "top")):
        await w.edge("belongsTo", ("records", r), ("recordGroups", g))
    for r in ("k1", "k2", "a"):
        await w.edge("belongsTo", ("records", r), ("apps", KB_APP))
    await w.edge("belongsTo", ("records", "b"), ("apps", APP))      # a record linked straight to an app that is not a collection does not count there


async def test_the_kept_counts_equal_the_walk_after_every_change(w: World, pg_db) -> None:
    await world(w, pg_db)
    assert await kept(w, APP) is None                                  # not counted whole yet: the walk answers
    await settle(w, pg_db)
    assert (await walk(w, APP)).total() == 4 and (await walk(w, KB_APP)).total() == 2
    await same(w, pg_db)
    # a record's own fields: status flips, its type, trash, a folder, internal, placeholder, its revision (another stage state)
    await w.nodes("records", {"_key": "a", "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED"})
    await same(w, pg_db)
    await w.nodes("records", {"_key": "deep", "recordType": "WEBPAGE"}, {"_key": "twice", "isDeleted": True})
    await same(w, pg_db)
    await w.nodes("records", {"_key": "twice", "isDeleted": False}, {"_key": "b", "mimeType": FOLDER_MIME_TYPES[0]}, {"_key": "k1", "isInternal": True})
    await same(w, pg_db)
    await w.nodes("records", {"_key": "b", "mimeType": "text/plain", "contentRev": "2"}, {"_key": "k1", "isPlaceholder": True, "isInternal": False})
    await same(w, pg_db)
    # the entities stage state: its status, one on a new revision, one deleted
    await w.nodes("stageStates", {"_key": "va:1:entities", "status": "FAILED"}, {"_key": "vb:2:entities", "status": "IN_PROGRESS"})
    await same(w, pg_db)
    await w.p.delete_nodes(["va:1:entities"], "stageStates")
    await same(w, pg_db)
    # membership of a record: a new link, a link gone, a new record, a record deleted, a link to a collection
    await w.nodes("records", {"_key": "new", **BASE, "recordType": "TICKET"})
    await w.edge("belongsTo", ("records", "new"), ("recordGroups", "low"))
    await w.edge("belongsTo", ("records", "stray"), ("recordGroups", "side"))
    await same(w, pg_db)
    await w.p.delete_edge("twice", "records", "top", "recordGroups", "belongsTo")
    await w.edge("belongsTo", ("records", "new"), ("apps", KB_APP))
    await same(w, pg_db)
    await w.p.delete_nodes(["deep"], "records")
    await same(w, pg_db)
    # a record group moves: the cut group joins, the mid group leaves (its records with it), then comes back further down
    await w.edge("belongsTo", ("recordGroups", "cut"), ("recordGroups", "side"))
    await same(w, pg_db, logged_whole=True)
    await w.p.delete_edge("mid", "recordGroups", "top", "recordGroups", "belongsTo")
    await same(w, pg_db, logged_whole=True)
    await w.edge("belongsTo", ("recordGroups", "mid"), ("recordGroups", "cut"))
    await same(w, pg_db, logged_whole=True)
    # the app itself: the collection stops being one (its records now count by group, and it has none), then is one again
    await w.nodes("apps", {"_key": KB_APP, "type": "DRIVE"})
    await same(w, pg_db, logged_whole=True)
    await w.nodes("apps", {"_key": KB_APP, "type": Connectors.KNOWLEDGE_BASE.value})
    await same(w, pg_db, logged_whole=True)
    stats = (await w.p.get_connector_stats(w.org, APP))["data"]["stats"]
    assert stats["total"] == (await walk(w, APP)).total()


async def test_the_ten_group_bound_of_the_walk_holds(w: World, pg_db) -> None:
    for i in range(1, 13):
        await w.nodes("recordGroups", {"_key": f"d{i}"})
        await w.edge("belongsTo", ("recordGroups", f"d{i}"), ("apps", APP) if i == 1 else ("recordGroups", f"d{i - 1}"))
    await w.nodes("records", {"_key": "at10", **BASE}, {"_key": "at11", **BASE})
    await w.edge("belongsTo", ("records", "at10"), ("recordGroups", "d10"))
    await w.edge("belongsTo", ("records", "at11"), ("recordGroups", "d11"))
    await settle(w, pg_db)
    assert (await kept(w, APP)).total() == 1
    await w.p.delete_edge("d2", "recordGroups", "d1", "recordGroups", "belongsTo")
    await w.edge("belongsTo", ("recordGroups", "d2"), ("apps", APP))          # one group shorter: the record eleven groups down is now ten
    await settle(w, pg_db)
    assert await kept(w, APP) == await walk(w, APP) and (await walk(w, APP)).total() == 2


async def test_a_write_not_yet_committed_is_counted_when_it_commits(w: World, pg_db) -> None:
    await world(w, pg_db)
    await settle(w, pg_db)
    txn = await w.p.begin_transaction(read=[], write=["records"])
    await w.p.batch_upsert_nodes([{"_key": "a", "orgId": w.org, "indexingStatus": "FAILED"}], "records", transaction=txn)
    await settle(w, pg_db)                                                    # the projection does not see it, and leaves its log row
    assert await kept(w, APP) == await walk(w, APP)
    await w.p.commit_transaction(txn)
    assert await kept(w, APP) == await walk(w, APP) and (await walk(w, APP))[("FILE", "FAILED", "NOT_STARTED", "COMPLETED")] == 1
    await settle(w, pg_db)
    assert await kept(w, APP) == await walk(w, APP)


async def test_a_read_costs_the_same_whatever_the_size_of_the_connector(w: World, pg_db) -> None:
    await w.nodes("recordGroups", {"_key": "g"})
    await w.edge("belongsTo", ("recordGroups", "g"), ("apps", APP))
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'r' || g,
        jsonb_build_object('recordType', 'FILE', 'indexingStatus', CASE WHEN g % 3 = 0 THEN 'COMPLETED' ELSE 'QUEUED' END) FROM generate_series(1, 3000) g""", w.org), write=True)
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT $1, 'belongsTo', 'records', 'r' || g,
        'recordGroups', 'g' FROM generate_series(1, 3000) g""", w.org), write=True)
    await settle(w, pg_db)
    assert (await kept(w, APP)) == await walk(w, APP) and (await walk(w, APP)).total() == 3000

    async def touched(sql: str, *args: object) -> int:
        plan = "\n".join(r[0] for r in await pg_db.run(lambda tx: tx._con.fetch("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + sql, *args)))
        root = next(line for line in plan.splitlines() if line.strip().startswith("Buffers:"))
        return sum(int(m) for m in re.findall(r"(?:hit|read)=(\d+)", root))
    assert await touched(connector_stats.COUNTS, w.org, APP) < 50
    assert await touched(STATS_CONNECTOR, w.org, APP, FOLDER_MIME_TYPES) > 3000


async def test_the_counts_of_one_org_are_not_read_in_another(w: World, pg_db) -> None:
    await world(w, pg_db)
    await settle(w, pg_db)
    other = World(w.p, _org())
    with in_org(other.org):
        assert await kept(other, APP) is None
        assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_counts WHERE app_id = $1", APP)) == 0
    assert await kept(w, APP) == await walk(w, APP)


async def test_a_log_row_that_names_nothing_does_not_stop_the_projection(w: World, pg_db) -> None:
    await world(w, pg_db)
    await settle(w, pg_db)
    await pg_db.run(lambda tx: tx.execute("INSERT INTO graph.stats_changes (org_id, keys) VALUES ($1, ARRAY['x', '', 'q:1', 'r'])", w.org), write=True)
    await w.nodes("records", {"_key": "a", "indexingStatus": "FAILED"})
    await same(w, pg_db)


async def test_a_new_install_drops_the_log_until_an_app_is_counted_whole(w: World, pg_db) -> None:
    """The log of a graph written before any app was counted names nothing the first counts (seeds, which read the graph after the drop) miss: it is dropped in one unit,
    not recounted a record at a time (on a new install that was every record of the org, and a moved group's subtree, in one unit). The counts are the walk's after."""
    await world(w, pg_db)
    assert await connector_stats.drain(pg_db, w.org) > 0
    rows = await pg_db.run(lambda tx: tx.fetchval("""SELECT (SELECT count(*) FROM graph.stats_changes WHERE org_id = $1)
        + (SELECT count(*) FROM graph.stats_records WHERE org_id = $1)""", w.org))
    assert rows == 0                                                              # dropped, nothing recounted
    await settle(w, pg_db)                                                        # counted whole from the graph: the walk's numbers
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app
    await w.nodes("records", {"_key": "a", "indexingStatus": "FAILED"})        # once an app is counted, the log is recounted again
    assert await connector_stats.drain(pg_db, w.org) == 1
    assert await kept(w, APP) == await walk(w, APP)


# graph's tables with no statistics, as after a restore or on a leaf written since the last analyse; RESTORE_GRAPH analyses them again.
NO_GRAPH_STATS = """DELETE FROM pg_statistic WHERE starelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'graph'::regnamespace);
    UPDATE pg_class SET reltuples = -1, relpages = 0, relallvisible = 0 WHERE relnamespace = 'graph'::regnamespace AND relkind IN ('r', 'p', 'i')"""
RESTORE_GRAPH = """DO $$ DECLARE t regclass; BEGIN
    FOR t IN SELECT c.oid::regclass FROM pg_class c WHERE c.relnamespace = 'graph'::regnamespace AND c.relkind IN ('r', 'p') LOOP EXECUTE format('ANALYZE %s', t); END LOOP; END $$"""
LIVE_KEYS = {"nodes": r"\bid = ", "edges": r"\bfrom_id >= "}      # a node by its id, a link by its source (the primary key's order)


async def test_the_live_rows_read_each_node_and_link_by_its_key_with_no_statistics_and_analysed(w: World, pg_db) -> None:
    """EXPLAIN gate of LIVE as the drain and the read run it (under reader.seeks): every read of graph.nodes is by id and every read of graph.edges by its source, with
    graph's statistics cleared and analysed. As joins, PostgreSQL 18 costed the primary key as a skip scan over to_id and read the org's links once per hop instead."""
    await world(w, pg_db)
    ids = ["a", "b", "twice", "deep", "stray", "k1", "k2"]

    async def go(tx) -> tuple[int, dict]:
        async with seeks(tx):
            n = len(await tx.fetch(connector_stats.LIVE, w.org, ids, FOLDER_MIME_TYPES, connector_stats.KB))
            ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + connector_stats.LIVE, w.org, ids, FOLDER_MIME_TYPES, connector_stats.KB)
        return n, json.loads(ran) if isinstance(ran, str) else ran
    try:
        for state in ("no statistics", "analysed"):
            await _admin(NO_GRAPH_STATS if state == "no statistics" else RESTORE_GRAPH)
            n, ran = await pg_db.run(go, custom_plan=True)
            assert n == 6, state                                                   # a, b (by group), twice, deep, a and k1 (the collection); stray and k2 count nowhere
            assert plan_gate.scans(ran, "edges"), state
            if bad := plan_gate.violations(ran, LIVE_KEYS):
                pytest.fail(f"{state}: not a read by key: {bad}\n{json.dumps(ran, indent=1)[:6000]}")
    finally:
        await _admin(RESTORE_GRAPH)


BULK = 200          # records of the world's connector beyond world()'s, under group "low": past a page many times over at PAGE below
PAGE = 3


async def bulk(w: World, pg_db) -> None:
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'm' || g,
        jsonb_build_object('recordType', 'FILE', 'indexingStatus', CASE WHEN g % 3 = 0 THEN 'COMPLETED' ELSE 'QUEUED' END) FROM generate_series(1, $2::int) g""",
                                          w.org, BULK), write=True)
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT $1, 'belongsTo', 'records', 'm' || g,
        'recordGroups', 'low' FROM generate_series(1, $2::int) g""", w.org, BULK), write=True)


@pytest.fixture
def paged(monkeypatch) -> dict[str, list]:
    """PAGE records a unit of the first count; records each statement of graph.stats_* (its rows) and each apply (its ids)."""
    monkeypatch.setattr(connector_stats, "PAGE", PAGE, raising=False)
    seen: dict[str, list] = {"rows": [], "applied": []}
    run, apply = Tx._run, connector_stats._apply

    async def counted(self, op, sql, *args, **kw):  # noqa: ANN202
        out = await run(self, op, sql, *args, **kw)
        if "pipeshub:graph.stats" in sql[:60]:
            n = len(out) if isinstance(out, list) else int(out.rsplit(" ", 1)[-1]) if isinstance(out, str) and out[-1:].isdigit() else 0
            seen["rows"].append((sql[3:40], n))
        return out

    async def applied(tx, org, ids):  # noqa: ANN202
        await apply(tx, org, ids)
        seen["applied"].append(list(ids))
    monkeypatch.setattr(Tx, "_run", counted)
    monkeypatch.setattr(connector_stats, "_apply", applied)
    return seen


async def test_the_first_count_runs_one_page_a_unit_and_marks_the_apps_when_the_last_commits(w: World, pg_db, paged) -> None:
    """Every unit of the first count recounts one page: no statement reads or writes more rows than a page's records give (two apps a record at most), however large the
    connector; no app is read from the counts before the last page commits; then both are, with the walk's numbers. The 16-part count wrote a sixteenth of the connector
    a statement and marked the app in its one unit."""
    await world(w, pg_db)
    await bulk(w, pg_db)
    await connector_stats.drain(pg_db, w.org)                                     # the log of the world, dropped
    paged["rows"].clear()
    units = 0
    while await kept(w, APP) is None:
        assert await connector_stats.seed(pg_db, w.org, APP)
        units += 1
        assert units <= (BULK + 8) // PAGE + 2, "the count did not finish"
    assert max(n for _, n in paged["rows"]) <= 2 * PAGE, sorted(paged["rows"], key=lambda r: -r[1])[:5]
    assert units >= (BULK + 8) // PAGE, units                                     # a page a unit
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app
    assert (await walk(w, APP)).total() == 4 + BULK


async def test_an_interrupted_first_count_resumes_at_its_checkpoint(w: World, pg_db, paged, monkeypatch) -> None:
    """A unit that fails (a restart, a timeout) rolls back alone: the next unit goes on from the last committed page, so every record is counted once by a committed
    unit, the failed page once more, and nothing again from the start."""
    await world(w, pg_db)
    await bulk(w, pg_db)
    await connector_stats.drain(pg_db, w.org)
    for _ in range(5):
        assert await connector_stats.seed(pg_db, w.org, APP)
    apply = connector_stats._apply

    async def failing(tx, org, ids):  # noqa: ANN202
        await apply(tx, org, ids)
        raise RuntimeError("the worker stopped")
    monkeypatch.setattr(connector_stats, "_apply", failing)
    with pytest.raises(RuntimeError):
        await connector_stats.seed(pg_db, w.org, APP)
    monkeypatch.setattr(connector_stats, "_apply", apply)
    await settle(w, pg_db)
    pages = paged["applied"]
    committed = pages[:5] + pages[6:]
    ids = [i for p in committed for i in p]
    assert len(ids) == len(set(ids)), "a committed page counted a record again"
    assert pages[5] == pages[6], "the next unit did not take the failed page again"
    assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = 'records'", w.org)) == len(set(ids))
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app


async def test_writes_during_the_first_count_are_counted_once_it_ends(w: World, pg_db, paged) -> None:
    """Writes between the units of the first count, behind its page and ahead of it, are recounted from the log (it is not dropped once the count has started): a status
    change on both sides, a new record, a deleted record, a group move. A kept row of a record gone before the count (from an earlier recount whose log was dropped) is
    recounted to none. After it, the counts are the walk's."""
    await world(w, pg_db)
    await bulk(w, pg_db)
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.stats_records (org_id, record_id, app_id, record_type, indexing, extraction, entities)
        VALUES ($1, 'ghost', $2, 'FILE', 'QUEUED', '', '')""", w.org, APP), write=True)
    await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.stats_counts (org_id, app_id, record_type, indexing, extraction, entities, n)
        VALUES ($1, $2, 'FILE', 'QUEUED', '', '', 1)""", w.org, APP), write=True)
    await connector_stats.drain(pg_db, w.org)
    for _ in range(20):
        assert await connector_stats.seed(pg_db, w.org, APP)
    assert await kept(w, APP) is None
    await w.nodes("records", {"_key": "a", "indexingStatus": "FAILED"}, {"_key": "m50", "indexingStatus": "FAILED"}, {"_key": "m99", "indexingStatus": "FAILED"})
    await w.nodes("records", {"_key": "late", **BASE})
    await w.edge("belongsTo", ("records", "late"), ("recordGroups", "top"))
    await w.p.delete_nodes(["m10"], "records")
    await w.edge("belongsTo", ("recordGroups", "cut"), ("recordGroups", "side"))     # stray now counts
    await settle(w, pg_db)
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app
    assert (await walk(w, APP)).total() == 4 + BULK + 1 - 1 + 1


async def test_a_page_of_the_first_count_reads_its_records_by_key_with_no_statistics_and_analysed(w: World, pg_db) -> None:
    """EXPLAIN gate of the first count's page as it runs (under reader.seeks): graph.nodes and graph.stats_records are read as a range of record ids after the checkpoint,
    a page's rows at most, with graph's statistics cleared and analysed; no read of the organisation's records grows with a connector."""
    await world(w, pg_db)
    await bulk(w, pg_db)
    await settle(w, pg_db)                                                        # kept rows for the stats side to read

    async def go(tx) -> tuple[int, dict]:
        async with seeks(tx):
            n = len(await tx.fetch(connector_stats.SWEEP_PAGE, w.org, "b", PAGE))
            ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + connector_stats.SWEEP_PAGE, w.org, "b", PAGE)
        return n, json.loads(ran) if isinstance(ran, str) else ran
    try:
        for state in ("no statistics", "analysed"):
            await _admin(NO_GRAPH_STATS if state == "no statistics" else RESTORE_GRAPH)
            n, ran = await pg_db.run(go, custom_plan=True)
            assert n == PAGE, state
            reads = plan_gate.scans(ran, ("nodes", "stats_records"))
            assert {str(r["Relation Name"]).startswith("stats") for r in reads} == {True, False}, state
            if bad := plan_gate.violations(ran, {"nodes": r"\bid > ", "stats_records": r"\brecord_id > "}):
                pytest.fail(f"{state}: not a range of record ids: {bad}\n{json.dumps(ran, indent=1)[:6000]}")
            if big := [f"{r['Relation Name']}: {r['Actual Rows']}" for r in reads if r.get("Actual Rows", 0) > 2 * PAGE]:
                pytest.fail(f"{state}: a read past the page: {big}")
    finally:
        await _admin(RESTORE_GRAPH)


async def test_the_drop_of_a_new_install_log_is_one_batch_a_unit_and_no_app_is_counted_before_it_ends(w: World, pg_db, monkeypatch) -> None:
    """A unit drops one batch of the log (a bounded unit, and the org's lock held that long), however much is logged or keeps being logged; a pass that ends on a full
    batch counts no app yet, so no app is counted while dropped rows are left to recount a record at a time. When the log is gone, the counts are the walk's."""
    await world(w, pg_db)
    logged = await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_changes WHERE org_id = $1", w.org))
    assert logged > 4, logged
    monkeypatch.setattr(connector_stats, "DROP_ROWS", 2)
    monkeypatch.setattr(connector_stats, "UNITS_PER_PASS", 1)
    assert await connector_stats.drain(pg_db, w.org) == 2
    assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_changes WHERE org_id = $1", w.org)) == logged - 2
    passes = 0
    while await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_changes WHERE org_id = $1", w.org)):
        assert await connector_stats.run(pg_db, w.org) == 2
        assert await kept(w, APP) is None and await kept(w, KB_APP) is None
        passes += 1
        assert passes <= logged, "the drop did not end"
    await settle(w, pg_db)
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app


async def test_a_pass_whose_drain_finds_the_org_held_counts_no_app(w: World, pg_db, monkeypatch) -> None:
    """A drain that finds another unit holding the org (another worker, perhaps dropping the log) reports that, not an empty log: the pass ends without counting an app,
    even if the holder lets go before the pass would seed. Counted then, the rest of a new install's log would be recounted a record at a time."""
    await world(w, pg_db)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    holder = con.transaction()
    await holder.start()
    await con.execute("SELECT pg_advisory_xact_lock($1::int, hashtext($2))", LOCK_CLASS, f"{w.org}|stats")
    drain, released = connector_stats.drain, []

    async def contended(db, org):  # noqa: ANN202
        n = await drain(db, org)
        await holder.commit()                                                     # the other unit ends right after
        released.append(True)
        return n
    monkeypatch.setattr(connector_stats, "drain", contended)
    try:
        await connector_stats.run(pg_db, w.org)
    finally:
        if not released:
            await holder.rollback()
        await con.close()
    monkeypatch.setattr(connector_stats, "drain", drain)
    assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM graph.stats_apps WHERE org_id = $1", w.org)) == 0
    await settle(w, pg_db)
    for app in (APP, KB_APP):
        assert await kept(w, app) == await walk(w, app), app
