"""A change whose reach is too large for the writer's unit to find one by one: the unit hides the connector whole, marks the part it found, and a subtree job walks down from
its marks and restamps them in bounded batches (restamp.enqueue_subtree, restamp._walk).

Safety at every batch boundary: the reader answers what the walk answers for every user, and nothing whose stamp is outdated is answered from it. Liveness: once the job is done no
mark and no job is left, the connector is served again and every stamp equals a fresh compile. A batch commits whole or not at all, so a job interrupted at any point goes on
from its last batch; a second change of that size joins the job and sends it back to the start of its walk.
"""
from __future__ import annotations

import json

import pytest

from app.services.graph_db.postgres.kg import drainer, restamp, stamp
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.reader import seeks
from app.services.graph_db.postgres.kg.writers import KgWriter
from tests.support import plan_gate

from .pg_support import new_org
from .test_kg_backfill import run_in
from .test_kg_derivation import ctx, edge, prov
from .test_kg_fences import marks
from .test_kg_projection import add_edge, delete_edge
from .test_kg_read import _decide, _switch, flip
from .test_kg_stamps import dry_diffs

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
FOLDERS, SUBS, FILES = 3, 2, 8                    # G > f<i> > f<i>s<j> > f<i>s<j>r<k>: 3 + 6 + 48 records below G, besides the extra links
TIMEOUT = "2s"


def _rec(org: str, key: str) -> dict:
    return {"_key": key, "recordName": key, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "OPEN"}


def _tree() -> list[tuple[str, str]]:
    """(parent, child) of the hierarchy below the record group G."""
    out = []
    for i in range(FOLDERS):
        out.append(("G", f"f{i}"))
        for j in range(SUBS):
            out.append((f"f{i}", f"f{i}s{j}"))
            out += [(f"f{i}s{j}", f"f{i}s{j}r{k}") for k in range(FILES)]
    return out


