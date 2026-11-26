"""Hide first, restamp later: a change that outdates more stamps than fit in the writer's unit hides what it reaches in that same unit and queues the restamp.

Safety, in every state a test reaches: a stamp that differs from a fresh compile of the graph (``dry_diffs``) is never visible, that is hidden by a mark on its row or by a fenced job
of its connector (the reader's rule, ``restamp.HIDDEN``). Liveness: after the worker has run, no mark and no job is left and every stamp equals a fresh compile. A batch is all or nothing,
so a worker that dies leaves its marks in place; a claim is by compare and set on the heartbeat, in the session's organisation only.
"""
from __future__ import annotations

import asyncio
import random

import pytest

from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import drainer, restamp
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.worker import KgWorker
from app.services.graph_db.postgres.kg.writers import KgWriter

from .pg_support import new_org
from .test_kg_backfill import run_in, shadowed
from .test_kg_derivation import ctx, edge, held, prov
from .test_kg_projection import add_edge, delete_edge, delete_node, update_node
from .test_kg_projection_races import OPS, drop_only, world2
from .test_kg_read import _decide, _switch, flip
from .test_kg_stamps import dry_diffs, held_scopes, held_stamps

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


HIDDEN_SCOPE_IDS = ("SELECT k.id FROM kg.scopes s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.sk WHERE s.org_id = $1 AND " + restamp.HIDDEN_SCOPE)


async def test_the_access_list_of_a_hidden_record_is_hidden_with_it_and_only_then(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await add_edge(p, org, ("users", "u1"), ("records", "r00"), "permission", type="USER", role="READER")        # two children get an access list of their own
    await add_edge(p, org, ("users", "u1"), ("records", "r01"), "permission", type="USER", role="READER")
    await drain_org(pg_db, org)

    async def hidden_scopes() -> set[str]:
        async def go(tx) -> set[str]:
            return {r["id"] for r in await tx.fetch(HIDDEN_SCOPE_IDS, org)}
        with ctx(org):
            return await p._q(go, None)
    assert {"r00", "r01"} <= set(await held_scopes(p, org)) and await hidden_scopes() == set()
    await narrow(p, org)
    await drain_org(pg_db, org)
    assert await marks(p, org) == 12
    assert await hidden_scopes() == {"r00", "r01"} and await hidden_scopes() == (await hidden_ids(p, org)) & set(await held_scopes(p, org))
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await hidden_scopes() == set() and await dry_diffs(p, org, "app1") == []


async def test_a_membership_change_of_a_member_of_the_connector_hides_nothing_and_queues_nothing(pg_db) -> None:
    """No stamp reads who is in a group or who links to a connector: a user who belongs to the connector joins and leaves a group, and only the closure changes."""
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await delete_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER")
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "grp", "orgId": org}], "groups")
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    for change, member in ((add_edge, True), (delete_edge, False)):
        await change(p, org, ("users", "u1"), ("groups", "grp"), "permission", type="USER")
        await drain_org(pg_db, org)
        assert await jobs(pg_db, org) == [] and await marks(p, org) == 0, change.__name__
        assert (("u1", "grp", "-") in (await held(p, org))["closure"]) is member
        assert await dry_diffs(p, org, "app1") == []


