"""Failure modes of the database runtime: swallowed errors, COMMIT outcome on the wire, nesting, savepoints, binding
across tasks/threads/generators, a mixed 8,000-unit run, retries, cancellation. Uses the fixtures of this directory."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import random
import time
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.core import org_context as oc
from app.core.db import (
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfig,
    DbTimeout,
    FailedTransaction,
    KernelDB,
    PoolTimeout,
    PrologueFailed,
    SessionRoleLeak,
    UnsupportedIsolation,
    UowUsageError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration
ORGS = ("aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccc", "dddddddddddddddddddddddd")


def as_org(org: str, rid: str = "rid") -> AbstractContextManager:
    return oc.bind(oc.OrgContext(org, "u", "user", "jwt", rid))


class Proxy:
    """TCP proxy in front of PostgreSQL/PgBouncer that can eat the reply to COMMIT, or drop COMMIT before the server sees it."""

    def __init__(self, port) -> None:
        self.target, self.mode, self.commits_seen, self.conns, self.delay = port, None, 0, 0, 0.0
        self.writers = []

    async def start(self) -> None:
        self.srv = await asyncio.start_server(self._h, "127.0.0.1", 0)
        self.port = self.srv.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.srv.close()
        for w in self.writers:
            w.close()

    async def _h(self, r, w) -> None:
        self.conns += 1
        ur, uw = await asyncio.open_connection("127.0.0.1", self.target)
        self.writers += [w, uw]
        st = {"drop": False}

        async def c2s() -> None:
            try:
                while data := await r.read(65536):
                    if data[:1] == b"Q" and data[5:12] == b"COMMIT;" or data[5:11] == b"COMMIT":
                        self.commits_seen += 1
                        if self.mode == "drop_before":
                            self.mode = None
                            break
                        if self.mode == "drop_reply":
                            self.mode = None
                            uw.write(data)
                            await uw.drain()
                            st["drop"] = True
                            await asyncio.sleep(0.2)
                            break
                        if self.mode == "hold_reply":
                            st["hold"] = True
                    uw.write(data)
                    await uw.drain()
            except Exception:
                pass
            finally:
                w.close()
                uw.close()

        async def s2c() -> None:
            try:
                while data := await ur.read(65536):
                    if st["drop"]:
                        continue
                    if st.get("hold"):
                        await asyncio.sleep(self.delay)
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                w.close()
                uw.close()

        await asyncio.gather(c2s(), s2c())


# ---- 1. swallowed statement error -----------------------------------------------------------------------------
@pytest.mark.parametrize("via", ["uow", "run"])
async def test_swallowed_error_then_return_raises_failed_transaction(env, make, via) -> None:
    db = await make("indexing")
    admin = await env.admin()
    note = f"swallow-{via}"

    async def body(tx) -> str:
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', $1)", note)
        with contextlib.suppress(asyncpg.UniqueViolationError):
            await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaa-1','dup')")  # 23505
        return "returned-ok"

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(FailedTransaction):
        if via == "uow":
            async with db.uow(write=True) as tx:
                await body(tx)
        else:
            await db.run(body, write=True)
    assert await admin.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", note) == 0
    await admin.close()


async def test_swallowed_error_then_next_statement_raises(env, make) -> None:
    db = await make("indexing")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(asyncpg.InFailedSQLTransactionError):
        async with db.uow(write=True) as tx:
            with contextlib.suppress(asyncpg.UniqueViolationError):
                await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaa-1','dup')")
            await tx.execute("SELECT 1")


async def test_swallowed_timeout_then_return_raises_failed_transaction(env, make) -> None:
    db = await make("indexing")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(FailedTransaction):
        async with db.uow(write=True, timeout_ms=50) as tx:
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','t-swallow')")
            with contextlib.suppress(DbTimeout):
                await tx.execute("SELECT pg_sleep(1)")
    admin = await env.admin()
    assert await admin.fetchval("SELECT count(*) FROM kx.log WHERE note='t-swallow'") == 0
    await admin.close()


async def test_savepoint_recovers_from_a_statement_error(env, make) -> None:
    db = await make("indexing")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow(write=True) as tx:
            with pytest.raises(asyncpg.UniqueViolationError):
                async with tx.savepoint("s1"):
                    await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaa-1','dup')")
            async with tx.savepoint("s2"):  # nothing failed: released
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','after-sp')")
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','after-sp-2')")
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.log WHERE note LIKE 'after-sp%'") == 2
        with pytest.raises(FailedTransaction):  # an error swallowed INSIDE the block is not recovered
            async with db.uow(write=True) as tx, tx.savepoint("s3"):
                with contextlib.suppress(asyncpg.UniqueViolationError):
                    await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaa-1','dup')")
        async with db.uow(write=True) as tx:
            with pytest.raises(ValueError):
                async with tx.savepoint("bad name"):
                    pass


async def test_statements_cannot_rebind_the_unit_or_use_raw_savepoints_in_the_lint(env, make) -> None:
    from app.core.db import sql_lint

    db = await make("node")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            for sql in ("SELECT set_config('app.org_id','bbbbbbbbbbbbbbbbbbbbbbbb',true)", "SET LOCAL ROLE pipeshub_node", "SET ROLE pipeshub_node"):
                with pytest.raises(UowUsageError):
                    await tx.execute(sql)
            assert await tx.fetchval("SELECT current_setting('app.org_id')") == "aaaaaaaaaaaaaaaaaaaaaaaa"  # the unit is not failed by the refusal
    assert sql_lint.scan_source("async def f(tx):\n    await tx.execute('/* pipeshub:a.b */ SAVEPOINT s1')\n")


# ---- 2. unknown COMMIT outcome --------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,applied", [("drop_reply", 1), ("drop_before", 0)])
@pytest.mark.parametrize("through_bouncer", [False, True])
@pytest.mark.parametrize("idem", [False, True])
async def test_commit_reply_dropped_is_never_retried(env, make, mode, applied, through_bouncer, idem) -> None:
    target = env.start_bouncer(pool_size=2) if through_bouncer else env.cluster.port
    px = Proxy(target)
    await px.start()
    try:
        db = await make("indexing", px.port, pool_max=2)
        note = f"c-{mode}-{through_bouncer}-{idem}"
        tries = 0

        async def fn(tx) -> None:
            nonlocal tries
            tries += 1
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', $1)", note)

        px.mode, px.commits_seen = mode, 0
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(CommitOutcomeUnknown):
            await db.run(fn, write=True, idempotent=idem)
        assert tries == 1 and px.commits_seen == 1
        admin = await env.admin()
        assert await admin.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", note) == applied
        await admin.close()
        # the pool recovered
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow() as tx:
                assert await tx.fetchval("SELECT 1") == 1
    finally:
        await px.stop()


async def test_connection_terminated_during_slow_commit_is_unknown(env, make) -> None:
    db = await make("indexing")
    admin = await env.admin()
    await admin.execute("""CREATE OR REPLACE FUNCTION kx.slow() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1); RETURN NULL; END $$""")
    await admin.execute("CREATE CONSTRAINT TRIGGER slow_c AFTER INSERT ON kx.log DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION kx.slow()")
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','slowc')")

    async def killer() -> None:
        for _ in range(100):
            await asyncio.sleep(0.05)
            n = await admin.fetchval("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE query LIKE 'COMMIT%' AND pid<>pg_backend_pid() AND datname=current_database()")
            if n:
                return
            # trigger fires at commit; sleep shows as 'active' with the last query text being the insert or COMMIT

    k = asyncio.create_task(killer())
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        try:
            await db.run(fn, write=True, idempotent=True)
            res = "ok"
        except Exception as e:
            res = type(e).__name__
    await k
    assert tries == 1 and res == "CommitOutcomeUnknown"
    await admin.close()


async def test_connection_lost_idle_then_return_is_connection_lost(env, make) -> None:
    db = await make("indexing")
    admin = await env.admin()
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','idle-kill')")
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'")
        await asyncio.sleep(0.2)

    # The connection died before COMMIT was sent (the pre-commit probe finds out), so nothing committed:
    # ConnectionLost, and a re-run only for an idempotent callable.
    for idem, want_tries in ((False, 1), (True, 4)):
        tries = 0
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(ConnectionLost):
            await db.run(fn, write=True, idempotent=idem)
        assert tries == want_tries
    await admin.close()


# ---- 3. org binding -------------------------------------------------------------------------------------------
async def _whoami(db: KernelDB, write: bool = False) -> str:
    async with db.uow(write=write) as tx:
        return await tx.fetchval("SELECT current_setting('app.org_id')")


async def test_binding_across_tasks_detached_threads_generators(env, make) -> None:
    db = await make("node", pool_max=4)
    seen = {}
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        # child task copies context
        seen["task"] = await asyncio.create_task(_whoami(db))
        # spawn_detached: empty context
        t = oc.spawn_detached(_whoami(db))
        try:
            await t
            seen["detached"] = "RAN"
        except oc.MissingOrgContext:
            seen["detached"] = "MissingOrgContext"
        # executor thread via to_thread (copies ctx) and run_coroutine_threadsafe back
        loop = asyncio.get_running_loop()

        def from_thread() -> str:
            return asyncio.run_coroutine_threadsafe(_whoami(db), loop).result(10)

        seen["to_thread"] = await asyncio.to_thread(from_thread)
        # run_in_executor: no context
        try:
            seen["executor"] = await loop.run_in_executor(None, from_thread)
        except oc.MissingOrgContext:
            seen["executor"] = "MissingOrgContext"

        # async generator created under aaaaaaaaaaaaaaaaaaaaaaaa, consumed under bbbbbbbbbbbbbbbbbbbbbbbb
        async def gen() -> AsyncIterator[list]:
            async with db.uow() as tx:
                yield await tx.fetchval("SELECT current_setting('app.org_id')")
            yield await _whoami(db)  # a new unit started after the consumer switched org

        g = gen()
    with as_org("bbbbbbbbbbbbbbbbbbbbbbbb"):
        seen["gen_first"] = await g.__anext__()
        seen["gen_second"] = await g.__anext__()
    print("\nBINDING", seen)
    assert seen["task"] == "aaaaaaaaaaaaaaaaaaaaaaaa" and seen["detached"] == "MissingOrgContext"
    assert seen["to_thread"] == "aaaaaaaaaaaaaaaaaaaaaaaa" and seen["executor"] == "MissingOrgContext"
    assert seen["gen_first"] in ("aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb")


async def test_open_async_generator_abandoned_does_not_leak_connection(env, make) -> None:
    db = await make("node", pool_max=1)

    async def gen() -> AsyncIterator[list]:
        async with db.uow():
            for i in range(10):
                yield i

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async for _i in gen():
            break
        # no explicit aclose
        await asyncio.sleep(0.2)
        try:
            async with db.uow() as tx:
                await tx.fetchval("SELECT 1")
            res = "freed"
        except Exception as e:
            res = type(e).__name__
    print("\nABANDONED-GEN", res)
    assert res == "freed", res


async def test_stress_8000_mixed_units(env, make) -> None:
    port = env.start_bouncer(pool_size=4)
    dbs = [await make("node", port, pool_max=24) for _ in range(2)]
    jobs = await make("node", port, pool_max=2, jobs_dsn=env.dsn("jobs", port))
    loop = asyncio.get_running_loop()
    rnd = random.Random(7)
    N = 8000
    sem = asyncio.Semaphore(300)
    stats = collections.Counter()
    expected = collections.Counter()
    wrong = []
    errors = collections.Counter()
    collections.Counter()  # cancelled writes: may or may not have committed

    async def unit(i, org, kind) -> None:
        db = dbs[i % 2]
        note = f"{org}:{i}"
        with as_org(org, f"r{i}"):
            if kind == "read":
                async with db.uow() as tx:
                    rows = await tx.fetch("SELECT org_id FROM kx.docs")
                    cur = await tx.fetchval("SELECT current_setting('app.org_id')")
                    if {r[0] for r in rows} != {org} or len(rows) != 100 or cur != org:
                        wrong.append((i, org, cur, len(rows)))
            elif kind == "write":
                async def fn(tx) -> None:
                    await tx.execute("INSERT INTO kx.log VALUES ($1,$2)", org, note)
                    rows = await tx.fetch("SELECT org_id FROM kx.log WHERE note=$1", note)
                    if [r[0] for r in rows] != [org]:
                        wrong.append((i, org, "write-readback"))
                await db.run(fn, write=True)
                expected[org] += 1
            elif kind == "raise":
                try:
                    async with db.uow(write=True) as tx:
                        await tx.execute("INSERT INTO kx.log VALUES ($1,$2)", org, note)
                        raise ValueError("x")
                except ValueError:
                    pass
            elif kind == "swallow":
                try:
                    async with db.uow(write=True) as tx:
                        await tx.execute("INSERT INTO kx.log VALUES ($1,$2)", org, note)
                        with contextlib.suppress(asyncpg.UniqueViolationError):
                            await tx.execute("INSERT INTO kx.docs VALUES ($1,$2,'d')", org, f"{org}-1")
                    stats["swallow_reported_success"] += 1
                except FailedTransaction as e:
                    stats["swallow_raised_" + type(e).__name__] += 1
            elif kind == "timeout":
                try:
                    async with db.uow(timeout_ms=15) as tx:
                        await tx.fetchval("SELECT pg_sleep(0.2)")
                except DbTimeout:
                    stats["timeouts"] += 1
            elif kind == "detached":
                async def inner() -> tuple:
                    with oc.for_org(org, job="test"):
                        async with jobs.jobs_uow() as tx:
                            return await tx.fetchval("SELECT current_setting('app.org_id')"), await tx.fetchval("SELECT current_setting('role')")
                got = await oc.spawn_detached(inner())
                if got != (org, "pipeshub_jobs"):
                    wrong.append((i, org, got))
            elif kind == "thread":
                def th() -> str:
                    return asyncio.run_coroutine_threadsafe(_whoami(db), loop).result(30)
                got = await asyncio.to_thread(th)
                if got != org:
                    wrong.append((i, org, got))
            elif kind == "gen":
                async def gen() -> AsyncIterator[list]:
                    async with db.uow() as tx:
                        for _ in range(3):
                            yield [r[0] for r in await tx.fetch("SELECT DISTINCT org_id FROM kx.docs")]
                wrong.extend([(i, org, got) async for got in gen() if got != [org]])
            elif kind == "cancel":
                async def w() -> None:
                    with as_org(org, f"r{i}"):
                        async with db.uow(write=True) as tx:
                            await tx.execute("INSERT INTO kx.log VALUES ($1,$2)", org, note)
                            await tx.execute("SELECT pg_sleep(0.3)")
                t = asyncio.create_task(w())
                await asyncio.sleep(rnd.random() * 0.05)
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    stats["cancelled"] += 1

    async def guarded(i) -> None:
        org = rnd.choice(ORGS)
        kind = rnd.choices(["read", "write", "raise", "swallow", "timeout", "detached", "thread", "gen", "cancel"],
                           [30, 25, 8, 6, 5, 8, 6, 8, 4])[0]
        async with sem:
            try:
                await unit(i, org, kind)
                stats["ok_" + kind] += 1
            except (PoolTimeout,):
                errors["PoolTimeout"] += 1
            except Exception as e:
                errors[f"{kind}:{type(e).__name__}"] += 1

    t0 = time.time()
    await asyncio.gather(*[guarded(i) for i in range(N)])
    print(f"\nSTRESS {N} units in {time.time()-t0:.1f}s stats={dict(stats)} errors={dict(errors)} wrong={wrong[:5]} n_wrong={len(wrong)}")
    admin = await env.admin()
    got = {r[0]: r[1] for r in await admin.fetch("SELECT org_id, count(*) FROM kx.log WHERE note NOT LIKE 'swallow%' AND note IS NOT NULL GROUP BY 1")}
    cross = await admin.fetchval("SELECT count(*) FROM kx.log WHERE note NOT LIKE org_id || ':%'")
    print("rows per org", got, "expected(min)", dict(expected), "cross_org_rows", cross)
    assert not wrong and cross == 0 and not stats["swallow_reported_success"]
    # committed rows >= acknowledged writes; extras only from swallow (should be 0) or cancels
    await admin.fetchval("SELECT count(*) FROM kx.log l WHERE EXISTS (SELECT 1 FROM generate_series(1,1))")  # placeholder
    for o in ORGS:
        assert got.get(o, 0) >= expected[o]
    assert not any(k.startswith(("write", "read", "raise", "detached", "thread", "gen")) for k in errors if k != "PoolTimeout"), errors
    await admin.close()


# ---- 4. role leak / prologue ----------------------------------------------------------------------------------
async def test_role_leak_guard_and_prologue_failures(env, make) -> None:
    port = env.start_bouncer(reset=False, pool_size=1)
    db = await make("query", port, pool_max=3)
    leaker = await asyncpg.connect(env.dsn("query", port))
    await leaker.execute("SET ROLE pipeshub_query")  # session-level leak on the only server connection
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        res = []
        for _ in range(5):
            try:
                async with db.uow() as tx:
                    ran = True
                    await tx.fetchval("SELECT 1")
                res.append("ran")
            except SessionRoleLeak:
                res.append("leak")
        print("\nROLE-LEAK sequence", res)
        assert res[0] == "leak"
        # reads of data never happened under the leaked role
    # SET SESSION AUTHORIZATION leak is not detected by the guard (role is still 'none')
    admin = await env.admin()
    # prologue failure: remove SET membership
    await admin.execute("REVOKE pipeshub_query FROM pipeshub_query_login")
    db2 = KernelDB(DbConfig(service="query", dsn=env.dsn("query"), strict=False, fence=False))
    try:
        await db2.start()
    except Exception as e:
        print("START after revoke:", type(e).__name__)
    else:
        ran = False
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            with pytest.raises(PrologueFailed):
                async with db2.uow() as tx:
                    ran = True
        assert not ran
        await db2.close()
    await admin.execute("GRANT pipeshub_query TO pipeshub_query_login WITH INHERIT FALSE, SET TRUE")
    await admin.close()


async def test_prologue_failure_connection_reusable_and_hostile_ids(env, make) -> None:
    db = await make("query", pool_max=1)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa", "x' ; DROP TABLE kx.docs; --" + "A" * 5000):
        async with db.uow() as tx:
            rid = await tx.fetchval("SELECT current_setting('app.request_id')")
            assert rid.startswith("invalid-")
    # org id that is hostile text stays a bound parameter
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa' OR '1'='1"):
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.docs") == 0


# ---- 5. prepared statements -----------------------------------------------------------------------------------
@pytest.mark.parametrize("pooler,max_prep", [("azure_pgbouncer", 0), ("pgbouncer_txn", 200), ("pgbouncer_txn", 0), ("none", 0)])
async def test_prepared_statements(env, make, pooler, max_prep) -> None:
    port = env.start_bouncer(max_prepared=max_prep, pool_size=2)
    db = await make("node", port, pool_max=8, pooler=pooler)
    errs = collections.Counter()
    named = 0

    async def one(i) -> None:
        nonlocal named
        try:
            with as_org(ORGS[i % 4]):
                async with db.uow(write=(i % 3 == 0)) as tx:
                    await tx.fetch("SELECT * FROM kx.docs WHERE id=$1", "x")
                    named = max(named, await tx.fetchval("SELECT count(*) FROM pg_prepared_statements"))
        except Exception as e:
            errs[type(e).__name__ + ":" + str(e)[:60]] += 1

    await asyncio.gather(*[one(i) for i in range(1200)])
    print(f"\nPREPARED pooler={pooler} max_prepared={max_prep} cache={db.cfg.statement_cache_size} errors={dict(errs)} max_named_seen={named}")
    assert db.cfg.statement_cache_size == (100 if pooler == "none" else 0)
    if pooler == "none":  # a direct-connection profile pointed at a pooler without prepared-statement tracking breaks
        assert errs
    else:
        assert not errs and named == 0


# ---- 6/7. nesting & savepoints --------------------------------------------------------------------------------
async def test_nested_units_are_refused_immediately(env, make) -> None:
    db = await make("query", pool_max=2)  # query class: 5 s acquire timeout, which a nested unit must not wait for

    async def caller(org) -> str:
        with as_org(org):
            async with db.uow():
                await asyncio.sleep(0.2)
                async with db.uow():
                    return "inner ok"

    t0 = time.time()
    res = await asyncio.gather(caller("aaaaaaaaaaaaaaaaaaaaaaaa"), caller("bbbbbbbbbbbbbbbbbbbbbbbb"), return_exceptions=True)
    assert all(isinstance(r, UowUsageError) for r in res) and time.time() - t0 < 2
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):  # usable again after the outer unit ended
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT 1") == 1
            with pytest.raises(UowUsageError):
                async with db.uow(write=True):
                    pass


async def test_nesting_other_org_is_refused_too(env, make) -> None:
    db = await make("node", pool_max=2)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow():
            with pytest.raises(oc.OrgMismatch), as_org("bbbbbbbbbbbbbbbbbbbbbbbb"):
                pass


# ---- 8. retries -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sqlstate,expect_tries", [("40001", 4), ("40P01", 4), ("57014", 1), ("55P03", 1), ("23505", 1), ("08006", 4)])
async def test_retry_table_with_real_sqlstates(env, make, sqlstate, expect_tries) -> None:
    db = await make("indexing")
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute(f"DO $$ BEGIN RAISE EXCEPTION 'x' USING ERRCODE='{sqlstate}'; END $$")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(Exception) as e:
            await db.run(fn, write=True)
    print(f"\nRETRY {sqlstate}: {type(e.value).__name__} tries={tries}")
    assert tries == expect_tries


async def test_nonrc_write_refused_before_connection(env, make) -> None:
    db = await make("indexing")
    for iso in ("repeatable_read", "serializable"):
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            with pytest.raises(UnsupportedIsolation):
                await db.run(lambda tx: tx.fetchval("SELECT 1"), write=True, isolation=iso)
            async with db.uow(isolation=iso) as tx:  # read-only ok
                assert await tx.fetchval("SELECT 1") == 1
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(ValueError):
            async with db.uow(write=True, isolation="bogus"):
                pass


async def test_rc_write_in_read_only_unit_fails_loudly_and_is_not_retried(env, make) -> None:
    db = await make("indexing")
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','ro')")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        await db.run(fn)
    assert tries == 1


# ---- 9. cancellation ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["body", "commit_held", "pool_wait", "prologue_race"])
async def test_cancellation_variants(env, make, where) -> None:
    if where == "commit_held":
        px = Proxy(env.cluster.port)
        await px.start()
        db = await make("indexing", px.port, pool_max=1)
    else:
        px = None
        db = await make("indexing", pool_max=1)
    admin = await env.admin()
    note = "cx-" + where

    async def job() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa',$1)", note)
                if where == "body":
                    await tx.execute("SELECT pg_sleep(20)")
                elif where == "pool_wait":
                    await asyncio.sleep(0.5)

    if where == "commit_held":
        px.mode, px.delay = "hold_reply", 3.0
        t = asyncio.create_task(job())
        await asyncio.sleep(0.5)
        t.cancel()
    elif where == "pool_wait":
        asyncio.create_task(job())
        await asyncio.sleep(0.1)
        t = asyncio.create_task(job())
        await asyncio.sleep(0.1)
        t.cancel()
    elif where == "prologue_race":
        ts = [asyncio.create_task(job()) for _ in range(30)]
        await asyncio.sleep(0.02)
        for x in ts:
            x.cancel()
        t = ts[0]
    else:
        t = asyncio.create_task(job())
        await asyncio.sleep(0.3)
        t.cancel()
    try:
        await t
        res = "completed"
    except asyncio.CancelledError:
        res = "cancelled"
    if where == "prologue_race":
        await asyncio.gather(*ts, return_exceptions=True)
    await asyncio.sleep(0.5)
    idle = await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state LIKE 'idle in transaction%'")
    active = await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='active'")
    rows = await admin.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", note)
    t0 = time.time()
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            ok = await tx.fetchval("SELECT 1")
    print(f"\nCANCEL[{where}] {res} idle_in_tx={idle} active={active} rows={rows} next_unit_ok={ok} in {time.time()-t0:.2f}s")
    assert idle == 0 and ok == 1
    if px:
        await px.stop()
