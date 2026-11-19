"""The net under the reader: a sampled check repeated by the walk, and the worker's periodic verify. Each takes a connector whose kg differs from the graph back to the walk, in either
direction (kg admitting what the walk denies, and kg denying what it admits), and neither can promote or widen anything when it fails."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time

import asyncpg
import pytest

from app.core.db import DbConfig, KernelDB
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import bootstrap, drainer, net
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg import verify as verify_mod
from app.services.graph_db.postgres.kg.worker import KgWorker
from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org
from .test_kg_derivation import ctx, prov
from .test_kg_service import _defaults, _mode, _until, _world  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.timeout(240)]
IDS = ["drv", "r1", "r2", "r3"]


async def _admin(sql: str, *args: object) -> None:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute(sql, *args)
    finally:
        await con.close()


async def _enforce(p, pg_db, org: str, *, extra: int = 0, apps: int = 0) -> None:
    await _world(p, org, extra=extra, apps=apps)
    due: dict = {}
    for _ in range(40):
        await bootstrap.bring_to_enforce(pg_db, org, due)
        if await _enforced(pg_db, org) == apps + 1:
            return
    raise AssertionError("not enforced")


async def _enforced(db, org: str) -> int:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.projection_state WHERE org_id = $1 AND app_sk <> 0 AND mode = 'enforce'", org)
    with ctx(org):
        return await db.run(go)


async def _class(org: str, clauses: str) -> int:
    """A class row of kg with the given clauses (an empty clause is always satisfied, no clause never is), for a record to be pointed at."""
    cid = 900_000 + secrets.randbelow(90_000)
    await _admin("INSERT INTO kg.scope_classes (org_id, id, app_sk, group_sk, clauses, hash) SELECT $1, $2, k.sk, NULL, $3::jsonb, $4 FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'drv'",
                 org, cid, clauses, secrets.token_bytes(16))
    return cid


async def _ask(p, org: str, user: str = "u1") -> dict:
    with ctx(org):
        return {r["id"]: r for r in await p._kh_v3_accessible_rows(user, org, IDS, [], access=None, transaction=None)}


@pytest.fixture
def every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_SHADOW_SAMPLE", "1")


async def test_kg_admitting_what_the_walk_denies_is_caught_by_the_sample_and_demotes(pg_db, every_check, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))       # r2 is open to every viewer of the connector in kg only
    with caplog.at_level("ERROR"):
        rows = await _ask(p, org)
        await net.settle()
    assert "r2" in rows                                                       # this answer was kg's: the sample runs after it
    assert await _mode(p, org) == "legacy" and "goes back to the walk" in caplog.text
    assert "r2" not in await _ask(p, org)                                     # and the next one is the walk's


async def test_kg_denying_what_the_walk_admits_demotes_too(pg_db, every_check) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r1' AND coll = 3", org, await _class(org, "[]"))          # r1, which u1 holds a grant on, is closed in kg
    assert "r1" not in await _ask(p, org)
    await net.settle()
    assert await _mode(p, org) == "legacy"
    assert "r1" in await _ask(p, org)


async def test_a_kg_that_agrees_with_the_walk_stays_enforced(pg_db, every_check) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    for _ in range(3):
        await _ask(p, org)
        await net.settle()
    assert await _mode(p, org) == "enforce"


async def test_a_connector_the_net_took_back_is_not_taken_up_again_at_once_and_then_only_through_the_verify(pg_db, every_check) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))
    await _ask(p, org)
    await net.settle()
    assert await _mode(p, org) == "legacy"
    due: dict = {}
    for _ in range(5):
        await bootstrap.bring_to_enforce(pg_db, org, due)
    assert await _mode(p, org) == "legacy"                                    # held: no flapping
    bootstrap._HELD.clear()
    for _ in range(40):
        await bootstrap.bring_to_enforce(pg_db, org, due)
        if await _mode(p, org) == "enforce":
            break
    assert await _mode(p, org) == "enforce"                                   # copied again from the graph and verified: the seeded class is gone
    assert "r2" not in await _ask(p, org)
    await net.settle()
    assert await _mode(p, org) == "enforce"


async def test_a_sample_that_fails_changes_nothing(pg_db, every_check, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("the walk is down")
    with monkeypatch.context() as m:
        m.setattr(net.access_sql, "accessible_rows", boom)
        await _ask(p, org)
        await net.settle()
    assert await _mode(p, org) == "enforce"                                   # not demoted, and nothing else changed


async def test_the_sample_is_bounded_in_share_size_and_number(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=6)
    seen: list[int] = []
    real = net._sample

    async def spy(run, db, o, ids, access) -> None:
        seen.append(len(ids))
        await real(run, db, o, ids, access)
    monkeypatch.setattr(net, "_sample", spy)
    monkeypatch.setenv("PG_KG_SHADOW_SAMPLE", "0")
    await _ask(p, org)
    await net.settle()
    assert seen == []                                                         # a share of 0 repeats nothing
    monkeypatch.setenv("PG_KG_SHADOW_SAMPLE", "1")
    monkeypatch.setattr(net, "SAMPLE_IDS", 2)
    with ctx(org):
        await p._kh_v3_accessible_rows("u1", org, [*IDS, *(f"x{i}" for i in range(6))], [], access=None, transaction=None)
    await net.settle()
    assert seen == [2]                                                        # at most SAMPLE_IDS nodes are repeated
    monkeypatch.setattr(net, "MAX_SAMPLES", 0)
    await _ask(p, org)
    await net.settle()
    assert seen == [2]                                                        # a busy process skips the sample


@pytest.fixture
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    monkeypatch.setattr(net, "_jitter", lambda: 0.0)


async def test_the_verify_catches_a_stale_copy_and_demotes_and_leaves_a_good_one(pg_db, no_jitter) -> None:
    p, good, stale = prov(pg_db), new_org(), new_org()
    await _enforce(p, pg_db, good)
    await _enforce(p, pg_db, stale)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", stale)             # kg lost a record the graph has
    assert await net.verify_due(pg_db, good, {}) == 1 and await _mode(p, good) == "enforce"
    assert await net.verify_due(pg_db, stale, {}) == 1 and await _mode(p, stale) == "legacy"


async def test_a_record_kg_holds_that_the_graph_does_not_is_caught_by_the_chunked_verify(pg_db, no_jitter) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk) SELECT $1, 80000001, 3, 'ghost', 3, k.sk FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'drv'", org)
    assert await net.verify_due(pg_db, org, {}) == 1 and await _mode(p, org) == "legacy"


async def test_the_verify_is_bounded_per_pass_scheduled_stoppable(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, apps=2)                                      # three connectors
    monkeypatch.setattr(net, "VERIFY_PER_PASS", 1)
    states: dict = {}
    assert [await net.verify_due(pg_db, org, states) for _ in range(4)] == [1, 1, 1, 0]       # one a pass, each once, then none is due
    assert len(states) == 3
    states.clear()
    stop = asyncio.Event()
    stop.set()
    assert await net.verify_due(pg_db, org, states, stop) == 0 and all(st.cursor is None or st.cursor.pages <= 1 for st in states.values())      # stopped: no more than the page in hand
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "0")
    assert await net.verify_due(pg_db, org, {}) == 0                           # never


async def test_a_connector_too_large_for_one_pass_is_verified_over_several_from_its_cursor(pg_db, no_jitter, monkeypatch, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=12)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'x9'", org)                  # a stale row, found by a page late in the verify
    monkeypatch.setattr(verify_mod, "PAGE", 3)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)                           # one page a pass
    states: dict = {}
    passes = 0
    seen_pages: list[int] = []
    while await _mode(p, org) == "enforce" and passes < 60:
        await net.verify_due(pg_db, org, states)
        passes += 1
        if (st := states.get((org, "drv"))) and st.cursor:
            seen_pages.append(st.cursor.pages)
    assert await _mode(p, org) == "legacy"                                     # found at the end, after many passes
    assert passes > 4 and seen_pages == sorted(seen_pages) and len(set(seen_pages)) > 3      # a cursor that moved forward and was kept between passes


async def test_a_verify_that_does_not_finish_in_its_intervals_is_logged_and_raised_not_demoted(pg_db, no_jitter, monkeypatch, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=6)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    monkeypatch.setattr(net, "OVERDUE_INTERVALS", 0)
    raised: list[float] = []
    monkeypatch.setattr(net, "OVERDUE", type("G", (), {"set": staticmethod(lambda *a, value: raised.append(value))})())
    states: dict = {}
    with caplog.at_level("ERROR"):
        for _ in range(3):
            await net.verify_due(pg_db, org, states)
    assert "has not completed a verify" in caplog.text and max(raised) >= 1
    assert await _mode(p, org) == "enforce"                                    # being large is not a reason to demote


async def test_a_complete_verify_sets_the_last_verified_gauge(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    sets: list[tuple] = []
    monkeypatch.setattr(net, "LAST_VERIFIED", type("G", (), {"set": staticmethod(lambda *labels, value: sets.append((labels, value)))})())
    before = time.time()
    assert await net.verify_due(pg_db, org, {}) == 1
    assert [labels for labels, _ in sets] == [(org, "drv")] and before <= sets[0][1] <= time.time()
    sets.clear()
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)
    await net.verify_due(pg_db, org, {})
    assert sets == []                                                          # a verify that found a difference is not a verified connector


async def test_the_first_verifies_are_spread_over_the_interval(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "1000")
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, apps=2)
    draws = iter([0.1, 0.5, 0.9])
    monkeypatch.setattr(net, "_jitter", lambda: next(draws))
    states: dict = {}
    assert await net.verify_due(pg_db, org, states) == 0                       # none is due yet
    now = time.monotonic()
    waits = sorted(st.due - now for st in states.values())
    assert [round(w / 100) for w in waits] == [1, 5, 9]


async def test_a_verify_that_fails_changes_nothing(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("the database went away")
    states: dict = {}
    with monkeypatch.context() as m:
        m.setattr(net, "verify_chunk", boom)
        assert await net.verify_due(pg_db, org, states) == 0
    assert await _mode(p, org) == "enforce" and states[(org, "drv")].due > time.monotonic() and states[(org, "drv")].cursor is None      # left as it is, tried again next period


async def test_a_chunk_that_runs_past_its_limit_is_cut_and_nothing_changes(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)
    monkeypatch.setattr(net, "VERIFY_TIMEOUT", "50ms")
    real = net.verify_chunk

    async def slow(tx, o, app, cur) -> bool:
        await tx.fetchval("SELECT pg_sleep(1)")
        return await real(tx, o, app, cur)
    monkeypatch.setattr(net, "verify_chunk", slow)
    assert await net.verify_due(pg_db, org, {}) == 0
    assert await _mode(p, org) == "enforce"


async def test_the_worker_verifies_in_a_task_of_its_own_and_demotes(pg_db, no_jitter, monkeypatch) -> None:
    monkeypatch.setattr(net, "VERIFY_TICK_S", 0.2)
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=0.2)
    worker.start()
    try:
        assert await _until(lambda: _is_legacy(p, org), 60)
    finally:
        await worker.stop()


async def test_the_drain_goes_on_while_a_verify_is_in_progress(pg_db, no_jitter, monkeypatch) -> None:
    monkeypatch.setattr(net, "VERIFY_TICK_S", 0.2)
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(db, o, app, cur) -> str:
        entered.set()
        await release.wait()
        return "skipped"
    monkeypatch.setattr(net, "_chunk", blocked)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=0.2)
    worker.start()
    try:
        await asyncio.wait_for(entered.wait(), 60)                              # the verify is in progress and stays so
        with ctx(org):
            await p.batch_upsert_nodes([{"_key": "r1", "recordName": "renamed while verifying"}], "records")
        assert await _until(lambda: _log_empty(p, org), 60)                     # the drain took the change meanwhile
        assert not release.is_set()
    finally:
        release.set()
        await worker.stop()


async def _log_empty(p, org: str) -> bool:
    async def go(tx) -> int:
        return await tx.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1", org)
    with ctx(org):
        return await p._q(go, None) == 0


async def test_the_query_service_that_cannot_write_kg_asks_the_worker_to_demote(pg_db, every_check, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "0")                          # only the notice can demote here, not the worker's own verify
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))
    direct = os.environ["PG_CORE_URL"]
    monkeypatch.setenv("PG_CORE_URL", os.environ["PG_QUERY_URL"])
    qdb = KernelDB(DbConfig.from_env("query"))
    await qdb.start()
    monkeypatch.setenv("PG_CORE_URL", direct)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, dsn=direct, poll_s=30)
    worker.start()
    try:
        await asyncio.sleep(1)                                                 # listening
        qp = PostgresGraphProvider(logging.getLogger("kg-query"), qdb)
        await _ask(qp, org)
        await net.settle()
        assert await _until(lambda: _is_legacy(p, org), 30)
    finally:
        await worker.stop()
        await qdb.close()


async def _is_legacy(p, org: str) -> bool:
    return await _mode(p, org) == "legacy"


async def test_a_demotion_notice_is_only_acted_on_for_an_org_the_worker_serves(pg_db, caplog) -> None:
    p, org, other = prov(pg_db), new_org(), new_org()
    await _enforce(p, pg_db, org)

    async def orgs() -> list[str]:
        return [other]
    payload = f'["{org}", "drv"]'
    await net.demote_requested(pg_db, orgs, payload)
    assert await _mode(p, org) == "enforce"                                    # not an org this worker serves
    with caplog.at_level("WARNING"):
        await net.demote_requested(pg_db, orgs, "not a notice")
    assert "ignored a demotion notice" in caplog.text and await _mode(p, org) == "enforce"

    async def mine() -> list[str]:
        return [org]
    await net.demote_requested(pg_db, mine, payload)
    assert await _mode(p, org) == "legacy"
    assert net.parse_ask('["a", 1]') is None and net.parse_ask("[") is None


# ---- a difference takes effect in the process that found it, whatever happens to the demotion ------------------------------------------------

async def test_when_the_demotion_fails_and_the_notice_is_lost_the_next_check_in_the_process_uses_the_walk(pg_db, every_check, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))

    async def fails(*_a: object, **_k: object) -> None:
        raise RuntimeError("this role may not write kg")

    async def lost(*_a: object, **_k: object) -> None:
        return None
    decided: list[int] = []
    real = kg_read.decide

    async def spy(*a: object, **k: object) -> object:
        d = await real(*a, **k)
        decided.append(len(d.admit) + d.denied)
        return d
    monkeypatch.setattr(bf, "demote", fails)
    monkeypatch.setattr(net, "_ask", lost)
    monkeypatch.setattr(kg_read, "decide", spy)
    assert "r2" in await _ask(p, org)                                         # kg's answer: the sample sees the difference after it
    await net.settle()
    assert await _mode(p, org) == "enforce"                                   # nothing demoted it
    decided.clear()
    assert "r2" not in await _ask(p, org)                                     # and the next answer is the walk's, not kg's
    assert decided == [0]


async def test_a_distrust_is_lifted_when_the_mode_change_is_seen_and_the_connector_is_served_again_after_a_re_promotion(pg_db, every_check, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("UPDATE kg.records SET class_id = $2 WHERE org_id = $1 AND id = 'r2' AND coll = 3", org, await _class(org, "[[]]"))
    await _ask(p, org)
    await net.settle()
    assert (org, "drv") in net.DISTRUSTED and await _mode(p, org) == "legacy"
    await _ask(p, org)                                                         # the mode is seen to have changed: trusted again, and (legacy) the walk answers
    assert (org, "drv") not in net.DISTRUSTED
    bootstrap._HELD.clear()
    due: dict = {}
    for _ in range(40):
        await bootstrap.bring_to_enforce(pg_db, org, due)
        if await _mode(p, org) == "enforce":
            break
    seen: list[int] = []
    real = kg_read.decide

    async def spy(*a: object, **k: object) -> object:
        d = await real(*a, **k)
        seen.append(len(d.admit) + d.denied)
        return d
    monkeypatch.setattr(kg_read, "decide", spy)
    assert "r2" not in await _ask(p, org) and seen and seen[-1] > 0           # kg answers again, from a copy that agrees with the graph


async def test_a_re_promotion_the_process_never_saw_as_legacy_is_recognised_by_the_new_verify(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    net.distrust(org, "drv")
    await net._remember(pg_db, org, "drv")
    assert net.DISTRUSTED[(org, "drv")] is not None
    async def read(tx) -> bool:
        return await net.distrusted(tx, org, "drv")
    with ctx(org):
        assert await pg_db.run(read) is True                                  # still the copy that was found different
    await _admin("UPDATE kg.projection_state SET verified_at = now() + interval '1 hour' WHERE org_id = $1 AND app_sk <> 0", org)      # a verify since (a re-promotion)
    with ctx(org):
        assert await pg_db.run(read) is False and (org, "drv") not in net.DISTRUSTED


@pytest.fixture
async def query_db(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    direct = os.environ["PG_CORE_URL"]
    monkeypatch.setenv("PG_CORE_URL", os.environ["PG_QUERY_URL"])
    qdb = KernelDB(DbConfig.from_env("query"))
    await qdb.start()
    monkeypatch.setenv("PG_CORE_URL", direct)
    yield qdb
    await qdb.close()


async def _marks(org: str) -> int:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        return await con.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = $1 AND ends @> ARRAY['3:@demote']", org)
    finally:
        await con.close()


async def test_a_demotion_the_worker_cannot_hear_is_still_found_in_the_change_log_on_its_next_pass(pg_db, query_db, monkeypatch, caplog) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    with caplog.at_level("INFO"):
        await net.demote(query_db, org, ["drv"], source="sample", why="seeded")            # the query role: the demotion is refused, so it asks
    expected = [r for r in caplog.records if "could not demote" in r.getMessage()]
    assert len(expected) == 1 and expected[0].levelname == "INFO" and expected[0].exc_info is None      # one line, no traceback
    assert await _mode(p, org) == "enforce" and await _marks(org) == 1         # the notice went nowhere; the request row is there

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, dsn=None, poll_s=0.2)                        # no listener: a polling worker
    await worker.run_once()
    assert await _mode(p, org) == "legacy" and await _marks(org) == 0           # demoted from the request row, which is gone


async def test_the_request_is_sent_once_a_hold_window_and_again_until_the_mode_changes(pg_db, query_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    monkeypatch.setattr(net, "HOLD_S", 0.4)
    net._ASKED.clear()
    await net.demote(query_db, org, ["drv"], source="sample", why="seeded")
    await net.demote(query_db, org, ["drv"], source="sample", why="seeded again")          # a second sample hit in the same window
    assert await _marks(org) == 1                                              # one request, not one per hit
    await asyncio.sleep(1.2)                                                   # the asking task goes on while the connector is enforced
    assert await _marks(org) >= 2
    await net.shutdown()


async def test_stop_cancels_the_samples_and_the_demotions_in_flight_and_leaves_no_task(pg_db, every_check, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    gate = asyncio.Event()

    async def hang(*_a: object, **_k: object) -> object:
        await gate.wait()
    monkeypatch.setattr(net, "compare", hang)

    async def orgs() -> list[str]:
        return [org]
    worker = KgWorker(pg_db, orgs, poll_s=0.2)
    worker.start()
    await _ask(p, org)                                                         # starts a sample that never ends
    spawned = asyncio.get_running_loop().create_task(asyncio.sleep(60))
    worker._demotes.add(spawned)
    assert net._SAMPLES.get(asyncio.get_running_loop())
    await worker.stop()
    assert not net._SAMPLES.get(asyncio.get_running_loop()) and spawned.cancelled() and worker._verify_task.done()
    assert not [t for t in asyncio.all_tasks() if t.get_name() in ("kg-verify", "kg-worker", "kg-demote") and not t.done()]


async def test_the_samples_draw_their_ids_from_the_nodes_kg_decided(pg_db, every_check, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    got: list[list[str]] = []
    real = net._sample

    async def spy(run, db, o, ids, access) -> None:
        got.append(list(ids))
        await real(run, db, o, ids, access)
    monkeypatch.setattr(net, "_sample", spy)
    with ctx(org):
        await p._kh_v3_accessible_rows("u1", org, [*IDS, *(f"nothing{i}" for i in range(40))], [], access=None, transaction=None)
    await net.settle()
    assert got and set(got[0]) <= set(IDS) and not any(i.startswith("nothing") for i in got[0])      # the 40 unknown ids are the walk's and are not repeated


async def test_the_statements_of_the_chunked_verify_are_index_ranges(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk) SELECT $1, 70000000 + g, 3, 'chunk-r' || g, 3, k.sk FROM generate_series(1, 12000) g, kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'drv' AND g > 0", org)
    await _admin("INSERT INTO kg.node_keys (org_id, coll, id, sk) SELECT $1, 3, 'chunk-r' || g, 70000000 + g FROM generate_series(1, 12000) g", org)
    await _admin("INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'chunk-r' || g, jsonb_build_object('connectorId', 'drv', 'orgId', $1::text) FROM generate_series(1, 12000) g", org)
    await _admin("INSERT INTO kg.scope_members (org_id, principal_sk, node_sk, gtype, app_sk) SELECT $1, 1, 70000000 + g, 'USER', k.sk FROM generate_series(1, 12000) g, kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'drv'", org)
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE kg.scope_members; ANALYZE kg.node_keys; ANALYZE graph.nodes")

        async def go(tx) -> dict[str, str]:
            sk = await tx.fetchval("SELECT k.sk FROM kg.node_keys k WHERE k.org_id = $1 AND k.coll = 1 AND k.id = 'drv'", org)
            await tx.fetchval(net.CHUNK_PROFILE, "30s")
            plans = {}
            for name, sql, args in (("ghosts", verify_mod.GHOSTS, (org, sk, 0, 2000, "drv")), ("grants", verify_mod.GRANT_GHOSTS, (org, sk, 0, 0, "", 2000))):
                plans[name] = "\n".join(r[0] for r in await tx.fetch("EXPLAIN (COSTS OFF) " + sql, *args))
            return plans
        with ctx(org):
            plans = await pg_db.run(go, custom_plan=True)
        for name, plan in plans.items():
            assert "Seq Scan" not in plan, (state, name, plan)
        assert "records_app" in plans["ghosts"] or "records_pkey" in plans["ghosts"], plans["ghosts"]
        assert "Index Scan using scope_members" in plans["grants"] or "Index Only Scan using scope_members" in plans["grants"], plans["grants"]


# ---- the request row, the verify's restart and its immediate demotion ------------------------------------------------------------------------------

async def _lag(db, org: str) -> int:
    async def go(tx) -> int:
        return await drainer.lag_in(tx, org)
    with ctx(org):
        return await db.run(go)


async def test_the_drain_never_takes_a_demotion_request_and_a_failing_request_pass_keeps_it(pg_db, query_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await net._ask(query_db, org, "drv", force=True)
    assert await _marks(org) == 1
    await drainer.drain_org(pg_db, org)
    assert await _marks(org) == 1 and await _lag(pg_db, org) == 0              # not drained, and not counted as a change waiting

    async def orgs() -> list[str]:
        return [org]
    async def fails(*_a: object, **_k: object) -> None:
        raise RuntimeError("the demotion fails")
    worker = KgWorker(pg_db, orgs, dsn=None, poll_s=0.2)
    with monkeypatch.context() as m:
        m.setattr(bf, "demote", fails)
        await worker.run_once()                                                # the request fails, and the drain of the same pass runs
    assert await _marks(org) == 1 and await _mode(p, org) == "enforce"         # the request is still there for the next pass
    await worker.run_once()
    assert await _mode(p, org) == "legacy" and await _marks(org) == 0


async def test_a_verify_that_found_a_change_waiting_starts_again_later_and_does_not_hold_the_pass(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=6)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    states: dict = {}
    await net.verify_due(pg_db, org, states)                                   # a page in
    assert states[(org, "drv")].cursor is not None
    monkeypatch.setenv("PG_KG_FLUSH", "async")
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r1", "recordName": "changed while verifying"}], "records")        # a change waits in the log: the connector is not served
    await net.verify_due(pg_db, org, states)
    st = states[(org, "drv")]
    assert st.cursor is None and st.due - time.monotonic() > net.SKIP_RETRY_S - 5       # the scan is dropped and starts again later
    calls = []
    real = net._chunk

    async def spy(*a: object, **k: object) -> str:
        calls.append(1)
        return await real(*a, **k)
    monkeypatch.setattr(net, "_chunk", spy)
    await net.verify_due(pg_db, org, states)
    assert calls == []                                                         # it does not take a slot of the pass until then
    await drainer.drain_org(pg_db, org)
    st.due = 0.0
    await net.verify_due(pg_db, org, states)
    assert states[(org, "drv")].cursor is not None and states[(org, "drv")].cursor.pages == 1        # from the beginning


async def test_the_first_page_that_shows_a_difference_demotes_at_once(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=12)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r1'", org)                  # in the first page of the scan
    states: dict = {}
    for _ in range(3):
        await net.verify_due(pg_db, org, states)
        if await _mode(p, org) == "legacy":
            break
    assert await _mode(p, org) == "legacy" and ((org, "drv") not in states or states[(org, "drv")].cursor is None)


async def test_a_scan_in_progress_is_dropped_when_the_connector_leaves_enforce_and_the_next_one_is_fresh(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=12)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    states: dict = {}
    await net.verify_due(pg_db, org, states)
    await net.verify_due(pg_db, org, states)
    assert states[(org, "drv")].cursor.pages >= 2
    await bf.demote(pg_db, org, "drv")
    await net.verify_due(pg_db, org, states)
    assert (org, "drv") not in states                                           # nothing of the old copy's scan is kept
    bootstrap._HELD.clear()
    for _ in range(40):
        await bootstrap.bring_to_enforce(pg_db, org, {})
        if await _mode(p, org) == "enforce":
            break
    await net.verify_due(pg_db, org, states)
    assert states[(org, "drv")].cursor is None or states[(org, "drv")].cursor.pages <= 1       # a fresh scan, not the old cursor


async def test_the_time_of_the_last_verify_is_recorded_before_the_demotion_is_tried(pg_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    seen: list = []
    real = bf.demote

    async def spy(db, o, app) -> None:
        seen.append(net.DISTRUSTED.get((o, app)))
        await real(db, o, app)
    monkeypatch.setattr(bf, "demote", spy)
    await net.demote(pg_db, org, ["drv"], source="sample", why="seeded")
    assert seen and seen[0] is not None                                         # known before the mode could change under it


async def test_the_asking_stops_once_the_connector_is_no_longer_enforced(pg_db, query_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    monkeypatch.setattr(net, "HOLD_S", 0.3)
    net._ASKED.clear()
    await net.demote(query_db, org, ["drv"], source="sample", why="seeded")
    assert await _marks(org) == 1
    await _admin("UPDATE kg.projection_state SET mode = 'legacy' WHERE org_id = $1 AND app_sk <> 0", org)       # the worker (or anyone) took the connector off enforce
    await asyncio.sleep(1.5)
    assert await _marks(org) == 1                                               # no further request
    await net.shutdown()


async def test_a_request_that_failed_is_asked_again_at_the_next_detection_not_a_hold_window_later(pg_db, query_db, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    net._ASKED.clear()
    fail = {"n": 1}

    class Flaky:
        async def run(self, fn, **k) -> object:
            if fail["n"]:
                fail["n"] -= 1
                raise RuntimeError("the database went away")
            return await query_db.run(fn, **k)
    await net._ask(Flaky(), org, "drv")
    assert await _marks(org) == 0
    await net._ask(Flaky(), org, "drv")                                         # the next detection, at once
    assert await _marks(org) == 1


async def test_a_change_drained_between_two_pages_restarts_the_scan_so_the_rewritten_key_is_verified(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=10)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    states: dict = {}
    await net.verify_due(pg_db, org, states)
    await net.verify_due(pg_db, org, states)
    assert states[(org, "drv")].cursor.pages == 2                              # two pages in
    monkeypatch.setenv("PG_KG_FLUSH", "async")
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r1", "recordName": "rewritten after its page was checked"}], "records")
    await drainer.drain_org(pg_db, org)                                        # the drain rewrites the key and deletes its log row: the connector is served again
    await net.verify_due(pg_db, org, states)
    cur = states[(org, "drv")].cursor
    assert cur is not None and cur.pages == 1 and cur.after is not None and cur.after[1] != ""      # a new scan, from the first page
    seen: list[int] = []
    for _ in range(40):
        await net.verify_due(pg_db, org, states)
        c = states[(org, "drv")].cursor
        if c is None:
            break
        seen.append(c.pages)
    assert states[(org, "drv")].cursor is None and await _mode(p, org) == "enforce"      # and it completes clean when nothing moves meanwhile


async def test_a_change_drained_during_the_principals_check_restarts_the_scan(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    sets: list = []
    monkeypatch.setattr(net, "LAST_VERIFIED", type("G", (), {"set": staticmethod(lambda *labels, value: sets.append(labels))})())
    real = net._principals

    async def drain_meanwhile(db, o) -> bool:
        net.locks.WRITTEN[(o, "drv")] = net.locks.written(o, "drv") + 1          # what a drain of the connector does while the principals are read
        return await real(db, o)
    monkeypatch.setattr(net, "_principals", drain_meanwhile)
    states: dict = {}
    for _ in range(3):
        await net.verify_due(pg_db, org, states)
    assert sets == [] and await _mode(p, org) == "enforce"                      # never recorded as verified, and not demoted either
    monkeypatch.setattr(net, "_principals", real)
    for _ in range(3):
        await net.verify_due(pg_db, org, states)
    assert sets and sets[0] == (org, "drv")                                     # a scan nothing moved under completes


async def test_a_scan_begins_only_after_a_unit_that_has_counted_has_committed(pg_db, no_jitter, monkeypatch) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=10)
    monkeypatch.setattr(verify_mod, "PAGE", 2)
    monkeypatch.setattr(net, "VERIFY_BUDGET_S", 0.0)
    states: dict = {}
    counted, go_on = asyncio.Event(), asyncio.Event()

    async def unit(tx) -> None:
        await net.locks.lock_apps(tx, org, {"drv"}, org_wide=False)
        net.locks.bump(org, {"drv"})                                            # a projection unit has written and counted, and has not committed
        counted.set()
        await go_on.wait()
    with ctx(org):
        writer = asyncio.create_task(pg_db.run(unit, write=True))
        await counted.wait()
        scan = asyncio.create_task(net.verify_due(pg_db, org, states))
        await asyncio.sleep(0.5)
        waiting = not scan.done()                                               # the scan waits for the unit: it would otherwise take the count with the unit's rows not yet visible
        go_on.set()
        await writer
        await scan
    assert waiting
    assert states[(org, "drv")].mark == net.locks.written(org, "drv")           # taken once the unit had committed


async def test_a_scan_that_cannot_get_the_lock_in_time_leaves_no_state_and_tries_again_later(pg_db, no_jitter) -> None:
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, extra=10)
    states: dict = {}
    held, release = asyncio.Event(), asyncio.Event()

    async def unit(tx) -> None:
        await net.locks.lock_apps(tx, org, {"drv"}, org_wide=False)
        held.set()
        await release.wait()
    with ctx(org):
        writer = asyncio.create_task(pg_db.run(unit, write=True))
        try:
            await held.wait()
            await net.verify_due(pg_db, org, states)                                   # waits for the lock for as long as a unit may (the connectors service: 5 s), then gives up
            st = states[(org, "drv")]
            assert st.cursor is None and st.due > time.monotonic()                      # no scan state survives: it starts again at its next turn
        finally:
            release.set()
            await writer