async def test_a_membership_change_of_a_user_with_a_grant_restamps_what_the_grant_reaches_never_the_connector_whole(pg_db) -> None:
    """The user also holds a grant on F, above twelve records. The change log cannot tell a membership from the user's node coming or going, so what the grant reaches is
    restamped as for a principal that came or went; the connector the user belongs to is not."""
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "grp", "orgId": org}], "groups")
    await drain_org(pg_db, org)
    for change in (add_edge, delete_edge):
        await change(p, org, ("users", "u1"), ("groups", "grp"), "permission", type="USER")
        await drain_org(pg_db, org)
        assert [j.kind for j in await jobs(pg_db, org)] in ([], ["rows"]), change.__name__
        await no_stale_visible(p, pg_db, org)
        await restamp.run_jobs(pg_db, org, "w1", batch=5)
        assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_user_whose_node_goes_with_its_edges_left_hides_what_its_grant_reached_until_restamped(pg_db) -> None:
    """The other side of the membership rule: the user's existence did change, so the folder it alone was granted is no longer granted and the records below it are outdated."""
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    for change in (drop_only("users", "u1"), lambda p, o: update_node(p, o, "users", "u1", orgId=o, email="u@example.com")):
        await change(p, org)
        await drain_org(pg_db, org)
        assert await jobs(pg_db, org) != []
        assert await no_stale_visible(p, pg_db, org) >= {f"r{i:02d}" for i in range(12)}
        await restamp.run_jobs(pg_db, org, "w1", batch=5)
        assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def ungranted_folder(p, pg_db, org: str) -> None:
    """``fat`` with the user's grant on F taken away and a grant to F from the team t9 written before t9 exists: nothing grants F, and kg agrees."""
    await fat(p, org)
    await delete_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER")
    await add_edge(p, org, ("teams", "t9"), ("records", "F"), "permission", type="TEAM", role="READER")
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_grant_a_principal_copy_wrote_does_not_stand_for_the_projection_of_the_node_it_is_on(pg_db) -> None:
    """t9 appears and the copy of the organisation's principals runs before the drain: it writes t9's grant on F without projecting F. When the drain then projects t9, the grant
    row agrees with t9, yet F was never projected with t9 there: F (granted now) and the records below it must still be restamped, and nothing outdated may be visible meanwhile."""
    p, org = prov(pg_db), new_org()
    await ungranted_folder(p, pg_db, org)
    await update_node(p, org, "teams", "t9", orgId=org)
    assert await bf.backfill_scope(pg_db, org, None) > 0
    await drain_org(pg_db, org)
    assert await no_stale_visible(p, pg_db, org) >= {f"r{i:02d}" for i in range(12)}
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_principal_that_goes_before_a_copy_of_the_principals_still_hides_what_its_grant_reached(pg_db) -> None:
    """The mirror: t9 alone grants F, then t9's node goes (its edges stay) and the principal copy runs before the drain. Nothing outdated may stay visible: F and the records
    below it are no longer granted."""
    p, org = prov(pg_db), new_org()
    await ungranted_folder(p, pg_db, org)
    await update_node(p, org, "teams", "t9", orgId=org)
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await dry_diffs(p, org, "app1") == []
    await drop_only("teams", "t9")(p, org)
    await bf.backfill_scope(pg_db, org, None)
    await drain_org(pg_db, org)
    assert await no_stale_visible(p, pg_db, org) >= {f"r{i:02d}" for i in range(12)}
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_principal_that_goes_and_comes_back_before_the_drain_leaves_no_stamp_compiled_without_it(pg_db) -> None:
    """t9 alone grants F. Its node goes (edges stay), the connector is stamped page by page while t9 is gone (F not granted), and t9 comes back before the drain: its grant
    row agrees with the graph again, but the stamps compiled in between do not, and they must be restamped."""
    p, org = prov(pg_db), new_org()
    await ungranted_folder(p, pg_db, org)
    await update_node(p, org, "teams", "t9", orgId=org)
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await dry_diffs(p, org, "app1") == []
    await drop_only("teams", "t9")(p, org)
    assert await bf.restamp_app(pg_db, org, "app1") > 0
    await update_node(p, org, "teams", "t9", orgId=org)
    await drain_org(pg_db, org)
    assert await no_stale_visible(p, pg_db, org) >= {f"r{i:02d}" for i in range(12)}
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
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


