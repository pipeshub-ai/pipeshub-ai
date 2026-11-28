"""The retype statements of ``batch_create_edges`` (a grant written for a pair that holds a grant of another type): the probe, under a custom plan and
a generic plan, and the delete, under the custom plan it runs with, read the grants of the batch's holders and targets by key, so their buffers are
bounded by the grants on the batch's targets, not by the organisation. Joined to the table per pair, the probe was planned as a merge or hash join over
every permission edge of the organisation once the plan knew the batch size (a custom plan, which a prepared statement gets for its first five runs
and a unit in ``force_custom_plan`` always gets).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_ADMIN_URL (to analyse), PG_POOLER=none.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

import app.core.db.uow as uow
from tests.support import plan_gate

from .pg_support import new_org
from .test_grants import in_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

USERS, RECORDS, BATCH = 20, 5000, 1000
UNTOUCHED = """SELECT count(*) AS n, md5(string_agg(concat_ws('|', from_coll, from_id, to_coll, to_id, ekey, doc::text), ',' ORDER BY from_coll, from_id, to_coll, to_id, ekey))
    AS sum FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND NOT (from_coll = 'users' AND from_id = ANY($2::text[]) AND to_coll = 'records'
    AND to_id = ANY($3::text[]))"""      # every grant that is not a pair of the batch: other holders, other records, a batch user on another record
SITES = ("pipeshub:graph.batch_create_edges_retype_probe ", "pipeshub:graph.batch_create_edges_retype ")


def _lit(v: Any) -> str:  # noqa: ANN401
    if isinstance(v, list):
        return "ARRAY[" + ", ".join(_lit(x) for x in v) + "]::text[]"
    return "'" + str(v).replace("'", "''") + "'"


@pytest.fixture
async def granted(pg_db) -> AsyncIterator[str]:
    """An organisation whose users hold a GROUP grant on each of its records, analysed: many grants, so a join over them is what a plan that
    knows the batch size weighs against keyed reads."""
    org = new_org()
    with in_org(org):
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, 'permission', 'users', 'u' || u, 'records', 'r' || r, 'GROUP', '{"type": "GROUP", "role": "READER"}'::jsonb
            FROM generate_series(0, $2::int - 1) u, generate_series(0, $3::int - 1) r""", org, USERS, RECORDS), write=True)
        # other holders on the batch's records, which the keyed read reaches and must leave alone
        await pg_db.run(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, 'permission', 'groups', 'g' || g, 'records', 'r' || r, 'GROUP', '{"type": "GROUP", "role": "OWNER"}'::jsonb
            FROM generate_series(0, 4) g, generate_series(0, $2::int - 1) r""", org, BATCH), write=True)
        admin = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
        try:
            await admin.execute("ANALYZE graph.edges")
        finally:
            await admin.close()
        yield org
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)


@pytest.mark.pg_table_lock           # analyses the shared edges table
async def test_the_retype_statements_read_by_key_within_the_batch_targets_grants(pg_provider, pg_db, granted, monkeypatch) -> None:
    org = granted
    ran: dict[str, tuple] = {}
    real = uow.Tx._run

    async def spy(self, op, sql, *args, internal=False) -> object:
        site = next((s for s in SITES if s in sql[:80]), None)
        if site:
            ran[site] = (sql, args)
        return await real(self, op, sql, *args, internal=internal)
    monkeypatch.setattr(uow.Tx, "_run", spy)
    pairs = [(f"u{u}", f"r{r}") for u in range(USERS) for r in range(BATCH)]
    targets = [f"r{r}" for r in range(BATCH)]
    with in_org(org):
        # the grants the keyed reads may touch: every grant on a target of the batch
        reach = await pg_db.run(lambda tx: tx.fetchval("""SELECT count(*) FROM graph.edges WHERE org_id = $1 AND coll = 'permission'
            AND to_id = ANY($2::text[])""", org, targets))
        users = [f"u{u}" for u in range(USERS)]
        before = await pg_db.run(lambda tx: tx.fetchrow(UNTOUCHED, org, users, targets))
        await pg_provider.batch_create_edges([{"from_collection": "users", "from_id": u, "to_collection": "records", "to_id": r, "type": "USER",
                                               "role": "WRITER"} for u, r in pairs], "permission")
        monkeypatch.setattr(uow.Tx, "_run", real)
        assert set(ran) == set(SITES)
        kinds = await pg_db.run(lambda tx: tx.fetch("""SELECT ekey, count(*) AS n FROM graph.edges WHERE org_id = $1 AND coll = 'permission'
            AND to_id = ANY($2::text[]) GROUP BY ekey""", org, targets))
        assert {r["ekey"]: r["n"] for r in kinds} == {"USER": len(pairs), "GROUP": 5 * BATCH}   # the batch's pairs retyped; the groups' grants kept
        after = await pg_db.run(lambda tx: tx.fetchrow(UNTOUCHED, org, users, targets))
        assert dict(after) == dict(before) and before["n"] == USERS * (RECORDS - BATCH) + 5 * BATCH, (dict(before), dict(after))

        async def plans(tx) -> dict[str, Any]:
            out = {}
            for site, modes in ((SITES[0], ("force_custom_plan", "force_generic_plan")), (SITES[1], ("force_custom_plan",))):
                sql, args = ran[site]
                await tx._con.execute(f"PREPARE q({', '.join(['text'] + ['text[]'] * (len(args) - 1))}) AS " + sql)
                for mode in modes:
                    await tx._con.execute(f"SET LOCAL plan_cache_mode = {mode}")
                    out[(site, mode)] = json.loads(await tx._con.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE q(" + ", ".join(_lit(a) for a in args) + ")"))
                await tx._con.execute("DEALLOCATE q")
            return out
        t = await pg_provider.begin_transaction([], [])
        try:
            # the delete is explained after the retype committed, so it finds nothing to delete; its read of the bound set is what is measured
            seen = await pg_provider._txn(t).run(plans)
        finally:
            await pg_provider.rollback_transaction(t)
    for (site, mode), plan in seen.items():
        assert not (bad := plan_gate.violations(plan)), (site, mode, bad)
        top = plan_gate.root(plan)
        read = top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0)
        # a keyed read of the targets' grants, whatever the organisation holds: a page or two a grant (the retype left each one's old row
        # version in the index too), against the 80,000 of a per-pair probe's generic plan here
        assert read <= 3 * (len(targets) + reach), (site, mode, read, reach)
