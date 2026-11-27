"""The access check keeps the plan of its knowledge-base membership read: a prepared statement is planned for its arguments five times and then, if the
generic plan looks cheaper, gets the generic one, which reads every membership edge of the app (``edges_by_target``) instead of the asked records
(the primary key). The statement runs with a custom plan; the other statements of the check are unchanged.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_ADMIN_URL (to analyse), PG_POOLER=none.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.core.db.uow import Tx
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.access import CHILDREN, CHILDREN_OPEN
from app.services.graph_db.postgres.kg.reader import seeks
from tests.support import plan_gate

from .test_grants import in_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

KB_SQL = """SELECT e.from_id, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'records' AND e.from_id = ANY($2::text[])
    AND e.to_coll = 'apps' AND e.to_id = ANY($3::text[])"""
MEMBERS = 10000


def _indexes(node: dict) -> set[str]:
    return ({node["Index Name"]} if "Index Name" in node else set()).union(*(_indexes(c) for c in node.get("Plans", [])))


@pytest.fixture
async def kb_world(pg_provider, pg_db) -> AsyncIterator[tuple[Any, str]]:
    org = uuid.uuid4().hex[:24]
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        p = pg_provider
        await p.batch_upsert_nodes([{"_key": "u", "orgId": org, "userId": "uid"}], "users")
        await p.batch_upsert_nodes([{"_key": "kb", "orgId": org, "type": "KB", "permissionModel": "APP_LEVEL", "isActive": True}], "apps")
        await p.batch_create_edges([{"from_collection": "users", "from_id": "u", "to_collection": "apps", "to_id": "kb", "type": "USER", "role": "READER"}], "permission")
        # the app's members, and the memberships, inheritance and hierarchy edges of 100k other records in 1000 groups: many distinct targets make a lookup by target look cheap
        # to a plan that does not know the asked ids
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes SELECT $1, 'records', 'r' || i, jsonb_build_object('orgId', $1::text, 'connectorId', 'kb',
            'isDeleted', false, 'origin', 'UPLOAD') FROM generate_series(0, $2::int - 1) i""", org, MEMBERS), write=True)
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id)
            SELECT $1, 'belongsTo', 'records', 'r' || i, 'apps', 'kb' FROM generate_series(0, $2::int - 1) i
            UNION ALL SELECT $1, c, 'records', 'x' || i, 'recordGroups', 'g' || (i / 100)
              FROM generate_series(0, 99999) i, unnest(ARRAY['belongsTo', 'inheritPermissions']) c
            UNION ALL SELECT $1, 'nodeRelations', 'recordGroups', 'g' || (i / 100), 'records', 'x' || i FROM generate_series(0, 99999) i""", org, MEMBERS), write=True)
        admin = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
        try:
            await admin.execute("ANALYZE graph.edges")
            await admin.execute("ANALYZE graph.nodes")
        finally:
            await admin.close()
        yield p, org
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


@pytest.mark.pg_table_lock           # changes the planner statistics or storage settings of shared tables
async def test_the_membership_read_never_gets_the_generic_plan(kb_world) -> None:
    p, org = kb_world
    asked = [f"r{i * 40}" for i in range(100)]
    t = await p.begin_transaction([], [])        # one connection for every call, so the statement's plan counters add up
    try:
        # the reason for the custom plan: on this graph the generic plan would read every membership edge of the app
        async def plans(tx) -> dict[str, set[str]]:
            await tx._con.execute("PREPARE kb(text, text[], text[]) AS " + KB_SQL)
            out = {}
            for mode in ("force_custom_plan", "force_generic_plan"):
                await tx._con.execute(f"SET LOCAL plan_cache_mode = {mode}")
                lits = ",".join(f"'{a}'" for a in asked)
                rows = await tx._con.fetch(f"EXPLAIN (FORMAT JSON) EXECUTE kb('{org}', ARRAY[{lits}]::text[], ARRAY['kb']::text[])")
                out[mode] = _indexes(json.loads(rows[0][0])[0]["Plan"])
            await tx._con.execute("DEALLOCATE kb")
            await tx._con.execute("SET LOCAL plan_cache_mode = auto")
            return out
        seen = await p._txn(t).run(plans)
        assert any(n.endswith("_pkey") for n in seen["force_custom_plan"]) and not any(n.endswith("to_id_idx") for n in seen["force_custom_plan"]), seen
        assert any(n.endswith("to_id_idx") for n in seen["force_generic_plan"]), f"the generic plan no longer reads edges_by_target: {seen}"
        # eight checks in the one unit: the sixth and later runs would take the generic plan
        for _ in range(8):
            got = await p._kh_v3_accessible_rows("u", org, asked, [], access=None, transaction=t)
            assert {r["id"] for r in got} == set(asked)         # every asked member is admitted by the app-level KB grant
        counts = await p._txn(t).run(lambda tx: tx.fetch(
            "SELECT generic_plans, custom_plans FROM pg_prepared_statements WHERE statement LIKE '%pipeshub:graph.access_kb%' AND statement NOT LIKE '%pg_prepared_statements%'"))
        assert len(counts) == 1, counts
        assert counts[0]["generic_plans"] == 0 and counts[0]["custom_plans"] == 8, dict(counts[0])
    finally:
        await p.rollback_transaction(t)


@pytest.mark.parametrize(("custom", "mode"), [(False, "auto"), (True, "force_custom_plan")])
async def test_a_custom_plan_fetch_gives_the_units_plan_mode_back(pg_db, org_ctx, custom: bool, mode: str) -> None:
    """A unit that is custom-planned throughout stays so after the call; any other keeps its own mode."""
    async def run(tx) -> list[str]:
        seen = [await tx.fetchval("SELECT current_setting('plan_cache_mode')")]
        await tx.fetch_custom_plan("SELECT 1")
        seen.append(await tx.fetchval("SELECT current_setting('plan_cache_mode')"))
        return seen
    with org_ctx(uuid.uuid4().hex[:24]):
        assert await pg_db.run(run, custom_plan=custom) == [mode, mode]


VRID_TAGS = ("pipeshub:graph.records_by_virtual_id", "pipeshub:graph.access_targets_vrid")


def _vrid_reads(node: dict) -> list[str]:
    """Every read of graph.nodes in a JSON plan that is not an index probe by the virtual record id: a sequential scan, or a scan of another key."""
    bad = []
    if node.get("Relation Name", "").startswith("nodes"):
        conds = [node.get("Index Cond", "")] + [c.get("Index Cond", "") for c in node.get("Plans", []) if c["Node Type"] == "Bitmap Index Scan"]
        if not any("virtualRecordId" in c for c in conds):
            bad.append(f"{node['Node Type']} on {node['Relation Name']}: {conds}")
    return bad + [b for c in node.get("Plans", []) for b in _vrid_reads(c)]


def _scans(node: dict) -> list[dict]:
    return ([node] if "Relation Name" in node else []) + [s for c in node.get("Plans", []) for s in _scans(c)]


@pytest.fixture
async def vrid_world(pg_provider, pg_db) -> AsyncIterator[tuple[Any, str]]:
    """A knowledge base the user reads, with records two to a virtual record id, some deleted or stubs; another org holds the same ids."""
    org, other = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        p = pg_provider
        await p.batch_upsert_nodes([{"_key": "u", "orgId": org, "userId": "uid"}], "users")
        await p.batch_upsert_nodes([{"_key": "kb", "orgId": org, "type": "KB", "permissionModel": "APP_LEVEL", "isActive": True}], "apps")
        await p.batch_create_edges([{"from_collection": "users", "from_id": "u", "to_collection": "apps", "to_id": "kb", "type": "USER", "role": "READER"}], "permission")
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes SELECT $1, 'records', 'r' || i, jsonb_build_object('orgId', $1::text, 'connectorId', 'kb',
            'virtualRecordId', 'v' || (i / 2), 'isDeleted', i % 7 = 0, 'isPlaceholder', i % 11 = 0, 'origin', 'UPLOAD') FROM generate_series(0, 5999) i""", org), write=True)
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id)
            SELECT $1, 'belongsTo', 'records', 'r' || i, 'apps', 'kb' FROM generate_series(0, 5999) i""", org), write=True)
    with bind(OrgContext(other, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.nodes SELECT $1, 'records', 'o' || i, jsonb_build_object('orgId', $1::text, 'connectorId', 'kb',
            'virtualRecordId', 'v' || i) FROM generate_series(0, 2999) i""", other), write=True)
    admin = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await admin.execute("ANALYZE graph.nodes")
    finally:
        await admin.close()
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        yield pg_provider, org
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
    with bind(OrgContext(other, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)


@pytest.mark.pg_table_lock           # changes the planner statistics or storage settings of shared tables
async def test_a_batch_of_virtual_record_ids_is_read_by_index_probes_at_any_size(vrid_world, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under row security the statement's `doc->>'virtualRecordId' = ANY(...)` cannot use the key's index (`->>` is not leakproof), so a batch over the
    vetted read's 1000 ids read the whole org. Every size is now read in pages of the vetted read, one index probe per id, with the same answer."""
    p, org = vrid_world
    sent: list[tuple[str, tuple]] = []
    fetch = Tx.fetch

    async def spy(self: Tx, sql: str, *args: object) -> list[asyncpg.Record]:
        if any(t in sql for t in VRID_TAGS):
            sent.append((sql, args))
        return await fetch(self, sql, *args)
    monkeypatch.setattr(Tx, "fetch", spy)
    asked = ["r22"]                                                   # a stub asked by its id is read; the other stubs are not
    for n in (1, 1000, 16000):
        vrids = [f"v{i}" for i in range(n)]
        sent.clear()
        got = {r["id"] for r in await p._kh_v3_accessible_rows("u", org, asked, vrids, access=None, transaction=None)}
        plans = []
        admin = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
        try:
            admin.add_log_listener(lambda _c, m, plans=plans: plans.append(json.loads(m.message.split("plan:", 1)[1])["Plan"]) if "plan:" in m.message else None)
            async with admin.transaction():
                for stmt in ("LOAD 'auto_explain'", "SET LOCAL auto_explain.log_min_duration = 0", "SET LOCAL auto_explain.log_nested_statements = on",
                             "SET LOCAL auto_explain.log_level = notice", "SET LOCAL auto_explain.log_format = json", "SET LOCAL client_min_messages = notice",
                             "SET LOCAL ROLE pipeshub_connectors"):
                    await admin.execute(stmt)
                await admin.execute("SELECT set_config('app.org_id', $1, true)", org)
                for sql, args in sent:
                    await admin.fetch(sql, *args)
        finally:
            await admin.close()
        assert sent and plans, n
        assert any(s["Relation Name"].startswith("nodes") for pl in plans for s in _scans(pl)), (n, plans)
        assert not [b for pl in plans for b in _vrid_reads(pl)], (n, [b for pl in plans for b in _vrid_reads(pl)])
        with monkeypatch.context() as m:
            m.setattr("app.services.graph_db.postgres.access._VRID_PAGE", 0)      # a page is never short: the plain statement answers
            plain = {r["id"] for r in await p._kh_v3_accessible_rows("u", org, asked, vrids, access=None, transaction=None)}
        live = {f"r{i}" for i in range(min(2 * n, 6000)) if i % 7 and (i % 11 or f"r{i}" in asked)}
        assert got == plain == live | {"r22"}, (n, len(got), len(plain), len(live))


@pytest.mark.pg_table_lock           # changes the planner statistics or storage settings of shared tables
async def test_the_walk_down_reads_each_child_by_its_key_on_every_edges_leaf_the_statistics_do_not_know_the_org_in(pg_db) -> None:
    """A hop of the walk down from a group with 2,000 children, half of them inheriting from it. On a leaf whose statistics put the org at a row (its rows
    written after the last analyse) the joins of the hop read the org's nodes and scanned the group's edges once per node, then the org's inherit edges once
    per child: ~2 million buffers (3 s) here, and past the statement timeout in CI, where the search scope then came back empty. One org per leaf, each
    analysed before its rows are written: every edge and node is read by its key, a few buffers per child, with the same answer."""
    kids = 2000
    admin = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    orgs: list[str] = []
    try:
        while len(orgs) < 16:           # graph.edges_def is HASH (org_id) with modulus 16: one org for each remainder
            o = uuid.uuid4().hex[:24]
            if await admin.fetchval("SELECT satisfies_hash_partition('graph.edges_def'::regclass, 16, $1, $2::text)", len(orgs), o):
                orgs.append(o)
        await admin.execute("ANALYZE graph.edges")
        await admin.execute("ANALYZE graph.nodes")
        for o in orgs:
            await admin.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'recordGroups', 'big', jsonb_build_object('orgId', $1::text, 'connectorId', 'app')
                UNION ALL SELECT $1, 'records', 'c' || g, jsonb_build_object('orgId', $1::text, 'connectorId', 'app') FROM generate_series(1, $2::int) g""", o, kids)
            await admin.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, 'nodeRelations', 'recordGroups', 'big', 'records', 'c' || g, 'PARENT_CHILD', '{"relationshipType": "PARENT_CHILD"}'::jsonb FROM generate_series(1, $2::int) g
                UNION ALL SELECT $1, 'inheritPermissions', 'records', 'c' || g, 'recordGroups', 'big', '', '{}'::jsonb FROM generate_series(1, $2::int / 2) g""", o, kids)
        for o in orgs:
            for sql, args, taken in ((CHILDREN, ("app", []), kids // 2), (CHILDREN_OPEN, (), kids)):
                async def explain(tx: Tx, sql: str = sql, args: tuple = args, o: str = o) -> tuple[object, int]:
                    async with seeks(tx):
                        plan = await tx._con.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, o, ["recordGroups"], ["big"], ["PARENT_CHILD", "ATTACHMENT"], *args)
                        return plan, len(await tx.fetch(sql, o, ["recordGroups"], ["big"], ["PARENT_CHILD", "ATTACHMENT"], *args))
                with in_org(o):
                    ran, got = await pg_db.run(explain)
                plan = plan_gate.root(ran)
                leaf = next(n["Relation Name"] for n in plan_gate.scans(plan, "edges"))
                assert got == taken, (leaf, got)
                assert not (bad := plan_gate.unkeyed(plan, "nodes", r"\bid = ")), (leaf, bad)
                assert not (bad := plan_gate.unkeyed(plan, "edges", r"\bfrom_id\b")), (leaf, bad)
                assert plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0) < 8 * kids, (leaf, plan.get("Shared Hit Blocks"))
    finally:
        for o in orgs:
            await admin.execute("DELETE FROM graph.edges WHERE org_id = $1", o)
            await admin.execute("DELETE FROM graph.nodes WHERE org_id = $1", o)
            await admin.execute("DELETE FROM kg.graph_changes WHERE org_id = $1", o)
        await admin.close()
