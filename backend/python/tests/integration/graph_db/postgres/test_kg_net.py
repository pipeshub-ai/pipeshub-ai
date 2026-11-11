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
from app.services.graph_db.postgres.kg import bootstrap, net
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


async def test_the_verify_catches_a_stale_copy_and_demotes_and_leaves_a_good_one(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    p, good, stale = prov(pg_db), new_org(), new_org()
    await _enforce(p, pg_db, good)
    await _enforce(p, pg_db, stale)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", stale)             # kg lost a record the graph has
    assert await net.verify_due(pg_db, good, {}) == 1 and await _mode(p, good) == "enforce"
    assert await net.verify_due(pg_db, stale, {}) == 1 and await _mode(p, stale) == "legacy"


async def test_the_verify_is_bounded_per_pass_scheduled_stoppable_and_cut_for_a_large_connector(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org, apps=2)                                      # three connectors
    monkeypatch.setattr(net, "VERIFY_PER_PASS", 1)
    due: dict = {}
    assert [await net.verify_due(pg_db, org, due) for _ in range(4)] == [1, 1, 1, 0]       # one a pass, each once, then none is due
    assert len(due) == 3
    due.clear()
    stop = asyncio.Event()
    stop.set()
    assert await net.verify_due(pg_db, org, due, stop) == 0                    # stopped: nothing is verified
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "0")
    assert await net.verify_due(pg_db, org, {}) == 0                           # never
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)
    monkeypatch.setattr(net, "VERIFY_PER_PASS", 5)
    monkeypatch.setattr(net, "VERIFY_TIMEOUT", "50ms")
    real = net.verify_app

    async def slow(tx, o, app) -> object:
        await tx.fetchval("SELECT pg_sleep(1)")                                # a connector too large for the limit
        return await real(tx, o, app)
    monkeypatch.setattr(net, "verify_app", slow)
    assert await net.verify_due(pg_db, org, {}) == 0                           # cut by the limit: left to the sample, not demoted, not promoted
    assert await _enforced(pg_db, org) == 3


async def test_a_verify_that_fails_changes_nothing(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("the database went away")
    due: dict = {}
    with monkeypatch.context() as m:
        m.setattr(net, "verify_app", boom)
        assert await net.verify_due(pg_db, org, due) == 0
    assert await _mode(p, org) == "enforce" and due[(org, "drv")] > time.monotonic()      # left as it is, and tried again next period


async def test_the_worker_verifies_on_its_pass_and_demotes(pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_VERIFY_EVERY_S", "60")
    p, org = prov(pg_db), new_org()
    await _enforce(p, pg_db, org)
    await _admin("DELETE FROM kg.records WHERE org_id = $1 AND id = 'r3'", org)

    async def orgs() -> list[str]:
        return [org]
    await KgWorker(pg_db, orgs, poll_s=0.2).run_once()
    assert await _mode(p, org) == "legacy"


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