async def test_a_rows_job_keeps_its_connector_served_and_the_reader_hands_exactly_the_rows_it_hides_to_the_walk(pg_db) -> None:
    """Only a job that hides the connector whole takes it off the readers. A rows job hides the rows it marks, and those are the ones whose stamps are outdated: the reader
    takes each of them to the walk, decides the others from their stamps, and answers what the walk answers, before the job and after it."""
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    assert (await flip(pg_db, org))[0] == 1

    async def ask(tx) -> tuple:
        return await drainer.is_current(tx, org, "app1"), await drainer.serves(tx, org, "app1")
    ids = ["F", *[f"r{i:02d}" for i in range(12)]]

    async def answers(switch: str | None) -> set[str]:
        with _switch(switch), ctx(org):
            access = await p.get_knowledge_hub_access_v3("u1", org)
            return {r["id"] for r in await p._kh_v3_accessible_rows("u1", org, ids, [], access=access, transaction=None)}
    assert await answers(None) == await answers("0") == set(ids)
    await narrow(p, org)
    await drain_org(pg_db, org)
    assert [j.kind for j in await jobs(pg_db, org)] == ["rows"] and await marks(p, org) == 12
    assert await run_in(pg_db, org, ask) == (True, True)
    with ctx(org):
        access = await p.get_knowledge_hub_access_v3("u1", org)
    want = await answers("0")
    assert want < set(ids)                                                      # the narrowing shut records whose outdated stamps still open them
    d = await _decide(pg_db, org, ids, access)
    assert set(d.walk) == await hidden_ids(p, org) == stale(await dry_diffs(p, org, "app1")) == set(ids) - {"F"}
    assert d.admit | d.deny == {"F"} and d.admit == want & {"F"}
    assert await answers(None) == want
    assert await restamp.run_jobs(pg_db, org, "w1", batch=5) == 1
    d = await _decide(pg_db, org, ids, access)
    assert d.walk == [] and d.admit == want and await answers(None) == want


async def test_a_job_that_hides_the_whole_connector_still_takes_it_off_the_readers(pg_db) -> None:
    p, org = await shadowed(pg_db)
    await bf.promote(pg_db, org, "app1")

    async def ask(tx) -> tuple:
        return await drainer.is_current(tx, org, "app1"), await drainer.fenced(tx, org, "app1")

    async def mark(tx) -> None:
        await restamp.enqueue_rows(tx, org, {("records", "f1")}, "reach", org_wide=True)
    await run_in(pg_db, org, mark, write=True)
    assert await run_in(pg_db, org, ask) == (True, {"rows"})

    async def queue(tx) -> None:
        await restamp.enqueue_app(tx, org, (await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)), "app")
    await run_in(pg_db, org, queue, write=True)
    assert await run_in(pg_db, org, ask) == (False, {"rows", "app"})
    assert await restamp.run_jobs(pg_db, org, "w1") == 2
    assert await run_in(pg_db, org, ask) == (True, set())


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
    await asyncio.sleep(0.1)                                    # w1's claim goes stale by the database's clock
    taken = await restamp.claim(pg_db, org, "w2", stale_ms=10)
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
    await asyncio.sleep(0.1)
    second = await restamp.claim(pg_db, org, "w2", stale_ms=10)      # w1 stopped heartbeating
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
        return await KgWriter(tx).queue_touch(job_b.id, job_b.heartbeat_ms, "wa", 1, None)
    assert await run_in(pg_db, a, steal, write=True) is None                               # b's job id, from a's session
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


async def test_a_connector_job_whose_pages_reach_another_connector_takes_its_lock_and_never_runs_without_locks(pg_db, monkeypatch) -> None:
    from app.services.graph_db.postgres.kg import stamp as stamp_mod
    from app.services.graph_db.postgres.kg.locks import NeedLocks
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "x1"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD")      # f1 (app1) hangs under x1 (app2)
    await drain_org(pg_db, org)

    async def queue(tx) -> None:
        await restamp.enqueue_app(tx, org, (await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)), "app")
    await run_in(pg_db, org, queue, write=True)
    seen: list = []
    real = stamp_mod.stamp_app

    async def spy(tx: object, o: str, app_id: str, after: str | None, limit: int, st: object = None, *, dry: bool = False, locked: set[str] | None = None) -> str | None:
        try:
            return await real(tx, o, app_id, after, limit, st, dry=dry, locked=locked)
        except NeedLocks as e:
            seen.append((locked, set(e.apps)))
            raise
        finally:
            seen.append((locked, None))
    monkeypatch.setattr(stamp_mod, "stamp_app", spy)
    assert await restamp.run_jobs(pg_db, org, "w1", batch=2) == 1
    assert all(locked is not None for locked, _ in seen)                          # the job always says which connectors it holds
    assert any(apps == {"app2"} for _, apps in seen if apps is not None)         # the page that reached app2 asked for it, before it wrote
    assert await jobs(pg_db, org) == []
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == []


