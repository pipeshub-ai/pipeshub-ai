"""The counters of the typed graph (kg.counts): after any sequence of changes, folded or not, a key's rows sum to count(*) of what it counts (by the listing's own
predicates); a seed counts a connector whole and repairs what drifted; a fold that races the writers loses and doubles nothing; and a listing that counts from them
counts only what the viewer's classes open."""
from __future__ import annotations

import asyncio
import logging
import os
import random

import asyncpg
import pytest

from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.kg import counts
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg.writers import KgWriter

from .pg_support import new_org
from .test_kg_derivation import ctx, edge, prov
from .test_kg_projection import run, update_node
from .test_kg_projection_races import OPS, everything2, world2
from .test_kg_read import flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

TRUTH = f"""SELECT r.app_sk, 1::smallint AS facet, r.class_id, r.kind, r.folder, NULL::text AS record_type, NULL::text AS origin, count(*) AS n FROM kg.records r
    WHERE r.org_id = $1 AND r.app_sk IS NOT NULL AND {kgl.LIVE} AND {kgl.DECIDED} GROUP BY 1, 3, 4, 5
  UNION ALL SELECT r.app_sk, 2::smallint, NULL, NULL, NULL, r.record_type, r.origin, count(*) FROM kg.records r
    WHERE r.org_id = $1 AND r.app_sk IS NOT NULL AND {counts.FACET_ROWS} GROUP BY 1, 6, 7"""
HELD = """SELECT app_sk, facet, class_id, kind, folder, record_type, origin, sum(n) AS n FROM kg.counts WHERE org_id = $1 AND facet <> 0
    GROUP BY 1, 2, 3, 4, 5, 6, 7 HAVING sum(n) <> 0"""


def _keyed(rows) -> dict:
    return {tuple(r[c] for c in counts.KEY): r["n"] for r in rows}


async def state(p, org: str) -> tuple[dict, dict, int]:
    """(count(*) by key, the counters by key, the deltas not folded), read when no write is running."""
    async def go(tx) -> tuple:
        return _keyed(await tx.fetch(TRUTH, org)), _keyed(await tx.fetch(HELD, org)), await tx.fetchval("SELECT count(*) FROM kg.counts WHERE org_id = $1 AND NOT folded", org)
    with ctx(org):
        return await p._q(go, None)


async def fence_some(p, org: str, rng: random.Random, *, lift: bool) -> None:
    """Hide (or show again) a random record of the org as a pending restamp does: the rows leave (or rejoin) what a listing counts by class."""
    async def go(tx) -> None:
        rows = await tx.fetch("SELECT sk, coll, id, kind FROM kg.records WHERE org_id = $1 AND kind IN (2, 3) ORDER BY sk", org)
        if rows:
            pick = rng.sample(list(rows), k=min(len(rows), rng.randint(1, 3)))
            await KgWriter(tx).fence_rows([(r["sk"], r["coll"], r["id"], r["kind"]) for r in pick], None if lift else 77)
    with ctx(org):
        await p._q(go, None, write=True)


async def fold_all(pg_db, org: str) -> None:
    with ctx(org):
        while await counts.fold(pg_db, org):
            pass


@pytest.mark.parametrize("seed", range(40))
async def test_after_random_changes_the_counters_are_count_star_folded_or_not(pg_db, seed: int) -> None:
    rng = random.Random(seed)
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))
    for _ in range(rng.randint(3, 10)):
        r = rng.random()
        if r < 0.15:
            await fence_some(p, org, rng, lift=False)
        elif r < 0.25:
            await fence_some(p, org, rng, lift=True)
        elif r < 0.4:
            await fold_all(pg_db, org)
        else:
            await run(p, org, await rng.choice(OPS)(p, org))
        truth, held, _ = await state(p, org)
        assert held == truth
    await fold_all(pg_db, org)
    truth, held, unfolded = await state(p, org)
    assert held == truth and unfolded == 0


async def test_a_seed_counts_a_connector_whole_repairs_a_drift_and_marks_it_readable(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))

    async def app_sk(tx) -> int:
        return await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
    with ctx(org):
        sk = await p._q(app_sk, None)
        assert await p._q(lambda tx: counts.class_rows(tx, org, sk, [1]), None) is None          # not counted whole yet: not read

        async def drift(tx) -> None:
            await KgWriter(tx).counts_add([(sk, 2, None, None, None, "FILE", "CONNECTOR", 1000)])
        await p._q(drift, None, write=True)
    truth, held, _ = await state(p, org)
    assert held != truth
    with ctx(org):
        assert await counts.seed(pg_db, org, sk)
    truth, held, _ = await state(p, org)
    assert held == truth
    with ctx(org):
        assert await p._q(lambda tx: counts.class_rows(tx, org, sk, []), None) == {}
        got = await p._q(lambda tx: counts.facets(tx, org, sk), None)
    assert got is not None and got["origins"] == sorted({k[6] for k in truth if k[0] == sk and k[1] == 2 and k[6]})


