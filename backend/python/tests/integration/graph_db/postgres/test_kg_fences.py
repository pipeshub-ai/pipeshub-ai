"""Hide first, restamp later: a change that outdates more stamps than fit in the writer's unit hides what it reaches in that same unit and queues the restamp.

Safety, in every state a test reaches: a stamp that differs from a fresh compile of the graph (``dry_diffs``) is never visible, that is hidden by a mark on its row or by a fenced job
of its connector (the reader's rule, ``restamp.HIDDEN``). Liveness: after the worker has run, no mark and no job is left and every stamp equals a fresh compile. A batch is all or nothing,
so a worker that dies leaves its marks in place; a claim is by compare and set on the heartbeat, in the session's organisation only.
"""
from __future__ import annotations

import random

import pytest

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import drainer, restamp
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.worker import KgWorker
from app.services.graph_db.postgres.kg.writers import KgWriter

from .pg_support import new_org
from .test_kg_backfill import run_in, shadowed
from .test_kg_derivation import ctx, edge, prov
from .test_kg_projection import add_edge, update_node
from .test_kg_projection_races import OPS, world2
from .test_kg_stamps import dry_diffs, held_stamps

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

HIDDEN_IDS = "SELECT k.id FROM kg.records r JOIN kg.node_keys k ON k.org_id = r.org_id AND k.sk = r.sk WHERE r.org_id = $1 AND " + restamp.HIDDEN


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")


