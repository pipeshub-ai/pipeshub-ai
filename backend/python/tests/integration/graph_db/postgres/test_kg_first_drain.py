"""The first drain of a large connector on a new install: kg's tables have no statistics, and the rows a drain unit reads were written by that same unit, so no ANALYZE can
have seen them. Every statement of the unit must still be planned by its keys: a plan that scans the organisation once per row runs into the statement timeout, the unit rolls
back, and every retry gets the same plan, so the drain never finishes."""
from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.services.graph_db.postgres.kg import drainer, grant_marks, stampgraph
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from app.services.graph_db.postgres.kg.reader import seeks
from tests.support import plan_gate

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_read import _admin, flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
FOLDERS = 50
FILES = 100                                     # per folder: 5,000 files, each with its own grant
CHUNK = 1000
# kg's tables with no statistics, as on a new install (pg_statistic rows gone, the table never analysed): what the planner of the drain's unit sees for rows it wrote itself.
NO_STATS = """DELETE FROM pg_statistic WHERE starelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'kg'::regnamespace);
    UPDATE pg_class SET reltuples = -1, relpages = 0, relallvisible = 0 WHERE relnamespace = 'kg'::regnamespace AND relkind IN ('r', 'p', 'i')"""


# Back to what the session's other tests plan with: every kg table analysed again (an index's pages and rows with its table's).
RESTORE = """DO $$ DECLARE t regclass; BEGIN
    FOR t IN SELECT c.oid::regclass FROM pg_class c WHERE c.relnamespace = 'kg'::regnamespace AND c.relkind IN ('r', 'p') LOOP EXECUTE format('ANALYZE %s', t); END LOOP; END $$"""
# A kg table never analysed, or kg.records without its column statistics.
UNANALYSED = """SELECT array_agg(c.relname::text ORDER BY c.relname) FILTER (WHERE c.reltuples < 0)
      || CASE WHEN EXISTS (SELECT 1 FROM pg_statistic WHERE starelid = 'kg.records'::regclass) THEN '{}'::text[] ELSE '{records: no column statistics}' END
    FROM pg_class c WHERE c.relnamespace = 'kg'::regnamespace AND c.relkind = 'r'"""


async def _unanalysed() -> list[str]:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        return list(await con.fetchval(UNANALYSED) or [])
    finally:
        await con.close()


@pytest.fixture
async def no_stats() -> AsyncIterator[Callable[[], Awaitable[None]]]:
    """Clears kg's statistics when called; analyses every kg table again at teardown, whatever the test did, so no later test of the session plans with none."""
    try:
        yield lambda: _admin(NO_STATS)
    finally:
        await _admin(RESTORE)
        assert await _unanalysed() == [], "kg's statistics are back after the test"


def _rec(org: str, key: str, *, folder: bool = False) -> dict:
    return {"_key": key, "recordName": key, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "externalRecordId": f"x-{key}",
            "mimeType": "application/vnd.folder" if folder else "text/plain"}


async def _world(p, org: str) -> None:
    """app1 with group g1 and FOLDERS folders of FILES files; u1 holds every folder and every file (so each file's grant is marked parent_held), u2 every file alone."""
    files = [(f"f{i}", f"f{i}-r{j}") for i in range(FOLDERS) for j in range(FILES)]
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "g1", "connectorId": "app1", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("u1", "u2")], "users")
        await p.batch_upsert_nodes([_rec(org, f"f{i}", folder=True) for i in range(FOLDERS)], "records")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD"), edge("g1", "recordGroups", "app1", "apps"),
                                    *(edge(u, "users", org, "organizations") for u in ("u1", "u2"))], "belongsTo")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD"),
                                    *(edge("g1", "recordGroups", f"f{i}", "records", relationshipType="PARENT_CHILD") for i in range(FOLDERS))], "nodeRelations")
        await p.batch_create_edges([*(edge(f"f{i}", "records", "g1", "recordGroups") for i in range(FOLDERS))], "belongsTo")
        await p.batch_create_edges([*(edge("u1", "users", f"f{i}", "records", type="USER", role="READER") for i in range(FOLDERS))], "permission")
        await p.batch_create_edges([edge(u, "users", "app1", "apps") for u in ("u1", "u2")], "userAppRelation")
        for i in range(0, len(files), CHUNK):
            part = files[i:i + CHUNK]
            await p.batch_upsert_nodes([_rec(org, k) for _, k in part], "records")
            await p.batch_create_edges([edge(f, "records", k, "records", relationshipType="PARENT_CHILD") for f, k in part], "nodeRelations")
            await p.batch_create_edges([edge(k, "records", f, "records") for f, k in part], "inheritPermissions")
            await p.batch_create_edges([edge(k, "records", "g1", "recordGroups") for _, k in part], "belongsTo")
            await p.batch_create_edges([edge(u, "users", k, "records", type="USER", role="READER") for _, k in part for u in ("u1", "u2")], "permission")