async def test_a_stopped_worker_returns_between_batches_while_jobs_keep_coming_and_what_it_left_is_still_hidden(pg_db) -> None:
    import time
    p, org = prov(pg_db), new_org()
    await fat(p, org, 30)
    await drain_org(pg_db, org)
    running = True

    async def producer() -> None:
        # a steady producer: the folder narrows and widens again and again, each change reaching its 30 records, each projected at once
        flip = False
        while running:
            await update_node(p, org, "records", "F", accessRule="OPEN" if flip else "RESTRICTED")
            await drain_org(pg_db, org)
            flip = not flip

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=0.05, batch=1)
    task = asyncio.get_running_loop().create_task(producer())
    worker.start()
    await asyncio.sleep(2)
    t0 = time.monotonic()
    await asyncio.wait_for(worker.stop(), 30)
    stopped_in = time.monotonic() - t0
    running = False
    await asyncio.wait_for(task, 30)
    assert stopped_in < 10, stopped_in                                  # a batch of one record, not the queue
    left_marks, left_jobs = await marks(p, org), await jobs(pg_db, org)
    assert (left_marks == 0) or left_jobs                                # whatever it left hidden has a job that will restamp it
    await no_stale_visible(p, pg_db, org)
    assert await restamp.run_jobs(pg_db, org, "w2", batch=50) >= 0           # a stopped worker gave its claim back: the job is claimable at once, whatever it was doing
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def claimed_by(db, org: str) -> list:
    return await run_in(db, org, lambda tx: tx.fetch("SELECT id, claimed_by, cursor FROM kg.restamp_queue WHERE org_id = $1 ORDER BY id", org))


async def one_job_hidden(p, db, org: str) -> None:
    """A narrowed folder: one queued ``rows`` job hiding its 12 children."""
    await fat(p, org)
    await drain_org(db, org)
    await narrow(p, org)
    await drain_org(db, org)
    assert len(await jobs(db, org)) == 1 and await marks(p, org) == 12