async def fat(p, org: str, n: int = 12) -> None:
    """An open folder F with ``n`` records that inherit from it, in an app the user belongs to, and a team grant on F."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "OPEN"}
                                    for k in ["F", *[f"r{i:02d}" for i in range(n)]]], "records")
        kids = [f"r{i:02d}" for i in range(n)]
        await p.batch_create_edges([edge("F", "records", k, "records", relationshipType="PARENT_CHILD") for k in kids], "nodeRelations")
        await p.batch_create_edges([edge(k, "records", "F", "records") for k in kids], "inheritPermissions")
        await p.batch_create_edges([edge(k, "records", "app1", "apps") for k in ["F", *kids]], "belongsTo")
        await p.batch_create_edges([edge("u1", "users", "F", "records", type="USER", role="READER")], "permission")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")


async def narrow(p, org: str) -> None:
    await update_node(p, org, "records", "F", accessRule="RESTRICTED")


async def hidden_ids(p, org: str) -> set[str]:
    async def go(tx) -> set[str]:
        return {r["id"] for r in await tx.fetch(HIDDEN_IDS, org)}
    with ctx(org):
        return await p._q(go, None)


async def marks(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1 AND fence_job IS NOT NULL", org)
    with ctx(org):
        return await p._q(go, None)


async def jobs(db, org: str) -> list:
    async def go(tx) -> list:
        return await restamp.pending(tx, org)
    return await run_in(db, org, go)


def stale(diffs: list[str]) -> set[str]:
    return {d.split("|", 1)[1].split(" ")[0] for d in diffs}


async def no_stale_visible(p, db, org: str, app: str = "app1") -> set[str]:
    """Every stamp that differs from a fresh compile is hidden; returns them."""
    s, h = stale(await dry_diffs(p, org, app)), await hidden_ids(p, org)
    assert s <= h, sorted(s - h)
    return s


async def test_a_change_that_reaches_more_than_fits_hides_what_it_reaches_until_the_worker_has_restamped_it(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    assert await dry_diffs(p, org, "app1") == [] and await jobs(pg_db, org) == []
    await narrow(p, org)
    await drain_org(pg_db, org)
    got = await jobs(pg_db, org)
    assert [(j.kind, j.reason) for j in got] == [("rows", "reach")] and await marks(p, org) == 12
    outdated = await no_stale_visible(p, pg_db, org)
    assert outdated == {f"r{i:02d}" for i in range(12)}                         # the children's stamps are outdated, and all of them are hidden
    assert "F" not in await hidden_ids(p, org) and "F" not in outdated          # the folder was stamped in the writer's unit
    assert await restamp.run_jobs(pg_db, org, "w1", batch=5) == 1
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_change_that_fits_is_restamped_in_the_writers_unit_and_hides_nothing(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_INLINE_MAX", "100")
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


@pytest.mark.parametrize("seed", range(30))
async def test_whatever_the_changes_no_outdated_stamp_is_visible_and_the_worker_leaves_none_hidden(pg_db, seed: int) -> None:
    rng = random.Random(seed)
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    for op in [rng.choice(OPS) for _ in range(rng.randint(2, 6))]:
        await op(p, org)
        if seed % 2 == 0:
            await drain_org(pg_db, org)
            for app in ("app1", "app2"):
                await no_stale_visible(p, pg_db, org, app)
    await drain_org(pg_db, org)
    for app in ("app1", "app2"):
        await no_stale_visible(p, pg_db, org, app)
    await restamp.run_jobs(pg_db, org, "w1", batch=2)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == []
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == [], (seed, app)


async def test_a_connector_hidden_whole_is_queued_as_one_job_and_restamped_page_by_page(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await update_node(p, org, "apps", "app1", name="Drive", type="DRIVE", permissionModel="APP_LEVEL", orgId=org)       # what the stamps are compiled with changes: the whole connector
    await drain_org(pg_db, org)
    got = await jobs(pg_db, org)
    assert [(j.kind, j.reason, j.fenced) for j in got] == [("app", "app", True)]
    assert await hidden_ids(p, org) >= {"F", "r00", "r11"}                      # every record of the connector reads as hidden
    await no_stale_visible(p, pg_db, org)
    assert await restamp.run_jobs(pg_db, org, "w1", batch=4) == 1
    assert await jobs(pg_db, org) == [] and await hidden_ids(p, org) == set() and await dry_diffs(p, org, "app1") == []


async def test_a_connector_with_a_fenced_job_is_not_current_and_serves_nothing_until_the_job_is_done(pg_db) -> None:
    p, org = await shadowed(pg_db)
    await bf.promote(pg_db, org, "app1")

    async def ask(tx) -> tuple:
        return await drainer.is_current(tx, org, "app1"), await drainer.serves(tx, org, "app1")
    assert await run_in(pg_db, org, ask) == (True, True)

    async def queue(tx) -> None:
        await restamp.enqueue_app(tx, org, (await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)), "app")
    await run_in(pg_db, org, queue, write=True)
    assert await run_in(pg_db, org, ask) == (False, False)
    assert await restamp.run_jobs(pg_db, org, "w1") == 1
    assert await run_in(pg_db, org, ask) == (True, True)


async def test_a_flip_and_a_promotion_wait_for_the_queued_job(pg_db) -> None:
    p, org = await shadowed(pg_db)

    async def queue(tx) -> None:
        await restamp.enqueue_app(tx, org, (await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)), "app")
    await run_in(pg_db, org, queue, write=True)
    with pytest.raises(bf.Refused):
        await bf.promote(pg_db, org, "app1")
    await restamp.run_jobs(pg_db, org, "w1")
    await bf.promote(pg_db, org, "app1")


async def test_hiding_a_key_kg_holds_no_row_for_makes_no_row(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org, 2)
    await drain_org(pg_db, org)

    async def go(tx) -> tuple:
        n = await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1", org)
        hid = await restamp.enqueue_rows(tx, org, {("records", "never-projected")}, "reach")
        return hid, n, await tx.fetchval("SELECT count(*) FROM kg.records WHERE org_id = $1", org)
    hid, before, after = await run_in(pg_db, org, go, write=True)
    assert hid == 0 and before == after and await jobs(pg_db, org) == []


async def test_a_batch_that_fails_leaves_the_records_hidden_and_the_job_is_claimed_again(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    real = KgWriter.queue_touch
    calls = {"n": 0}

    async def flaky(self, *a: object, **k: object) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:                                  # the second batch's own touch (the claim and the first batch's touch came before)
            raise RuntimeError("boom")
        return await real(self, *a, **k)
    monkeypatch.setattr(KgWriter, "queue_touch", flaky)
    assert await restamp.run_jobs(pg_db, org, "w1", batch=5) == 0
    monkeypatch.setattr(KgWriter, "queue_touch", real)
    left = await marks(p, org)
    assert 0 < left < 12                                      # a batch is all or nothing: the first batch's rows are restamped, the failed one's are still hidden
    await no_stale_visible(p, pg_db, org)
    [job] = await jobs(pg_db, org)
    assert job.attempts == 1
    assert await restamp.claim(pg_db, org, "w2") is None       # w1's claim is fresh
    taken = await restamp.claim(pg_db, org, "w2", stale_ms=10, now=restamp.now_ms() + 60_000)
    assert taken is not None and taken.attempts == 2 and await restamp.run_job(pg_db, org, taken, "w2", batch=5)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_worker_that_lost_its_claim_changes_nothing(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    first = await restamp.claim(pg_db, org, "w1")
    assert first is not None
    second = await restamp.claim(pg_db, org, "w2", stale_ms=10, now=restamp.now_ms() + 60_000)      # w1 stopped heartbeating
    assert second is not None
    before = await marks(p, org)
    with pytest.raises(restamp.LostClaim):
        await restamp.run_batch(pg_db, org, first, "w1", batch=5)
    assert await marks(p, org) == before                                                         # the batch of the stale worker rolled back whole
    assert await restamp.run_job(pg_db, org, second, "w2", batch=5)
    assert await marks(p, org) == 0 and await dry_diffs(p, org, "app1") == []


async def test_a_job_that_finishes_while_a_newer_change_hid_the_same_records_leaves_them_hidden(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    [first] = await jobs(pg_db, org)
    await update_node(p, org, "records", "F", accessRule="OPEN")                          # a newer change reaches the same records
    await drain_org(pg_db, org)
    assert {j.id for j in await jobs(pg_db, org)} >= {first.id} and len(await jobs(pg_db, org)) == 2
    claimed = await restamp.claim(pg_db, org, "w1")
    assert claimed is not None and claimed.id == first.id
    assert await restamp.run_job(pg_db, org, claimed, "w1")                                # the older job has nothing left of its own: it ends at once
    assert await marks(p, org) == 12 and len(await jobs(pg_db, org)) == 1                  # the records are still hidden, by the newer job
    await no_stale_visible(p, pg_db, org)
    assert await restamp.run_jobs(pg_db, org, "w1") == 1
    assert await marks(p, org) == 0 and await dry_diffs(p, org, "app1") == []


async def test_a_change_waiting_in_the_log_keeps_the_connector_from_being_current_when_a_job_ends(pg_db) -> None:
    p, org = await shadowed(pg_db)

    async def queue(tx) -> None:
        await restamp.enqueue_app(tx, org, (await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)), "app")
    await run_in(pg_db, org, queue, write=True)
    await update_node(p, org, "records", "f1", recordName="renamed")                     # committed after the job was queued, not projected yet
    await restamp.run_jobs(pg_db, org, "w1")

    async def ask(tx) -> bool:
        return await drainer.is_current(tx, org, "app1")
    assert await run_in(pg_db, org, ask) is False
    await drain_org(pg_db, org)
    assert await run_in(pg_db, org, ask) is True


async def test_a_job_of_another_organisation_is_neither_claimed_nor_touched(pg_db) -> None:
    p, a, b = prov(pg_db), new_org(), new_org()
    for org in (a, b):
        await fat(p, org)
        await drain_org(pg_db, org)
        await narrow(p, org)
        await drain_org(pg_db, org)
    [job_b] = await jobs(pg_db, b)
    seen_a = (await jobs(pg_db, a))[0]
    assert seen_a.id != job_b.id and await jobs(pg_db, a) != await jobs(pg_db, b)
    took = await restamp.claim(pg_db, a, "wa")
    assert took is not None and took.id == seen_a.id                                        # org a's claim is org a's job

    async def steal(tx) -> bool:
        return await KgWriter(tx).queue_touch(job_b.id, job_b.heartbeat_ms, "wa", restamp.now_ms(), 1, None)
    assert await run_in(pg_db, a, steal, write=True) is False                               # b's job id, from a's session
    [after] = await jobs(pg_db, b)
    assert after.attempts == 0 and after.heartbeat_ms == job_b.heartbeat_ms
    assert await restamp.run_job(pg_db, a, took, "wa") and len(await jobs(pg_db, b)) == 1 and await marks(p, b) == 12


async def test_a_restamp_that_reaches_another_connector_takes_its_lock_and_goes_on(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 (app2) hangs under f2 (app1)
    await drain_org(pg_db, org)
    await update_node(p, org, "records", "f2", accessRule="RESTRICTED")
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=1)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == []
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == []


async def test_the_worker_restamps_the_queue_in_its_pass(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=3600, batch=5)
    assert await worker.run_once() >= 1
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_the_stamps_a_job_leaves_are_those_of_a_fresh_projection(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    other = new_org()
    await fat(p, other)
    await update_node(p, other, "records", "F", accessRule="RESTRICTED")
    await drain_org(pg_db, other)
    # the same graph, projected whole: the same stamps (the key of the organisation and the sks differ, the ids and the classes' clauses do not)
    got, want = await held_stamps(p, org), await held_stamps(p, other)
    assert {k: v for k, v in got.items() if not k.startswith("org")} == {k: v for k, v in want.items() if not k.startswith("org")}


async def test_hiding_records_of_a_connector_the_unit_does_not_hold_asks_for_its_lock_before_it_writes(pg_db, monkeypatch) -> None:
    from app.services.graph_db.postgres.kg.locks import NeedLocks
    from app.services.graph_db.postgres.kg.stamp import stamp
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 (app2) hangs under f2 (app1)
    await drain_org(pg_db, org)

    async def asks(tx) -> set:
        try:
            await stamp(tx, org, {("records", "fold")}, locked={"app1"})
        except NeedLocks as e:
            return e.apps
        return set()
    monkeypatch.setenv("PG_KG_INLINE_MAX", "0")
    with ctx(org):
        assert await pg_db.run(asks, write=True) == {"app2"}                 # the reach of the folder includes x1: raised before a mark or a job was written
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == []


async def test_a_whole_connector_hidden_leaves_no_outdated_stamp_visible_in_another_connector(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")
    await drain_org(pg_db, org)
    await update_node(p, org, "apps", "app1", name="Drive", type="DRIVE", permissionModel="APP_LEVEL", orgId=org)
    await drain_org(pg_db, org)
    for app in ("app1", "app2"):
        await no_stale_visible(p, pg_db, org, app)
    await restamp.run_jobs(pg_db, org, "w1", batch=2)
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == []
