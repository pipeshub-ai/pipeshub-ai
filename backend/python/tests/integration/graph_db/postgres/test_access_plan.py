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

from app.core.org_context import OrgContext, bind

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