async def test_a_worker_that_stops_mid_job_gives_its_claim_back_and_another_worker_finishes_the_job_at_once(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await one_job_hidden(p, pg_db, org)
    stop = asyncio.Event()
    real = restamp.run_batch

    async def stop_after_the_first_batch(*a: object, **k: object) -> tuple:
        out = await real(*a, **k)
        stop.set()
        return out
    monkeypatch.setattr(restamp, "run_batch", stop_after_the_first_batch)
    job = await restamp.claim(pg_db, org, "w1")
    assert await restamp.run_job(pg_db, org, job, "w1", batch=3, stop=stop) is False
    monkeypatch.setattr(restamp, "run_batch", real)
    left = await claimed_by(pg_db, org)
    assert [(r["claimed_by"], r["cursor"] is not None) for r in left] == [(None, True)]       # the claim is back, the cursor of the committed batch stays
    assert 0 < await marks(p, org) < 12 and await no_stale_visible(p, pg_db, org)             # what is not restamped yet is still hidden
    assert await restamp.run_jobs(pg_db, org, "w2", batch=50) == 1                           # the default staleness: no 120 s wait
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []     # the hidden rows are back, and current


async def test_a_worker_that_stops_right_after_claiming_gives_the_job_back_before_any_batch(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await one_job_hidden(p, pg_db, org)
    stop = asyncio.Event()
    job = await restamp.claim(pg_db, org, "w1")
    stop.set()
    assert await restamp.run_job(pg_db, org, job, "w1", stop=stop) is False
    assert [r["claimed_by"] for r in await claimed_by(pg_db, org)] == [None] and await marks(p, org) == 12
    assert await restamp.run_jobs(pg_db, org, "w2") == 1 and await marks(p, org) == 0


async def test_a_worker_cannot_give_back_a_claim_another_worker_holds(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await one_job_hidden(p, pg_db, org)
    job = await restamp.claim(pg_db, org, "w1")
    assert await restamp.release(pg_db, org, job, "w2") is False
    assert [r["claimed_by"] for r in await claimed_by(pg_db, org)] == ["w1"]
    assert await restamp.run_jobs(pg_db, org, "w3") == 0                                      # w1's claim is live
    assert await restamp.release(pg_db, org, job, "w1") is True and await restamp.release(pg_db, org, job, "w1") is False


async def test_a_worker_whose_stale_claim_was_taken_over_cannot_give_the_new_claim_back(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await one_job_hidden(p, pg_db, org)
    old = await restamp.claim(pg_db, org, "w1")
    await asyncio.sleep(0.01)                                                                  # stale means a heartbeat older than now - stale_ms, so not in the same millisecond
    assert await restamp.claim(pg_db, org, "w2", stale_ms=0) is not None                       # w1 went quiet: w2 takes the job over
    assert await restamp.release(pg_db, org, old, "w1") is False                              # w1 stops late and gives back nothing
    assert [r["claimed_by"] for r in await claimed_by(pg_db, org)] == ["w2"]


async def test_a_claim_that_was_not_given_back_is_still_taken_only_once_it_is_stale(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await one_job_hidden(p, pg_db, org)
    await restamp.claim(pg_db, org, "w1")                                                    # a crash: nothing runs, nothing is released
    assert await restamp.run_jobs(pg_db, org, "w2") == 0 and await marks(p, org) == 12
    assert await restamp.run_jobs(pg_db, org, "w2", stale_ms=0) == 1 and await marks(p, org) == 0


async def test_a_rows_job_whose_records_reach_another_connector_takes_its_lock_and_never_runs_without_locks(pg_db, monkeypatch) -> None:
    from app.services.graph_db.postgres.kg import stamp as stamp_mod
    from app.services.graph_db.postgres.kg.locks import NeedLocks
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "x1"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD")      # f1 (app1) hangs under x1 (app2)
    await drain_org(pg_db, org)

    async def queue(tx) -> None:
        await restamp.enqueue_rows(tx, org, {("records", "f1"), ("records", "fold")}, "reach")
    await run_in(pg_db, org, queue, write=True)
    assert await marks(p, org) == 2
    seen: list = []
    real = stamp_mod.stamp_keys

    async def spy(tx: object, o: str, keys: set, *a: object, **k: object) -> object:
        try:
            return await real(tx, o, keys, *a, **k)
        except NeedLocks as e:
            seen.append((k.get("locked"), set(e.apps)))
            raise
        finally:
            seen.append((k.get("locked"), None))
    monkeypatch.setattr(stamp_mod, "stamp_keys", spy)
    assert await restamp.run_jobs(pg_db, org, "w1", batch=5) == 1
    assert all(locked is not None for locked, _ in seen)                          # the job always says which connectors it holds
    assert any(apps == {"app2"} for _, apps in seen if apps is not None)         # the batch that reached app2 asked for it, before it wrote
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == []
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == []


async def test_hiding_records_of_no_connector_asks_for_the_whole_organisation_before_it_writes(pg_db) -> None:
    from app.services.graph_db.postgres.kg.locks import NeedLocks
    p, org = prov(pg_db), new_org()
    await fat(p, org, 2)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "orphan", "recordName": "orphan", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"}], "records")      # no connectorId
    await drain_org(pg_db, org)

    async def go(tx) -> tuple:
        no_app = await tx.fetchval("SELECT count(*) FROM kg.records r JOIN kg.node_keys k ON k.org_id = r.org_id AND k.sk = r.sk WHERE r.org_id = $1 AND k.id = 'orphan' AND r.app_sk IS NULL", org)
        try:
            await restamp.enqueue_rows(tx, org, {("records", "orphan")}, "reach", locked={"app1"})
        except NeedLocks as e:
            return no_app, e.wide
        return no_app, None
    assert await run_in(pg_db, org, go, write=True) == (1, True)                         # a record of no connector has no lock of its own: the unit asks for the organisation's
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == []


async def test_a_batch_slower_than_the_staleness_keeps_its_claim_by_heartbeat(pg_db, monkeypatch) -> None:
    import asyncio

    from app.services.graph_db.postgres.kg import stamp as stamp_mod
    p, org = prov(pg_db), new_org()
    await fat(p, org, 4)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    real = stamp_mod.stamp_keys

    async def slow(*a: object, **k: object) -> object:
        await asyncio.sleep(1.5)                                  # five times the staleness below
        return await real(*a, **k)
    monkeypatch.setattr(stamp_mod, "stamp_keys", slow)
    job = await restamp.claim(pg_db, org, "w1", stale_ms=300)
    assert job is not None
    task = asyncio.get_running_loop().create_task(restamp.run_batch(pg_db, org, job, "w1", batch=50, stale_ms=300))
    await asyncio.sleep(0.9)
    assert await restamp.claim(pg_db, org, "w2", stale_ms=300) is None                     # w1's heartbeat has kept the claim fresh while its batch runs
    done, _ = await asyncio.wait_for(task, 30)
    assert done and await marks(p, org) == 0


async def test_a_row_whose_node_is_gone_keeps_its_mark_until_the_change_that_removes_it_has_been_projected(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    assert await marks(p, org) == 12
    await delete_node(p, org, "records", "r05")                                  # deleted in the graph, not projected yet: kg still holds its outdated stamp
    assert await restamp.run_jobs(pg_db, org, "w1", batch=5) == 0                 # the job cannot finish: one row cannot be restamped
    assert await marks(p, org) == 1 and "r05" in await hidden_ids(p, org)         # every other row was restamped and unhidden; the one that was not is still hidden
    assert len(await jobs(pg_db, org)) == 1
    await drain_org(pg_db, org)                                                   # the delete is projected: the row goes, and its mark with it
    await asyncio.sleep(0.1)
    assert await restamp.run_jobs(pg_db, org, "w2", batch=5, stale_ms=10) == 1
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []


async def test_a_worker_with_a_skewed_clock_neither_steals_a_live_claim_nor_blocks_recovery(pg_db, monkeypatch) -> None:
    import time
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await narrow(p, org)
    await drain_org(pg_db, org)
    real = time.time
    live = await restamp.claim(pg_db, org, "w1", stale_ms=60_000)
    assert live is not None
    monkeypatch.setattr(time, "time", lambda: real() + 10 * 86400)             # a worker whose clock is ten days ahead
    assert await restamp.claim(pg_db, org, "w2", stale_ms=60_000) is None       # it cannot take a live claim
    monkeypatch.setattr(time, "time", real)
    await asyncio.sleep(0.1)
    skewed = await restamp.claim(pg_db, org, "w1", stale_ms=10)                 # w1 lets go; the next claim is made by the skewed worker
    monkeypatch.setattr(time, "time", lambda: real() + 10 * 86400)
    assert skewed is not None
    [job] = await jobs(pg_db, org)
    db_now = await run_in(pg_db, org, lambda tx: tx.fetchval("SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint"))
    assert abs(job.heartbeat_ms - db_now) < 60_000                              # the heartbeat is the database's now, not ten days ahead
    monkeypatch.setattr(time, "time", real)
    await asyncio.sleep(0.1)
    again = await restamp.claim(pg_db, org, "w3", stale_ms=10)                  # so the job can be taken over once it is stale
    assert again is not None


# ---- a grant that leaves its node granted (kg_0021) -------------------------------------------------------------------------------------------------------------------

IDS = ["F", *[f"r{i:02d}" for i in range(12)]]
U2 = ("users", "u2")


async def served_fat(p, pg_db, org: str) -> None:
    """``fat`` with a second user u2 of the connector who holds no grant, copied, verified and enforced."""
    await fat(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "u2", "orgId": org, "email": "v@example.com"}], "users")
        await p.batch_create_edges([edge("u2", "users", "app1", "apps")], "userAppRelation")
    assert (await flip(pg_db, org))[0] == 1


async def answers(p, org: str, user: str, switch: str | None) -> set[str]:
    with _switch(switch), ctx(org):
        access = await p.get_knowledge_hub_access_v3(user, org)
        return {r["id"] for r in await p._kh_v3_accessible_rows(user, org, IDS, [], access=access, transaction=None)}


async def kg_walks(p, pg_db, org: str, user: str) -> list[str]:
    """The records the reader hands to the walk for ``user`` instead of deciding them from their stamps."""
    with ctx(org):
        access = await p.get_knowledge_hub_access_v3(user, org)
    return sorted((await _decide(pg_db, org, IDS, access)).walk)


def stamped(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every projection's stamping from here on: what it stamped (``keys``) and what it hid (``hidden``)."""
    from app.services.graph_db.postgres.kg import project as kg_project
    seen: list = []
    real = kg_project.stamp

    async def spy(*args: object, **kwargs: object) -> object:
        st = await real(*args, **kwargs)
        seen.append(st)
        return st
    monkeypatch.setattr(kg_project, "stamp", spy)
    return seen


async def test_a_grant_on_a_node_that_stays_granted_restamps_that_node_alone_and_its_connector_keeps_answering_from_kg(pg_db, monkeypatch) -> None:
    """F is granted to u1. Sharing F with u2 too, and taking it back, leaves F granted: no stamp below F can change, so F alone is restamped in the writer's unit, nothing is
    hidden or queued, every record of the connector is still decided from its stamp, and u2's answer is the walk's at every step (the share opens all thirteen, the revoke none)."""
    p, org = prov(pg_db), new_org()
    await served_fat(p, pg_db, org)
    assert await answers(p, org, "u2", None) == await answers(p, org, "u2", "0") == set()
    seen = stamped(monkeypatch)
    for change, want in ((add_edge, set(IDS)), (delete_edge, set())):
        seen.clear()
        await change(p, org, U2, ("records", "F"), "permission", type="USER", role="READER")
        await drain_org(pg_db, org)
        assert await jobs(pg_db, org) == [] and await marks(p, org) == 0, change.__name__
        assert {k for st in seen for k in st.keys} == {("records", "F")} and sum(st.hidden for st in seen) == 0, change.__name__
        assert await kg_walks(p, pg_db, org, "u2") == []
        assert await answers(p, org, "u2", None) == await answers(p, org, "u2", "0") == want, change.__name__
        assert await answers(p, org, "u1", None) == set(IDS)
        assert await dry_diffs(p, org, "app1") == []


async def test_a_grant_change_on_a_connector_kg_has_not_verified_restamps_what_is_below_as_before(pg_db, monkeypatch) -> None:
    """Below a copy that is not verified the stamps were compiled from the graph, and kg's rows may differ from it with nothing in the log: a grant change there restamps everything
    below the node."""
    p, org = prov(pg_db), new_org()
    await fat(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, U2, ("records", "F"), "permission", type="USER", role="READER")
    await drain_org(pg_db, org)
    assert [j.kind for j in await jobs(pg_db, org)] == ["rows"] and await marks(p, org) == 12
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await dry_diffs(p, org, "app1") == []


async def test_taking_away_the_last_grant_on_a_node_still_restamps_everything_below_it(pg_db) -> None:
    """The revoke that flips F to not granted outdates the twelve stamps below it: they are hidden in the same unit and restamped by the worker, and u1 loses them at once."""
    p, org = prov(pg_db), new_org()
    await served_fat(p, pg_db, org)
    await delete_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER")
    await drain_org(pg_db, org)
    assert [j.kind for j in await jobs(pg_db, org)] == ["rows"] and await marks(p, org) == 12
    assert await no_stale_visible(p, pg_db, org) == {f"r{i:02d}" for i in range(12)}
    assert await answers(p, org, "u1", None) == await answers(p, org, "u1", "0") == set()
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await marks(p, org) == 0 and await dry_diffs(p, org, "app1") == []
    assert await answers(p, org, "u1", None) == set()


async def test_a_stamp_compiled_while_a_grant_change_waits_in_the_log_reads_kgs_flag_so_the_node_alone_is_restamped_when_it_comes(pg_db) -> None:
    """A-B-A: u1's grant, F's only one, goes and comes back; in between, before the drain, the connector is restamped page by page. Those stamps are compiled with kg's flag
    (still granted), not the graph's (not granted), so when the drain finds F granted as kg held it and restamps F alone, no stamp below F is left outdated."""
    p, org = prov(pg_db), new_org()
    await served_fat(p, pg_db, org)
    await delete_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER")
    assert await bf.restamp_app(pg_db, org, "app1") > 0
    await add_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER", role="READER")
    await drain_org(pg_db, org)
    assert await jobs(pg_db, org) == [] and await marks(p, org) == 0
    assert await dry_diffs(p, org, "app1") == []
    assert await answers(p, org, "u1", None) == await answers(p, org, "u1", "0") == set(IDS)


async def test_a_node_whose_flag_a_childs_projection_flips_first_has_everything_below_it_restamped(pg_db) -> None:
    """u1's grant, F's only one, goes, and a new record c is put under F; c's rows are drained first, and with them F's row (its leaf flag) is rewritten from the graph, so kg's
    flag flips there. Everything below F is restamped then, and F's own grant change, drained next, finds the flag as kg holds it and restamps F alone."""
    p, org = prov(pg_db), new_org()
    await served_fat(p, pg_db, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "c", "recordName": "c", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "OPEN"}], "records")
        await p.batch_create_edges([edge("F", "records", "c", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("c", "records", "F", "records")], "inheritPermissions")
        await p.batch_create_edges([edge("c", "records", "app1", "apps")], "belongsTo")
    await delete_edge(p, org, ("users", "u1"), ("records", "F"), "permission", type="USER")
    while True:                                                                  # a page of one row: c's node first, F's grant last
        with ctx(org):
            if not await drainer.drain(pg_db, org, limit=1):
                break
        await no_stale_visible(p, pg_db, org)
    assert await marks(p, org) >= 12
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await marks(p, org) == 0 and await jobs(pg_db, org) == [] and await dry_diffs(p, org, "app1") == []
    assert await answers(p, org, "u1", None) == await answers(p, org, "u1", "0") == set()


async def test_a_change_of_a_node_waiting_behind_a_grant_change_on_it_is_not_taken_with_the_grant(pg_db) -> None:
    """u2's grant on F, then F narrowed, both committed before a drain of one row: the page projects the grant (F stays granted, F alone is restamped), and the narrowing behind it
    names F too. It is not acknowledged with the page, since what is below F was not restamped: the next page takes it and hides the twelve records below F."""
    p, org = prov(pg_db), new_org()
    await served_fat(p, pg_db, org)
    await add_edge(p, org, U2, ("records", "F"), "permission", type="USER", role="READER")
    await narrow(p, org)
    with ctx(org):
        assert await drainer.drain(pg_db, org, limit=1) == 1
    assert await drainer.lag(pg_db, org) == 1
    await drain_org(pg_db, org)
    assert await marks(p, org) == 12 and await no_stale_visible(p, pg_db, org) == {f"r{i:02d}" for i in range(12)}
    await restamp.run_jobs(pg_db, org, "w1", batch=5)
    assert await dry_diffs(p, org, "app1") == []
    for user in ("u1", "u2"):
        assert await answers(p, org, user, None) == await answers(p, org, user, "0"), user
