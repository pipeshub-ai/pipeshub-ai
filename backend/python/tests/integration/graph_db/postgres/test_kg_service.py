"""The typed graph from the first write, with no setting made: a connector created on a fresh org is projected, verified and promoted to enforce by the worker, the change log it drains
returns to its baseline, ``check_access`` is then decided from kg and equals the walk, and a revoked grant is denied by kg after the next drain. Two drains of one org never project
at the same time."""
from __future__ import annotations

import asyncio
import time
from collections import Counter

import pytest

from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import bootstrap, drainer, modes, worker
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.worker import KgWorker

from .pg_support import new_org
from .test_kg_derivation import ctx, edge, prov

pytestmark = [pytest.mark.integration, pytest.mark.timeout(240)]


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PG_KG_PROJECT", "PG_KG_READ", "PG_KG_FLUSH", "PG_KG_STRICT"):
        monkeypatch.delenv(name, raising=False)


async def _world(p, org: str, extra: int = 0, users: int = 0, apps: int = 0) -> None:
    """A connector, two users, three records; u1 may use the connector and holds a grant on r1 only. The writes are the provider's own."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_upsert_nodes([{"_key": "drv", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u1@example.com"}, {"_key": "u2", "orgId": org, "email": "u2@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": f"r{i}", "recordName": f"R{i}", "connectorId": "drv", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for i in (1, 2, 3)], "records")
        if users:
            await p.batch_upsert_nodes([{"_key": f"extra-u{i}", "orgId": org, "email": f"e{i}@example.com"} for i in range(users)], "users")
        for a in range(apps):
            await p.batch_upsert_nodes([{"_key": f"drv{a}", "name": f"Drive {a}", "type": "DRIVE", "orgId": org}], "apps")
            await p.batch_upsert_nodes([{"_key": f"app{a}-r", "recordName": "R", "connectorId": f"drv{a}", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        if extra:
            await p.batch_upsert_nodes([{"_key": f"x{i}", "recordName": f"X{i}", "connectorId": "drv", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for i in range(extra)], "records")
        await p.batch_create_edges([edge("u1", "users", "drv", "apps"), edge("u2", "users", "drv", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "r1", "records", type="USER", role="READER")], "permission")


async def _log_rows(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None)


async def _mode(p, org: str) -> str | None:
    async def go(tx) -> str | None:
        return await tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = 'drv'""", org)
    with ctx(org):
        return await p._q(go, None)


async def _until(cond, seconds: float = 90) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await cond():
            return True
        await asyncio.sleep(0.1)
    return False


async def _check(p, org: str, user: str, ids: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Counter, list[str]]:
    """The rows ``check_access`` reads for ``user`` with the defaults, what kg decided, and the ids the walk was asked about."""
    seen: Counter = Counter()
    walked: list[str] = []
    real_decide, real_walk = kg_read.decide, access_sql.accessible_rows

    async def decide(*a, **k) -> kg_read.Decision:
        d = await real_decide(*a, **k)
        seen.update(admit=len(d.admit), denied=d.denied, walk=len(d.walk))
        return d

    async def walk(tx, o, node_ids, vrids, access) -> list[dict]:
        walked.extend(node_ids)
        return await real_walk(tx, o, node_ids, vrids, access)
    with monkeypatch.context() as m:
        m.setattr(kg_read, "decide", decide)
        m.setattr(access_sql, "accessible_rows", walk)
        with ctx(org):
            rows = {r["id"]: r for r in await p._kh_v3_accessible_rows(user, org, ids, [], access=None, transaction=None)}
    return rows, seen, walked