async def test_a_fold_that_races_the_writers_loses_and_doubles_nothing(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))
    rng = random.Random(7)
    done = asyncio.Event()

    async def writer() -> None:
        try:
            for _ in range(20):
                await run(p, org, await rng.choice(OPS)(p, org))
        finally:
            done.set()

    async def folder() -> None:
        while not done.is_set():
            with ctx(org):
                await counts.fold(pg_db, org, limit=3)
            await asyncio.sleep(0)
    await asyncio.gather(writer(), folder(), folder())
    truth, held, _ = await state(p, org)
    assert held == truth


async def test_the_counters_of_one_organisation_are_never_read_or_folded_by_another(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    for o in (a, b):
        await world2(p, o)
        await run(p, o, await everything2(p, o))
    before = await state(p, b)
    await fold_all(pg_db, a)
    assert await state(p, b) == before and before[2] > 0                                         # b's deltas are still there, unfolded
    with ctx(a):
        assert await p._q(lambda tx: tx.fetchval("SELECT count(*) FROM kg.counts WHERE org_id = $1", b), None) == 0       # the row policy hides them


async def _group_world(p, db, org: str, n: int) -> None:
    """One connector of ``n`` records under a group that ``u1`` is granted on (every record inherits it); ``u2`` may use the connector and is granted nothing."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "G", "connectorId": "app1", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([{"_key": f"r{i:04d}", "recordName": f"R{i:04d}", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}
                                    for i in range(n)], "records")
        await p.batch_create_edges([edge(f"r{i:04d}", "records", "g1", "recordGroups") for i in range(n)], "belongsTo")
        await p.batch_create_edges([edge("g1", "recordGroups", f"r{i:04d}", "records", relationshipType="PARENT_CHILD") for i in range(n)], "nodeRelations")
        await p.batch_create_edges([edge(f"r{i:04d}", "records", "g1", "recordGroups") for i in range(n)], "inheritPermissions")
        await p.batch_upsert_nodes([{"_key": "u1", "userId": "user-1", "orgId": org, "email": "u@example.com"}, {"_key": "u2", "userId": "user-2", "orgId": org, "email": "v@example.com"}], "users")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps"), edge("u2", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "g1", "recordGroups", type="USER", role="READER")], "permission")
    await flip(db, org)


def _asks(org: str, user: str, **kw) -> dict:
    return {"app_id": "app1", "org_id": org, "grantee_ids": [user], "gated_app_ids": ["app1"], "granted_ids": kw.pop("granted_ids", ["g1"] if user == "u1" else []), **kw}


async def _page(p, org: str, user: str, **kw) -> dict:
    with ctx(org):
        return await p.get_knowledge_hub_connector_page_v3(**_asks(org, user, **kw), limit=3, include_total=True)


async def test_a_listing_counts_past_its_cap_from_the_counters_and_only_what_the_viewer_may_read(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _group_world(p, pg_db, org, 12)
    monkeypatch.setattr(kh_listing, "COUNT_CAP", 4)
    page = await _page(p, org, "u1")
    assert page["total"] == 4 and page["totalCapped"] is True                    # counted where the rows are: stops at the cap
    monkeypatch.setenv("PG_KG_COUNTERS", "1")
    page = await _page(p, org, "u1")
    assert page["total"] == 4 and page["totalCapped"] is True                    # not counted whole yet: the same
    await counts.run(pg_db, org)
    page = await _page(p, org, "u1")
    assert page["total"] == 13 and not page.get("totalCapped") and page["counts"] == {"record": 12, "recordGroup": 1}       # the group itself: by its own grant, counted where it is
    nobody = await _page(p, org, "u2")                                            # granted nothing: the counters say nothing of the rows
    assert nobody["total"] == 0 and not nobody["rows"]
    filtered = await _page(p, org, "u1", filters={"search_query": "R000"})        # a filter is counted where the rows are
    assert filtered["total"] == 4 and filtered["totalCapped"] is True


async def _own_records(p, org: str, n: int) -> list[str]:
    """``n`` records of the connector outside the group, each granted to ``u1`` alone: their rows need their own grant, so the scan counts them, never the counters."""
    ids = [f"o{i:04d}" for i in range(n)]
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": i, "recordName": i.upper(), "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for i in ids], "records")
        await p.batch_create_edges([edge("u1", "users", i, "records", type="USER", role="READER") for i in ids], "permission")
    return ids


async def test_a_capped_listing_keeps_the_exact_class_count_and_its_counts_add_up_to_its_total_on_every_page(pg_db, monkeypatch) -> None:
    """12 rows the viewer reads by class (counted by the counters) and 6 records it reads by their own grant (plus the group, by its own): with the cap at 4 the scan of the
    own-grant rows stops at 4, so the total is 12 + 4, a lower bound that says so, and the counts by type add up to it. The service carries both on its cursor, so every
    page of the listing reports the same total and counts."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )
    from tests.permissions.kh_layer import _Config

    p, org = prov(pg_db), new_org()
    await _group_world(p, pg_db, org, 12)
    own = await _own_records(p, org, 6)
    await flip(pg_db, org)
    await counts.run(pg_db, org)
    monkeypatch.setattr(kh_listing, "COUNT_CAP", 4)
    monkeypatch.setenv("PG_KG_COUNTERS", "1")
    page = await _page(p, org, "u1", granted_ids=["g1", *own])
    assert page["total"] == 16 and page["totalCapped"] is True
    assert sum(page["counts"].values()) == page["total"] and page["counts"]["record"] >= 12

    service = KnowledgeHubService(logging.getLogger("kg-counts"), p, _Config())
    seen, cursor = [], None
    for _ in range(10):
        with ctx(org):
            got = await service.get_nodes(user_id="user-1", org_id=org, parent_id="app1", parent_type="app", flattened=True, include=["counts"], limit=5,
                                          sort_by="name", sort_order="asc", **({"cursor": cursor} if cursor else {}))
        assert got.success, got
        seen.append((got.pagination.totalItems, got.pagination.totalCapped, got.counts.total, tuple(sorted((c.label, c.count) for c in got.counts.items))))
        cursor = got.pagination.nextCursor
        if not cursor:
            break
    assert len(seen) > 1 and len(set(seen)) == 1, seen                          # one total and one breakdown for the whole listing
    total, capped, counted, items = seen[0]
    assert total == counted == sum(n for _, n in items) == 16 and capped is True


async def test_a_restore_keeps_no_counter_and_the_seed_counts_the_restored_rows(pg_db) -> None:
    """A restore (db.restore_window) inserts an org's exported rows as the restore role, with the triggers on. Its kg.records rows add no delta and the exported counter
    rows are not kept, so the connector carries no mark that it was counted whole: the worker's seed counts it again from the restored rows."""
    p, src, dst = prov(pg_db), new_org(), new_org()
    await world2(p, src)
    await run(p, src, await everything2(p, src))
    await counts.run(pg_db, src)
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        tables = {t: [dict(r) for r in await con.fetch(f"SELECT * FROM kg.{t} WHERE org_id = $1", src)] for t in ("node_keys", "projection_state", "counts", "records")}
        assert tables["counts"] and tables["records"]
        await con.execute("INSERT INTO kernel.restore_window (org, until) VALUES ($1, now() + interval '1 hour')", dst)
        try:
            async with con.transaction():
                await con.execute("SET LOCAL ROLE pipeshub_restore")
                for t, rows in tables.items():
                    cols = list(rows[0])
                    await con.executemany(f"INSERT INTO kg.{t} ({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))})",
                                          [[dst if c == "org_id" else r[c] for c in cols] for r in rows])
        finally:
            await con.execute("DELETE FROM kernel.restore_window WHERE org = $1", dst)
        assert await con.fetchval("SELECT count(*) FROM kg.counts WHERE org_id = $1", dst) == 0
        assert await con.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1", dst) == len(tables["records"])
    finally:
        await con.close()
    assert await counts.run(pg_db, dst)                                          # every restored connector is counted whole again
    truth, held, unfolded = await state(p, dst)
    assert truth and held == truth and unfolded == 0
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:                                                                         # and its rows, new to the statistics, ask the worker to analyze kg
        assert await con.fetchval("SELECT count(*) FROM kg.analyze_requests WHERE org_id = $1", dst) == 1
    finally:
        await con.close()


async def test_a_connector_copied_again_at_the_current_projection_epoch_keeps_its_counters_equal_to_count_star(pg_db) -> None:
    """A connector an older projection code copied: epoch 5 wrote no kind-3 links (every PARENT_CHILD parent), and records arrived since that its copy does not hold. The
    worker's bootstrap finds the copy differs from the graph and leaves the connector on the walk; it is copied again from the start (``backfill(restart=True)``) at the current
    epoch and promoted. Every row the drain and the copy write goes through the counter trigger, so the counters the connector was counted whole with equal count(*) afterwards."""
    from app.services.graph_db.postgres.kg import backfill as bf
    from app.services.graph_db.postgres.kg import bootstrap
    from app.services.graph_db.postgres.kg.drainer import drain_org
    from app.services.graph_db.postgres.kg.model import CODE_EPOCH, LINK_PARENT_CHILD

    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await flip(pg_db, org)
    await counts.run(pg_db, org)
    late = [f"late{i}" for i in range(3)]
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        assert await con.fetchval("SELECT count(*) FROM kg.counts WHERE org_id = $1 AND facet = 0", org)        # counted whole before the copy
        assert await con.fetchval("SELECT count(*) FROM kg.links WHERE org_id = $1 AND kind = $2", org, LINK_PARENT_CHILD)
        # what epoch-5 code left: no kind-3 links, and its epoch on the connectors
        await con.execute("DELETE FROM kg.links WHERE org_id = $1 AND kind = $2", org, LINK_PARENT_CHILD)
        await con.execute("UPDATE kg.projection_state SET code_epoch = $2 WHERE org_id = $1 AND app_sk <> 0", org, CODE_EPOCH - 1)
        with ctx(org):
            await p.batch_upsert_nodes([{"_key": i, "recordName": i, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for i in late], "records")
            await p.batch_create_edges([edge("fold", "records", i, "records", relationshipType="PARENT_CHILD") for i in late], "nodeRelations")
        await drain_org(pg_db, org)
        with ctx(org):
            await bootstrap.bring_to_enforce(pg_db, org, {})                     # the copy of epoch 5 differs from the graph: on the walk
        assert await con.fetchval("SELECT mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk WHERE s.org_id = $1 AND k.id = 'app1'", org) != "enforce"
        for app in ("app1", "app2"):
            out = await bf.backfill(pg_db, org, app, restart=True)
            assert out.flipped, (app, out)
            await bf.promote(pg_db, org, app)
        assert await con.fetchval("SELECT count(*) FROM kg.projection_state WHERE org_id = $1 AND app_sk <> 0 AND NOT (mode = 'enforce' AND code_epoch = $2)", org, CODE_EPOCH) == 0
        # copied again: the kind-3 links of the rows the old copy already held are back (the drain alone only projects what changed since, the late records)
        assert await con.fetchval("""SELECT count(*) FROM kg.links l JOIN kg.records r ON r.org_id = l.org_id AND r.sk = l.child_sk
            WHERE l.org_id = $1 AND l.kind = $2 AND NOT r.id = ANY($3::text[])""", org, LINK_PARENT_CHILD, late)
        assert await con.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = ANY($2::text[])", org, late) == len(late)
    finally:
        await con.close()
    truth, held, _ = await state(p, org)
    assert truth and held == truth
    await fold_all(pg_db, org)
    truth, held, unfolded = await state(p, org)
    assert held == truth and unfolded == 0


async def test_records_with_the_parent_and_grant_rule_keep_the_counters_equal_to_count_star(pg_db) -> None:
    """A live record under a folder its viewer reads by a grant of the folder's own becomes PARENT_AND_GRANT, then RESTRICTED: the records writer takes the rule,
    the record moves to a class of its own and back, and the counters by class follow it, folded or not."""
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await run(p, org, await everything2(p, org))
    with ctx(org):
        await p.batch_create_edges([edge("f1", "records", "fold", "records")], "inheritPermissions")
        await p.batch_create_edges([edge("u1", "users", "fold", "records", type="USER", role="READER")], "permission")
    for rule in ("PARENT_AND_GRANT", "RESTRICTED", "PARENT_AND_GRANT"):
        await run(p, org, await update_node(p, org, "records", "f1", isDeleted=False, accessRule=rule) | {("records", "fold")})

        async def stored(tx) -> str | None:
            return await tx.fetchval("SELECT rule FROM kg.records WHERE org_id = $1 AND id = 'f1'", org)
        with ctx(org):
            assert await p._q(stored, None) == rule
        truth, held, _ = await state(p, org)
        assert held == truth
    await fold_all(pg_db, org)
    truth, held, unfolded = await state(p, org)
    assert held == truth and unfolded == 0