async def test_the_first_drain_of_a_large_connector_commits_with_no_statistics_under_a_short_statement_timeout(pg_provider, pg_db, monkeypatch, no_stats) -> None:
    """One page drains the whole world (every node, grant and mark written and read in one unit) with kg's statistics cleared and every statement of the unit held to 5 s
    after the projection starts. On the plans the planner made of the marks' lookups with no statistics (a record's own row and its parents' rows found by a scan of the
    organisation or the connector per grant) the page did not finish in that time; by its keys it takes well under a second per statement."""
    p, org = pg_provider, new_org()
    await _world(p, org)
    await no_stats()
    project = drainer.project

    async def bounded(tx, *args, **kwargs) -> object:
        await tx.fetchval("/* pipeshub:test.statement_timeout */ SELECT set_config('statement_timeout', '5s', true)")
        return await project(tx, *args, **kwargs)
    monkeypatch.setattr(drainer, "project", bounded)
    st = await drainer.drain_org(pg_db, org, page=10 * FOLDERS * FILES)
    assert st.pages == 1, st                    # the world in one unit: the case of a new install's first big page

    async def marks(tx) -> tuple[int, int, int]:
        app = await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        rows = await tx.fetch(grant_marks.BY_APP, org, app, 0, 0, "", 100_000)
        wrong = sum((r["parent_held"], r["node_group"]) != (r["want_held"], r["want_group"]) for r in rows)
        return len(rows), sum(r["parent_held"] for r in rows), wrong
    with ctx(org):
        assert await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)) == 0      # the log is drained: the unit committed
        grants, held, wrong = await pg_db.run(marks)
    # the folders' grants, two per file and each user's grant of the app; u1's file grants are under a folder u1 holds; every mark is what its rows give
    assert (grants, held, wrong) == (FOLDERS + 2 * FOLDERS * FILES + 2, FOLDERS * FILES, 0)


# The key column each index of a statement below must be bounded by (with the org): a probe of any other index, or of one of these by the org alone, reads the org per row.
KEY_OF = {"records_pkey": r"\bsk >= ", "records_id": r"\bid >= ", "records_parent": r"\bparent_sk >= ", "links_pkey": r"\bparent_sk\b", "links_child": r"\bchild_sk\b",
          "scope_members_pkey": r"\bprincipal_sk\b", "scope_members_by_node": r"\bnode_sk >= "}
KG = ("records", "links", "scope_members")


def _not_seeks(plan: dict[str, Any]) -> list[str]:
    """Each read of kg.records, kg.links or kg.scope_members in an ``EXPLAIN (ANALYZE, FORMAT JSON)`` plan that is not a seek: not one of KEY_OF's indexes, not bounded by the org and
    its key, or reading more than a folder's rows per probe."""
    bad = []
    for node in plan_gate.scans(plan, KG):
        if node["Relation Name"] not in KG:
            continue
        bounds = plan_gate.bounds(node)
        per = (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0) + node.get("Rows Removed by Index Recheck", 0)) / max(node.get("Actual Loops", 0), 1)
        if not bounds or any(i not in KEY_OF or "org_id = " not in c or not re.search(KEY_OF[i], c) for i, c in bounds):
            bad.append(f"{node['Node Type']} on {node['Relation Name']}: {bounds}")
        elif per > FILES + 1:
            bad.append(f"{bounds[0][0]}: {per:.0f} rows per probe")
    return bad


async def test_the_marks_and_stamp_lookups_of_a_drain_are_seeks_with_no_statistics_and_analysed(pg_provider, pg_db, no_stats) -> None:
    """EXPLAIN gate: on the world above (drained and promoted), with kg's statistics cleared and then analysed, the statements a drain unit runs per grant or node (the marks of the grants on
    nodes, the children of parents, the stamp's kg rows), as the unit runs them (under reader.seeks, custom plans), read kg.records, kg.links and kg.scope_members only by
    their key's index, bounded by the org and the key, a folder's rows at most per probe. Run on every PostgreSQL version of the suite."""
    p, org = pg_provider, new_org()
    await _world(p, org)
    assert (await flip(pg_db, org))[0] == 1          # copied and promoted: the stamp reads kg's rows of a served connector

    async def keys(tx) -> tuple[list[int], list[int], list[int], list[str]]:
        rows = await tx.fetch("SELECT sk, id, folder FROM kg.records WHERE org_id = $1 AND coll = 3 ORDER BY sk", org)
        files = [r for r in rows if not r["folder"]]
        return [r["sk"] for r in files[::50]], [r["sk"] for r in rows if r["folder"]], [3] * len(files[::50]), [r["id"] for r in files[::50]]
    with ctx(org):
        nodes, folders, colls, ids = await pg_db.run(keys)
    assert nodes and len(folders) == FOLDERS
    wants = {"marks on nodes": (grant_marks.ON_NODES, [nodes]), "children of parents": (grant_marks.CHILDREN, [folders]),
             "stamp kg rows": (stampgraph.KG_GRANTED, [colls, ids, CODE_EPOCH])}
    for state in ("no statistics", "analysed"):
        await (no_stats() if state == "no statistics" else _admin(RESTORE))
        for name, (sql, args) in wants.items():
            async def go(tx, sql=sql, args=args) -> tuple[int, dict[str, Any]]:
                async with seeks(tx):
                    n = len(await tx.fetch(sql, org, *args))
                    ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, org, *args)
                return n, (json.loads(ran) if isinstance(ran, str) else ran)[0]
            with ctx(org):
                n, ran = await pg_db.run(go, custom_plan=True)
            assert n > 0, f"{state} {name}: the statement found nothing, so the gate judged no probe"
            if bad := _not_seeks(ran):
                pytest.fail(f"{state} {name}: not a seek per key: {bad}\n{json.dumps(ran, indent=1)[:6000]}")


async def test_clearing_kg_statistics_is_undone_at_teardown(no_stats) -> None:
    """The fixture's own check (its teardown asserts the same for every test that uses it): cleared here, so the teardown has to bring them back."""
    await no_stats()
    assert "records" in await _unanalysed()