async def _walk_rows(p, org: str, user: str, ids: list[str], monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("PG_KG_READ", "0")
    try:
        with ctx(org):
            return {r["id"]: r for r in await p._kh_v3_accessible_rows(user, org, ids, [], access=None, transaction=None)}
    finally:
        monkeypatch.delenv("PG_KG_READ", raising=False)


async def test_a_new_connector_is_enforced_by_the_worker_its_log_is_drained_and_the_read_follows_a_revoked_grant(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    assert await _log_rows(p, org) > 0                                       # the writers only log; the worker projects
    assert await _mode(p, org) is None                                       # nothing is copied yet

    ids = ["drv", "r1", "r2", "r3"]
    rows, seen, _ = await _check(p, org, "u1", ids, monkeypatch)
    assert not seen["admit"] and not seen["denied"]                          # a connector that is not enforced is the walk's
    assert set(rows) == {"drv", "r1"}

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=0.2)
    worker.start()
    try:
        async def enforced() -> bool:
            return await _mode(p, org) == "enforce" and await _log_rows(p, org) == 0
        assert await _until(enforced), (await _mode(p, org), await _log_rows(p, org))      # promoted through backfill and promote, and the log is back to its baseline
        assert await _log_rows(p, org) == 0

        rows, seen, walked = await _check(p, org, "u1", ids, monkeypatch)
        assert seen["admit"] >= 2 and seen["denied"] >= 2, seen              # drv and r1 admitted, r2 and r3 denied: all by kg
        assert not seen["walk"] and not walked, (seen, walked)               # the walk was not asked about any of them
        assert set(rows) == {"drv", "r1"} and rows == await _walk_rows(p, org, "u1", ids, monkeypatch)
        rows2, seen2, _ = await _check(p, org, "u2", ids, monkeypatch)
        assert set(rows2) == {"drv"} and rows2 == await _walk_rows(p, org, "u2", ids, monkeypatch)

        with ctx(org):                                                       # the grant is revoked
            await p.batch_delete_edges([edge("u1", "users", "r1", "records", type="USER", role="READER")], "permission")

        async def revoked() -> bool:
            if await _log_rows(p, org):
                return False
            _, s, w = await _check(p, org, "u1", ids, monkeypatch)
            return bool(s["denied"]) and not w
        assert await _until(revoked)
        rows, seen, walked = await _check(p, org, "u1", ids, monkeypatch)
        assert set(rows) == {"drv"} and rows == await _walk_rows(p, org, "u1", ids, monkeypatch)
        assert seen["admit"] == 1 and seen["denied"] == 3 and not walked, (seen, walked)
        assert await _log_rows(p, org) == 0
    finally:
        await worker.stop()


async def test_a_connector_with_nodes_before_the_worker_is_backfilled_once_and_a_failed_verify_stays_on_the_walk(pg_db, monkeypatch, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    due: dict = {}
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 1
    assert await _mode(p, org) == "enforce"
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 0            # enforced: not a candidate again

    # a second org whose verify finds a difference: kg is made to differ from the graph, the connector stays on the walk and is logged, and it is not tried again
    other = new_org()
    await _world(p, other)
    real = bf.verify_app

    async def differs(tx, o, app) -> object:
        res = await real(tx, o, app)
        res.note("extra", "x")
        return res
    due2: dict = {}
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(bf, "verify_app", differs)
        assert await bootstrap.bring_to_enforce(pg_db, other, due2) == 0
    assert await _mode(p, other) != "enforce"
    assert "stays on the walk" in caplog.text
    assert await bootstrap.bring_to_enforce(pg_db, other, due2) == 0         # once: a failed verify is not retried by the worker
    assert await _mode(p, other) != "enforce"


async def test_a_connector_with_changes_still_arriving_is_tried_again_later(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    real = bootstrap.bf.backfill

    async def pending(db, o, app, **k) -> object:
        out = await real(db, o, app, **k)
        out.flipped, out.pending = False, 3
        return out
    due: dict = {}
    with monkeypatch.context() as m:
        m.setattr(bootstrap.bf, "backfill", pending)
        assert await bootstrap.bring_to_enforce(pg_db, org, due) == 0
    assert 0 < due[(org, "drv")] - time.monotonic() <= bootstrap.RETRY_S            # not never, and not at once
    due[(org, "drv")] = 0.0
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 1


@pytest.mark.parametrize("failure", ["refused", "error"])
async def test_a_promotion_that_is_refused_or_fails_is_logged_and_tried_again_later(pg_db, monkeypatch, caplog, failure) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)

    async def boom(db, o, app) -> None:
        raise bootstrap.bf.Refused("changed meanwhile") if failure == "refused" else RuntimeError("database went away")
    due: dict = {}
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(bootstrap.bf, "promote", boom)
        assert await bootstrap.bring_to_enforce(pg_db, org, due) == 0
    assert await _mode(p, org) != "enforce" and "kg: connector drv" in caplog.text
    assert 0 < due[(org, "drv")] - time.monotonic() <= bootstrap.RETRY_S
    due[(org, "drv")] = 0.0
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 1


async def test_two_drains_of_one_org_never_project_at_the_same_time_and_the_log_is_emptied_once(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    active = {"now": 0, "max": 0, "calls": 0}
    real = drainer.project

    async def slow(tx, o, focus) -> object:
        active["now"] += 1
        active["calls"] += 1
        active["max"] = max(active["max"], active["now"])
        try:
            await asyncio.sleep(0.5)
            return await real(tx, o, focus)
        finally:
            active["now"] -= 1
    monkeypatch.setattr(drainer, "project", slow)
    first, second = await asyncio.gather(drainer.drain_org(pg_db, org, page=1000), drainer.drain_org(pg_db, org, page=1000))
    assert active["max"] == 1, active                                         # the second found the page taken and waited for the next look
    assert first.rows + second.rows > 0 and await _log_rows(p, org) == 0
    await drainer.drain_org(pg_db, org)
    assert await _log_rows(p, org) == 0


async def _cursor(p, org: str) -> str | None:
    async def go(tx) -> str | None:
        return await tx.fetchval("""SELECT s.backfill_cursor FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = 'drv'""", org)
    with ctx(org):
        return await p._q(go, None)


async def test_a_copy_cleared_while_it_is_verified_is_retried_and_only_a_real_difference_is_not(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    real = bf.verify_app

    async def clears(tx, o, app) -> object:
        res = await real(tx, o, app)
        await modes.demote(tx, o, await bf._app_sk(tx, app))            # a concurrent demotion empties the copy during the flip
        return res
    with monkeypatch.context() as m:
        m.setattr(bf, "verify_app", clears)
        out = await bf.backfill(pg_db, org, "drv")
        assert not out.flipped and out.reason == "cleared" and out.pending == 0
        due: dict = {}
        assert await bootstrap.bring_to_enforce(pg_db, org, due) == 0
    assert 0 < due[(org, "drv")] - time.monotonic() <= bootstrap.RETRY_S      # retried later, not treated as a difference
    due[(org, "drv")] = 0.0
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 1 and await _mode(p, org) == "enforce"

    other = new_org()
    await _world(p, other)

    async def differs(tx, o, app) -> object:
        res = await real(tx, o, app)
        res.note("extra", "x")
        return res
    with monkeypatch.context() as m:
        m.setattr(bf, "verify_app", differs)
        assert (await bf.backfill(pg_db, other, "drv")).reason == "differs"


async def test_a_large_copy_resumes_across_passes_keeps_its_cursor_and_still_promotes(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org, extra=12)
    monkeypatch.setattr(bootstrap, "PAGE", 2)
    monkeypatch.setattr(bootstrap, "BATCHES", 1)
    due: dict = {}
    passes, cursors = 0, []
    while await _mode(p, org) != "enforce":
        assert await bootstrap.bring_to_enforce(pg_db, org, due) >= 1
        passes += 1
        cursors.append(await _cursor(p, org))
        assert passes < 40
    assert passes > 3                                                         # several passes, each bounded
    mid = [c for c in cursors if c not in (None, "done")]
    assert mid and len(set(mid)) == len(mid)                                  # the cursor moved forward each time and was kept between passes


async def test_stop_returns_promptly_in_the_middle_of_a_copy_and_the_cursor_is_kept(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org, extra=8)
    monkeypatch.setattr(bootstrap, "PAGE", 1)
    monkeypatch.setattr(bootstrap, "BATCHES", 1000)
    calls = {"n": 0}
    real = bf.project

    async def slow(*a, **k) -> object:
        calls["n"] += 1
        await asyncio.sleep(0.7)
        return await real(*a, **k)
    monkeypatch.setattr(bf, "project", slow)

    async def orgs() -> list[str]:
        return [org]
    w = KgWorker(pg_db, orgs, poll_s=0.2)
    w.start()
    assert await _until(lambda: _calls_at_least(calls, 6), 60)             # the principals are copied and so is part of the connector
    t0 = time.monotonic()
    await w.stop()
    assert time.monotonic() - t0 < 3, "stop() waited for the whole copy"
    assert await _mode(p, org) != "enforce"
    assert await _cursor(p, org) not in (None, "done")                       # what was copied is kept for the next start


async def _calls_at_least(calls: dict, n: int) -> bool:
    return calls["n"] >= n


async def test_stop_cuts_a_unit_that_does_not_end_and_says_so(pg_db, monkeypatch, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    entered = asyncio.Event()

    async def hangs(tx, o, app) -> object:
        entered.set()
        await asyncio.sleep(120)

    async def orgs() -> list[str]:
        return [org]
    monkeypatch.setattr(bf, "verify_app", hangs)
    monkeypatch.setattr(worker, "STOP_TIMEOUT_S", 0.5)
    w = KgWorker(pg_db, orgs, poll_s=0.2)
    w.start()
    await asyncio.wait_for(entered.wait(), 60)
    t0 = time.monotonic()
    with caplog.at_level("WARNING"):
        await w.stop()
    assert time.monotonic() - t0 < 5 and "did not stop" in caplog.text


async def test_a_second_org_gets_its_turn_while_a_large_connector_is_still_being_copied(pg_db, monkeypatch) -> None:
    p, big, small = prov(pg_db), new_org(), new_org()
    await _world(p, big, extra=30)
    await _world(p, small)
    monkeypatch.setattr(bootstrap, "PAGE", 3)
    monkeypatch.setattr(bootstrap, "BATCHES", 3)

    async def orgs() -> list[str]:
        return [big, small]
    w = KgWorker(pg_db, orgs, poll_s=0.2)
    assert await w.run_once() > 0
    assert await _mode(p, small) == "enforce"                                # the small org was served in the first pass
    assert await _mode(p, big) != "enforce" and await _cursor(p, big) not in (None, "done")
    for _ in range(40):
        await w.run_once()
        if await _mode(p, big) == "enforce":
            break
    assert await _mode(p, big) == "enforce"                                  # and the large one still promotes


async def _enforced(p, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.projection_state WHERE org_id = $1 AND app_sk <> 0 AND mode = 'enforce'", org)
    with ctx(org):
        return await p._q(go, None)


async def test_a_log_that_keeps_gaining_rows_is_waited_for_not_chased_and_the_next_org_still_runs(pg_db, monkeypatch) -> None:
    p, busy, quiet = prov(pg_db), new_org(), new_org()
    await _world(p, busy)
    await _world(p, quiet)
    rounds = {"n": 0}
    real_drain = bf.drain_org
    asked: list[dict] = []

    async def endless(db, o, **k) -> object:
        if o != busy:
            return await real_drain(db, o, **k)
        asked.append(k)
        rounds["n"] += 1
        with ctx(busy):
            await p.batch_upsert_nodes([{"_key": "r1", "recordName": f"changed {rounds['n']}"}], "records")        # a writer is always ahead of the drain
        return drainer.DrainStats(rows=1)
    stamped = []
    real_stamp = bf._restamp_pages

    async def spy(db, o, *a, **k) -> object:
        stamped.append(o)
        return await real_stamp(db, o, *a, **k)
    monkeypatch.setattr(bf, "drain_org", endless)
    monkeypatch.setattr(bf, "_restamp_pages", spy)
    out = await bf.backfill(pg_db, busy, "drv", drain_rounds=3)
    assert not out.flipped and out.reason == "pending" and out.pending > 0
    assert rounds["n"] == 3 and busy not in stamped                              # three rounds, then back: nothing was stamped meanwhile
    assert all(k.get("page") and k.get("page") == k.get("max_rows") for k in asked), asked      # a bounded round reads small pages too: one commits whole

    async def orgs() -> list[str]:
        return [busy, quiet]
    w = KgWorker(pg_db, orgs, poll_s=0.2)
    await w.run_once()
    assert await _enforced(p, quiet) == 1                                         # the next org was served
    assert await _enforced(p, busy) == 0 and 0 < w._due[(busy, "drv")] - time.monotonic() <= bootstrap.RETRY_S


async def test_the_principals_are_copied_once_per_pass_however_many_connectors_an_org_has(pg_db, monkeypatch) -> None:
    p, big, small = prov(pg_db), new_org(), new_org()
    await _world(p, big, users=9, apps=2)                                         # three connectors and more principals than BATCHES pages hold
    await _world(p, small)
    monkeypatch.setattr(bootstrap, "PAGE", 4)
    monkeypatch.setattr(bootstrap, "BATCHES", 2)
    spent = {"n": 0}
    real = bf.backfill_scope

    async def count(db, o, app_id, **k) -> int:
        n = await real(db, o, app_id, **k)
        if app_id is None and o == big:
            spent["n"] += n
        return n
    monkeypatch.setattr(bf, "backfill_scope", count)

    async def orgs() -> list[str]:
        return [big, small]
    w = KgWorker(pg_db, orgs, poll_s=0.2)
    await w.run_once()
    assert spent["n"] <= bootstrap.BATCHES, spent                                  # not BATCHES per connector
    assert await _enforced(p, small) == 1                                         # the second org reaches enforce in the first pass
    assert await _enforced(p, big) == 0
    for _ in range(60):
        await w.run_once()
        if await _enforced(p, big) == 3:
            break
    assert await _enforced(p, big) == 3


async def test_a_connector_that_is_demoted_after_its_promotion_is_promoted_again(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    due: dict = {}
    assert await bootstrap.bring_to_enforce(pg_db, org, due) == 1 and await _mode(p, org) == "enforce"
    await bf.demote(pg_db, org, "drv")                                            # the take-back, a verify that differed, an operator
    assert await _mode(p, org) == "legacy"
    assert await bootstrap.bring_to_enforce(pg_db, org, due) >= 1
    for _ in range(5):
        if await _mode(p, org) == "enforce":
            break
        await bootstrap.bring_to_enforce(pg_db, org, due)
    assert await _mode(p, org) == "enforce"


async def test_a_restart_of_the_principal_copy_between_the_check_and_the_flip_refuses_the_flip(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org)
    real = bf.verify_principals

    async def restarts(tx, o) -> object:
        res = await real(tx, o)
        await modes.demote(tx, o, 0)                                              # a concurrent restart empties the principal copy
        return res
    with monkeypatch.context() as m:
        m.setattr(bf, "verify_principals", restarts)
        out = await bf.backfill(pg_db, org, "drv")
    assert not out.flipped and out.reason == "cleared"
    assert await _mode(p, org) != "enforce"


async def test_the_stamping_of_a_large_connector_goes_on_across_passes_and_the_connector_still_promotes(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _world(p, org, extra=12)
    monkeypatch.setattr(bootstrap, "STAMP_PAGE", 2)
    monkeypatch.setattr(bootstrap, "STAMP_PAGES", 1)
    seen: list[str | None] = []
    due: dict = {}
    for _ in range(40):
        await bootstrap.bring_to_enforce(pg_db, org, due)
        seen.append(bootstrap._STAMP_AFTER.get((org, "drv")))
        if await _mode(p, org) == "enforce":
            break
    assert await _mode(p, org) == "enforce"
    moved = [c for c in seen if c]
    assert len(moved) >= 3 and len(set(moved)) == len(moved)                       # one page a pass, each from where the last stopped
    assert (org, "drv") not in bootstrap._STAMP_AFTER