async def world(p, pg_db, org: str, *, u2_reads: bool = False) -> list[str]:
    """app1 with a record group G and the tree below it, every record inheriting from its parent; nothing grants G, or (``u2_reads``) a grant of u2 alone. u1 is in the group grp,
    u2 is not. Besides the tree:
    ``early`` gets its key before anything else and is then placed under the last subfolder (a row below the walk's position when it is reached), and one file has a second parent
    (a link). Copied, verified and enforced. Returns the ids of G and of every record."""
    tree = _tree()
    recs = [c for _, c in tree]
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("u1", "u2")], "users")
        await p.batch_upsert_nodes([_rec(org, "early")], "records")
        await p.batch_create_edges([edge("early", "records", "app1", "apps")], "belongsTo")
    await drain_org(pg_db, org)
    last = f"f{FOLDERS - 1}s{SUBS - 1}"
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "grp", "orgId": org}], "groups")
        await p.batch_upsert_nodes([{"_key": "G", "groupName": "G", "connectorId": "app1", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([_rec(org, k) for k in recs], "records")
        await p.batch_create_edges([edge("app1", "apps", "G", "recordGroups", relationshipType="PARENT_CHILD"),
                                    *(edge(a, "recordGroups" if a == "G" else "records", c, "records", relationshipType="PARENT_CHILD") for a, c in tree),
                                    edge(last, "records", "early", "records", relationshipType="PARENT_CHILD"),
                                    edge("f1", "records", "f0s0r0", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([*(edge(c, "records", a, "recordGroups" if a == "G" else "records") for a, c in tree),
                                    edge("early", "records", last, "records")], "inheritPermissions")
        await p.batch_create_edges([edge("G", "recordGroups", "app1", "apps"), *(edge(c, "records", "G", "recordGroups") for c in recs)], "belongsTo")
        await p.batch_create_edges([edge("u1", "users", "grp", "groups", type="USER"),
                                    *([edge("u2", "users", "G", "recordGroups", type="USER", role="READER")] if u2_reads else [])], "permission")
        await p.batch_create_edges([edge(u, "users", "app1", "apps") for u in ("u1", "u2")], "userAppRelation")
    assert (await flip(pg_db, org))[0] == 1
    return ["G", "early", *recs]


async def share(p, org: str) -> None:
    """grp, of which u1 is a member, is granted G: the first grant on G, so every class below it changes."""
    await add_edge(p, org, ("groups", "grp"), ("recordGroups", "G"), "permission", type="GROUP", role="READER")


async def unshare(p, org: str) -> None:
    await delete_edge(p, org, ("groups", "grp"), ("recordGroups", "G"), "permission", type="GROUP")


async def answers(p, org: str, user: str, ids: list[str], switch: str | None) -> set[str]:
    with _switch(switch), ctx(org):
        access = await p.get_knowledge_hub_access_v3(user, org)
        return {r["id"] for r in await p._kh_v3_accessible_rows(user, org, ids, [], access=access, transaction=None)}


async def parity(p, pg_db, org: str, ids: list[str], want: dict[str, set[str]]) -> None:
    """The reader answers what the walk answers, and what each user may read, for both users."""
    for user, expect in want.items():
        kg, walk = await answers(p, org, user, ids, None), await answers(p, org, user, ids, "0")
        assert kg == walk == expect, (user, sorted(kg ^ expect), sorted(walk ^ expect))


async def jobs(pg_db, org: str) -> list[restamp.Job]:
    return await run_in(pg_db, org, lambda tx: restamp.pending(tx, org))


async def serving(pg_db, org: str) -> bool:
    return await run_in(pg_db, org, lambda tx: drainer.serves(tx, org, "app1"))


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounds small enough for the world above: a reach of more than 10 records is walked by a job, a page of the walk is 7 rows, and a row with more than 3 children by one index
    is walked on its own."""
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    monkeypatch.setattr(stamp, "REACH_MAX", 10)
    monkeypatch.setattr(stamp, "WALK_STEP", 4)
    monkeypatch.setattr(restamp, "WALK_PAGE", 7)
    monkeypatch.setattr(restamp, "WALK_FEW", 3)


@pytest.fixture
def bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every projection's statements held to TIMEOUT, as a unit under the product's statement budget is."""
    project = drainer.project

    async def go(tx, *args, **kwargs) -> object:
        await tx.fetchval("/* pipeshub:test.statement_timeout */ SELECT set_config('statement_timeout', $1, true)", TIMEOUT)
        return await project(tx, *args, **kwargs)
    monkeypatch.setattr(drainer, "project", go)


async def batches(p, pg_db, org: str, ids: list[str], want: dict[str, set[str]], worker: str, *, n: int | None = None, size: int = 5) -> list[str | None]:
    """Claims the subtree job and runs it a batch at a time (``n`` batches, or to the end), checking the reader against the walk after every one. Returns the cursors seen."""
    job = await restamp.claim(pg_db, org, worker, stale_ms=0)
    assert job is not None and job.reason == restamp.SUBTREE
    seen = []
    while n is None or len(seen) < n:
        done, job = await restamp.run_batch(pg_db, org, job, worker, batch=size)
        seen.append(job.cursor)
        await parity(p, pg_db, org, ids, want)
        if done:
            break
    return seen


async def settled(p, pg_db, org: str, ids: list[str], want: dict[str, set[str]]) -> None:
    """Nothing left: no job, no mark, the connector served from stamps that equal a fresh compile, and the reader decides every record from them."""
    assert await jobs(pg_db, org) == [] and await marks(p, org) == 0
    assert await dry_diffs(p, org, "app1") == []
    assert await serving(pg_db, org)
    for user in want:
        with ctx(org):
            access = await p.get_knowledge_hub_access_v3(user, org)
        assert (await _decide(pg_db, org, ids, access)).walk == [], user
    await parity(p, pg_db, org, ids, want)


@pytest.mark.parametrize("change", ["share", "member"])
async def test_a_group_change_over_a_subtree_too_large_for_the_unit_is_walked_by_a_job_in_bounded_batches_never_a_whole_connector_restamp(pg_db, small, bounded, change) -> None:
    """``share``: grp is granted G, which nothing granted, so every class below G changes and u1 (in grp) may read it all. ``member``: u2, who holds a grant on G, joins grp; the
    change log cannot tell that from u2's node coming or going, so everything u2's grant reaches is restamped. Either way the drain unit, its statements held to 2 s, finds only a
    bounded part of the reach, hides the connector and queues one subtree job (no whole-connector restamp, no rows job of the whole reach). The job walks down page by page and row
    by row, and after every batch the reader answers what the walk does. (Before, the unit found the whole reach and marked every row of it in that one unit.)"""
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org, u2_reads=change == "member")
    everything = set(ids)
    want = {"u1": set(), "u2": everything} if change == "member" else {"u1": everything, "u2": set()}
    await parity(p, pg_db, org, ids, {"u1": set(), "u2": want["u2"]})
    if change == "share":
        await share(p, org)
    else:
        await add_edge(p, org, ("users", "u2"), ("groups", "grp"), "permission", type="USER")
    assert (await drain_org(pg_db, org)).rows > 0
    queued = await jobs(pg_db, org)
    assert [(j.kind, j.reason) for j in queued] == [("app", restamp.SUBTREE)]
    found = await marks(p, org)
    assert 0 < found < len(ids) - 1, found                       # a bounded part of the reach, not all of it
    assert not await serving(pg_db, org)                         # the connector is hidden whole: the reader walks it
    await parity(p, pg_db, org, ids, want)
    cursors = await batches(p, pg_db, org, ids, want, "w1")
    walking = [c for c in cursors if c and c.startswith("w")]
    assert any(c.count(":") == 4 for c in walking) and any(c.count(":") == 1 for c in walking)      # rows walked by page, and a row with many children by itself
    assert any(c and c.startswith("w1") for c in cursors)        # ``early`` was marked behind the walk's position: a second pass
    await settled(p, pg_db, org, ids, want)


async def test_a_subtree_job_interrupted_at_any_batch_goes_on_from_its_last_batch(pg_db, small, monkeypatch) -> None:
    """A batch that fails half way (in the walk, then in the restamp) leaves no trace; the job's state is its marks and cursor, so a worker that died (its claim gone stale)
    is replaced by another that finishes it, and nothing outdated is answered on the way."""
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await share(p, org)
    await drain_org(pg_db, org)
    want = {"u1": set(ids), "u2": set()}
    await batches(p, pg_db, org, ids, want, "w1", n=2)
    for broken, where in ((restamp, "_mark"), (stamp, "stamp_keys")):
        real = getattr(broken, where)
        calls = {"n": 0}

        async def once(*args: object, real=real, calls=calls, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                await real(*args, **kwargs)                       # the write happens, and the batch then fails after it
                raise RuntimeError("worker killed")
            return await real(*args, **kwargs)
        if where == "stamp_keys":                                 # reach the restamp: walk to its end first
            while not ((job := (await jobs(pg_db, org))[0]).cursor or "").startswith("s"):
                await batches(p, pg_db, org, ids, want, "w1", n=1)
        before = ((await jobs(pg_db, org))[0].cursor, await marks(p, org))
        monkeypatch.setattr(broken, where, once)
        job = await restamp.claim(pg_db, org, "w2", stale_ms=0)
        with pytest.raises(RuntimeError, match="worker killed"):
            await restamp.run_batch(pg_db, org, job, "w2", batch=5)
        assert ((await jobs(pg_db, org))[0].cursor, await marks(p, org)) == before          # rolled back whole
        await parity(p, pg_db, org, ids, want)
    assert await restamp.run_jobs(pg_db, org, "w3", stale_ms=0, batch=5) == 1
    await settled(p, pg_db, org, ids, want)


async def test_a_second_large_change_joins_the_running_subtree_job_and_sends_it_back_to_the_start_of_its_walk(pg_db, small) -> None:
    """While the job of grp's grant on G is part way, the grant is taken back: the same job takes the new marks, its walk starts again, and the worker that held it loses the claim
    at its next batch (rolled back) instead of finishing a walk that missed the new reach."""
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await share(p, org)
    await drain_org(pg_db, org)
    job = await restamp.claim(pg_db, org, "w1", stale_ms=0)
    for _ in range(3):
        _, job = await restamp.run_batch(pg_db, org, job, "w1", batch=5)
    assert job.cursor is not None
    await unshare(p, org)
    await drain_org(pg_db, org)
    queued = await jobs(pg_db, org)
    assert [(j.id, j.cursor, j.reason) for j in queued] == [(job.id, None, restamp.SUBTREE)]
    want = {"u1": set(), "u2": set()}
    await parity(p, pg_db, org, ids, want)
    with pytest.raises(restamp.LostClaim):
        await restamp.run_batch(pg_db, org, job, "w1", batch=5)
    assert await restamp.run_jobs(pg_db, org, "w2", batch=5) == 1
    await settled(p, pg_db, org, ids, want)


async def test_a_large_reach_through_rows_of_no_connector_is_still_found_whole_in_the_unit(pg_db, small) -> None:
    """A connector's fence hides only its own rows: when a row of no connector has a child in kg, what hangs below it may belong to any connector, so the unit finds the reach
    whole (and hides it row by row) instead of fencing connectors."""
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "loose", "recordName": "loose", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"},
                                    {**_rec(org, "under"), "_key": "under"}], "records")
        await p.batch_create_edges([edge("loose", "records", "under", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("under", "records", "app1", "apps")], "belongsTo")
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w0", batch=50)
    assert await run_in(pg_db, org, lambda tx: tx.fetchval(restamp.APPLESS_PARENT, org, restamp.APPLESS_SCAN))
    await share(p, org)
    await drain_org(pg_db, org)
    assert {(j.kind, j.reason) for j in await jobs(pg_db, org)} == {("rows", "reach")}
    assert await marks(p, org) >= len(ids) - 1
    await restamp.run_jobs(pg_db, org, "w1", batch=50)
    assert await jobs(pg_db, org) == [] and await marks(p, org) == 0 and await dry_diffs(p, org, "app1") == []
    await parity(p, pg_db, org, ids, {"u1": set(ids), "u2": set()})


async def test_a_change_within_the_units_reach_keeps_hiding_row_by_row(pg_db, small, monkeypatch) -> None:
    """Below ``REACH_MAX`` nothing changes: the reach is found whole, its rows are marked and a rows job restamps them; the connector stays served."""
    monkeypatch.setattr(stamp, "REACH_MAX", 10_000)
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await share(p, org)
    await drain_org(pg_db, org)
    assert [(j.kind, j.reason) for j in await jobs(pg_db, org)] == [("rows", "reach")] and await serving(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w1", batch=50)
    await settled(p, pg_db, org, ids, {"u1": set(ids), "u2": set()})


async def test_a_rows_job_never_takes_over_a_row_that_a_subtree_job_has_marked(pg_db, small) -> None:
    """A row marked by the subtree job is still to be walked below; a smaller change that reaches it later leaves the mark alone (the row is hidden already, and is restamped from
    the graph as it is then)."""
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org)
    await share(p, org)
    await drain_org(pg_db, org)
    job = (await jobs(pg_db, org))[0]

    async def go(tx) -> tuple[int, int]:
        sk, fence = await tx.fetchrow("SELECT sk, fence_job FROM kg.records WHERE org_id = $1 AND fence_job IS NOT NULL ORDER BY sk LIMIT 1", org)
        coll, rid = await tx.fetchrow("SELECT coll, id FROM kg.node_keys WHERE org_id = $1 AND sk = $2", org, sk)
        await restamp.enqueue_rows(tx, org, {({1: "apps", 2: "recordGroups", 3: "records"}[coll], rid)}, "reach", org_wide=True)
        return fence, await tx.fetchval("SELECT fence_job FROM kg.records WHERE org_id = $1 AND sk = $2", org, sk)
    assert await run_in(pg_db, org, go, write=True) == (job.id, job.id)
    assert [j.kind for j in await jobs(pg_db, org)] == ["app"]


async def test_a_mark_on_an_apps_row_does_not_hold_the_job_or_its_connector(pg_db, small) -> None:
    """An app's row has no stamp a batch restamps: a mark on it is lifted with the restamp of the other rows, so the job ends and the connector is served again."""
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await share(p, org)
    await drain_org(pg_db, org)
    job = (await jobs(pg_db, org))[0]

    async def mark(tx) -> None:
        r = await tx.fetchrow("SELECT sk, coll, id, kind FROM kg.records WHERE org_id = $1 AND kind = 1", org)
        await KgWriter(tx).fence_rows([(r["sk"], r["coll"], r["id"], r["kind"])], job.id)
    await run_in(pg_db, org, mark, write=True)
    assert await restamp.run_jobs(pg_db, org, "w1", batch=50) == 1
    await settled(p, pg_db, org, ids, {"u1": set(ids), "u2": set()})


async def loose_records(p, pg_db, org: str, n: int) -> None:
    """``n`` records of no connector, none with a child, drained into kg."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": f"loose{i}", "recordName": f"loose{i}", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"} for i in range(n)], "records")
    await drain_org(pg_db, org)
    await restamp.run_jobs(pg_db, org, "w0", batch=500)


async def appless_rows_read(pg_db, org: str) -> float:
    """The rows of no connector the check reads (EXPLAIN ANALYZE of it as the unit runs it, under reader.seeks)."""
    async def go(tx) -> float:
        async with seeks(tx):
            ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + restamp.APPLESS_PARENT, org, restamp.APPLESS_SCAN)
        plan = (json.loads(ran) if isinstance(ran, str) else ran)[0]
        return sum(n.get("Actual Rows", 0) * n.get("Actual Loops", 1) for n in plan_gate.scans(plan, ("records",)) if n.get("Index Name") == "records_app")
    with ctx(org):
        return await pg_db.run(go, custom_plan=True)


async def test_the_check_for_rows_of_no_connector_reads_a_bounded_number_of_them_and_takes_the_safe_path_beyond(pg_db, small, monkeypatch) -> None:
    """Many records of no connector and none with a child: the check reads at most ``APPLESS_SCAN`` + 1 of them, whatever their number (it ran once per row of no connector in
    the organisation, in the writer's unit, and again on every retry). When there are more than that it cannot tell, so it takes the path that is always exact: the reach is
    found whole in the unit and hidden row by row."""
    monkeypatch.setattr(restamp, "APPLESS_SCAN", 20)
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await loose_records(p, pg_db, org, 120)
    assert await appless_rows_read(pg_db, org) <= 21
    await share(p, org)
    await drain_org(pg_db, org)
    assert {(j.kind, j.reason) for j in await jobs(pg_db, org)} == {("rows", "reach")}
    await restamp.run_jobs(pg_db, org, "w1", batch=50)
    await settled(p, pg_db, org, ids, {"u1": set(ids), "u2": set()})


async def test_a_few_rows_of_no_connector_without_children_keep_the_subtree_job(pg_db, small, monkeypatch) -> None:
    """Up to ``APPLESS_SCAN`` records of no connector, none with a child: nothing hangs below them, so hiding the connector whole hides the whole reach and the job walks it."""
    monkeypatch.setattr(restamp, "APPLESS_SCAN", 20)
    p, org = prov(pg_db), new_org()
    ids = await world(p, pg_db, org)
    await loose_records(p, pg_db, org, 20)
    await share(p, org)
    await drain_org(pg_db, org)
    assert [(j.kind, j.reason) for j in await jobs(pg_db, org)] == [("app", restamp.SUBTREE)]
    await restamp.run_jobs(pg_db, org, "w1", batch=50)
    await settled(p, pg_db, org, ids, {"u1": set(ids), "u2": set()})
