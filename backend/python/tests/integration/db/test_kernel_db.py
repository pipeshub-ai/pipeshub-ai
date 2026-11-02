"""The database runtime against real PostgreSQL (17 and 18) and a real PgBouncer in transaction mode with the kernel reset.

Run: KERNEL_TEST_PG_BINS=<pg17 bin>,<pg18 bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> KERNEL_TEST_MIGRATE_PYTHON=<python with backend/db/requirements.txt> pytest tests/integration/db -m integration --timeout=300

The database is the migrated kernel (real roles, policies and fence); ``fixture.sql`` only adds the test tables through ``kernel.apply_rls``.
"""
from __future__ import annotations

import asyncio
import collections
import random
from typing import TYPE_CHECKING, Never

import asyncpg
import pytest

from app.core import org_context as oc
from app.core.db import (
    CapabilityError,
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfig,
    DbError,
    DbTimeout,
    KernelDB,
    LockTimeout,
    OrgLimits,
    PoolTimeout,
    ProfileEpochMismatch,
    PrologueFailed,
    SessionRoleLeak,
    UnsupportedIsolation,
    catalog,
    sql_lint,
)
from app.core.db import config as db_config
from app.core.db.uow import connection_closed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
ORGS = ("aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccc", "dddddddddddddddddddddddd")


def as_org(org: str, *, request_id: str = "rid-1") -> AbstractContextManager:
    return oc.bind(oc.OrgContext(org, "u1", "user", "jwt", request_id))


@pytest.fixture
def make_with_limits(env) -> Callable[..., Awaitable[KernelDB]]:
    async def build(hook, **kw) -> KernelDB:
        db = KernelDB(DbConfig(service="query", dsn=env.dsn("query"), **kw), org_limits=hook)
        await db.start()
        return db

    return build


# ---- start-up ----------------------------------------------------------------------------------------------
async def test_start_probes_capabilities_and_reads_the_epoch(env, make) -> None:
    db = await make()
    assert db.capabilities.pg_major == env.cluster.major
    assert db.capabilities.extensions["pg_trgm"] == "installed" and db.capabilities.flags["fused_vector"] is False     # the kernel does not install pgvector
    assert db.profile_epoch == 1


async def test_strict_start_refuses_a_missing_required_extension_and_non_strict_downgrades(make) -> None:
    with pytest.raises(CapabilityError, match="required extension vector is"):
        await make(want_extensions=("vector",))
    db = await make(want_extensions=("vector",), strict=False)
    assert db.capabilities.flags["fused_vector"] is False and db.capabilities.warnings


# ---- org binding -------------------------------------------------------------------------------------------
async def test_missing_org_context_is_a_typed_error_and_runs_no_query(make) -> None:
    db = await make()
    acquired = 0
    real = db._pool

    class Counting:
        def acquire(self, *a: object, **k: object) -> object:
            nonlocal acquired
            acquired += 1
            return real.acquire(*a, **k)

        def __getattr__(self, name: str) -> object:
            return getattr(real, name)

    db._pool = Counting()
    ran = []
    with pytest.raises(oc.MissingOrgContext):
        async with db.uow() as tx:
            ran.append(await tx.fetchval("SELECT 1"))
    with oc.system("test"), pytest.raises(oc.SystemContextNotAllowed):
        async with db.uow() as tx:
            ran.append(await tx.fetchval("SELECT 1"))
    assert acquired == 0 and ran == []


async def test_prologue_binds_role_org_request_id_timeouts_and_plan_mode(env, make) -> None:
    db = await make("connectors")
    with as_org("bbbbbbbbbbbbbbbbbbbbbbbb", request_id="req-1:ok"):
        async with db.uow(custom_plan=True) as tx:
            row = await tx.fetchrow("SELECT current_user u, current_setting('app.org_id') o, current_setting('app.request_id') r, "
                                    "current_setting('statement_timeout') st, current_setting('lock_timeout') lt, "
                                    "current_setting('plan_cache_mode') pm, current_setting('application_name') an, "
                                    "current_setting('transaction_read_only') ro")
    assert dict(row) == {"u": "pipeshub_connectors", "o": "bbbbbbbbbbbbbbbbbbbbbbbb", "r": "req-1:ok", "st": "1min", "lt": "5s",
                         "pm": "force_custom_plan", "an": "pipeshub-connectors/dev", "ro": "on"}


async def test_hostile_request_id_is_replaced(make) -> None:
    db = await make()
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa", request_id="x'); DROP TABLE kx.docs; --" + "A" * 5000):
        async with db.uow() as tx:
            got = await tx.fetchval("SELECT current_setting('app.request_id')")
    assert got.startswith("invalid-") and len(got) <= 64


async def test_each_unit_of_work_sees_only_its_org(make) -> None:
    db = await make()
    for org in ORGS:
        with as_org(org):
            async with db.uow() as tx:
                r = await tx.fetchrow("SELECT count(*) n, min(org_id) lo, max(org_id) hi FROM kx.docs")
                assert (r["n"], r["lo"], r["hi"]) == (100, org, org)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with db.uow(write=True) as tx:
            await tx.execute("INSERT INTO kx.docs VALUES ('bbbbbbbbbbbbbbbbbbbbbbbb', 'planted', 't')")


async def test_read_only_unit_of_work_refuses_writes(make) -> None:
    db = await make()
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        async with db.uow() as tx:
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'x')")


async def test_lineage_sets_the_session_value_for_the_transaction(make) -> None:
    db = await make()
    with as_org("cccccccccccccccccccccccc"):
        async with db.uow(lineage=True) as tx:
            assert await tx.fetchval("SELECT current_setting('app.org_lineage')") == "cccccccccccccccccccccccc"


async def test_jobs_unit_of_work_uses_the_jobs_role_and_pool(env, make) -> None:
    db = await make(jobs_dsn=env.dsn("jobs"))
    with oc.for_org("aaaaaaaaaaaaaaaaaaaaaaaa", job="t"):
        async with db.jobs_uow(write=True) as tx:
            assert await tx.fetchval("SELECT current_user") == "pipeshub_jobs"
            assert await tx.fetchval("SELECT current_setting('app.org_id')") == "aaaaaaaaaaaaaaaaaaaaaaaa"
            assert await tx.fetchval("SELECT count(DISTINCT org_id) FROM kx.docs") == 4
    with oc.system("sweep"):
        async with db.jobs_uow() as tx:
            assert await tx.fetchval("SELECT current_setting('app.org_id')") == ""
            assert await tx.fetchval("SELECT count(*) FROM kx.docs") == 400
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(oc.SystemContextNotAllowed):
        async with db.jobs_uow():
            pytest.fail("a request path cannot start a jobs unit of work")


async def test_copy_into_temp_stages_rows_and_the_org_comes_from_the_setting(make) -> None:
    db = await make("indexing")
    with as_org("cccccccccccccccccccccccc"):
        async with db.uow(write=True) as tx:
            await tx.copy_into_temp("stage", {"id": "text", "title": "text"}, [(f"bulk-{i}", "t") for i in range(500)])
            await tx.execute("INSERT INTO kx.docs (org_id, id, title) SELECT current_setting('app.org_id'), id, title FROM stage")
            with pytest.raises(ValueError):
                await tx.copy_into_temp("bad name", {"id": "text"}, [])
            with pytest.raises(ValueError):
                await tx.copy_into_temp("ok", {"id; DROP TABLE x": "text"}, [])
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.docs WHERE id LIKE 'bulk-%'") == 500
            assert await tx.fetchval("SELECT to_regclass('pg_temp.stage')") is None


# ---- guard, prologue failure, fence -------------------------------------------------------------------------
async def test_session_role_left_behind_is_detected_and_the_connection_discarded(env, make) -> None:
    port = env.start_bouncer(reset=False, pool_size=1)
    db = await make(port=port)
    closed_on_release: list[bool] = []
    real = db._release

    async def spy(pool, con) -> None:
        closed_on_release.append(connection_closed(con))
        await real(pool, con)

    db._release = spy
    leaker = await asyncpg.connect(env.dsn("query", port))
    await leaker.execute("SET ROLE pipeshub_query")  # session level: stays on the only server connection
    ran = []
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(SessionRoleLeak):
        async with db.uow() as tx:
            ran.append(await tx.fetchval("SELECT count(*) FROM kx.docs"))
    assert ran == [] and closed_on_release == [True]
    # The dropped client connection took the dirty server connection with it (PgBouncer closes a server connection whose
    # client vanished inside a transaction), so the next unit of work gets a clean one.
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.docs") == 100
    await leaker.close()


async def test_the_test_tables_pass_the_kernel_catalog_check(env) -> None:
    admin = await env.admin()
    try:
        assert await admin.fetch("SELECT * FROM kernel.rls_violations WHERE object LIKE 'kx.%'") == []  # TEMP on the database is granted per test on purpose
    finally:
        await admin.close()


async def test_prologue_failure_runs_no_statement_and_the_fence_stops_old_writers(env, make) -> None:
    db = await make()
    body = []

    async def fn(tx) -> None:
        body.append(await tx.fetchval("SELECT 1"))

    admin = await env.admin()
    await admin.execute("REVOKE pipeshub_query FROM pipeshub_query_login")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(PrologueFailed):
        await db.run(fn)
    await admin.execute("GRANT pipeshub_query TO pipeshub_query_login WITH INHERIT FALSE, SET TRUE")
    assert body == []
    # the profile moves on; this process's writes fail, its reads continue, a new process starts fine
    await admin.execute("UPDATE platform.deployment_profile SET epoch = 2")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(ProfileEpochMismatch):
            await db.run(fn, write=True)
        assert body == []
        await db.run(fn)
        assert body == [1]
    fresh = await make()
    assert fresh.profile_epoch == 2
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        await fresh.run(fn, write=True)
    assert body == [1, 1]
    await admin.close()


# ---- retries ------------------------------------------------------------------------------------------------
async def test_serialization_conflict_is_retried_and_both_commit(make) -> None:
    db = await make("indexing")
    attempts = collections.Counter()

    async def txn(tx, name: str) -> None:
        attempts[name] += 1
        if attempts[name] == 1:  # the server reports a conflict on the first attempt
            await tx.execute("DO $$ BEGIN RAISE EXCEPTION 'conflict' USING ERRCODE = '40001'; END $$")
        await tx.execute("INSERT INTO kx.docs (org_id, id, title) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', $1, 'ser')", f"ser-{name}")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        await asyncio.gather(*(db.run(lambda tx, k=k: txn(tx, k), write=True) for k in "ab"))
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.docs WHERE title = 'ser'") == 2
    assert attempts == {"a": 2, "b": 2}


async def test_write_units_of_work_refuse_isolation_other_than_read_committed(make) -> None:
    db = await make("indexing")
    ran = []
    for iso in ("repeatable_read", "serializable"):
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(UnsupportedIsolation):
            async with db.uow(write=True, isolation=iso) as tx:
                ran.append(await tx.fetchval("SELECT 1"))
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow(isolation="serializable") as tx:  # read-only units may
            assert await tx.fetchval("SELECT 1") == 1
    assert ran == []


async def test_deadlock_is_retried_and_both_commit(make) -> None:
    db = await make("indexing")
    attempts = collections.Counter()
    barrier = asyncio.Barrier(2)

    async def txn(tx, first, second, name) -> None:
        attempts[name] += 1
        await tx.execute("UPDATE kx.docs SET title = $2 WHERE id = $1", first, name)
        if attempts[name] == 1:
            await barrier.wait()
        await tx.execute("UPDATE kx.docs SET title = $2 WHERE id = $1", second, name)

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        await asyncio.gather(db.run(lambda tx: txn(tx, "aaaaaaaaaaaaaaaaaaaaaaaa-1", "aaaaaaaaaaaaaaaaaaaaaaaa-2", "x"), write=True),
                             db.run(lambda tx: txn(tx, "aaaaaaaaaaaaaaaaaaaaaaaa-2", "aaaaaaaaaaaaaaaaaaaaaaaa-1", "y"), write=True))
    assert sum(attempts.values()) >= 3


async def test_constraint_violations_and_timeouts_are_not_retried(make) -> None:
    db = await make("indexing")
    tries = 0

    async def dup(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'aaaaaaaaaaaaaaaaaaaaaaaa-1', 't')")

    async def slow(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("SELECT pg_sleep(1)")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(asyncpg.UniqueViolationError):
            await db.run(dup, write=True)
        assert tries == 1
        with pytest.raises(DbTimeout):
            await db.run(slow, timeout_ms=50)
        assert tries == 2


async def test_connection_killed_mid_transaction(env, make) -> None:
    db = await make("indexing")
    admin = await env.admin()
    tries = 0

    async def fn(tx) -> int:
        nonlocal tries
        tries += 1
        pid = await tx.fetchval("SELECT pg_backend_pid()")
        if tries == 1:
            await admin.execute("SELECT pg_terminate_backend($1)", pid)
            await asyncio.sleep(0.2)
        return await tx.fetchval("SELECT 42")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(ConnectionLost) as e:
            await db.run(fn)
        assert tries == 1 and e.value.retry_safe is False
        tries = 0
        assert await db.run(fn, idempotent=True) == 42 and tries == 2
    await admin.close()


async def test_connection_lost_after_commit_was_sent_is_never_retried(make, monkeypatch) -> None:
    db = await make("indexing")

    async def boom(self) -> Never:
        self._connection.terminate()
        raise asyncpg.ConnectionDoesNotExistError("connection was closed in the middle of operation")

    monkeypatch.setattr(asyncpg.transaction.Transaction, "commit", boom)
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'x')")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(CommitOutcomeUnknown):
        await db.run(fn, write=True, idempotent=True)
    assert tries == 1


# ---- timeouts ----------------------------------------------------------------------------------------------
async def test_statement_timeout_fires_and_the_next_unit_of_work_has_the_default(env, make) -> None:
    db = await make(port=env.start_bouncer(pool_size=1))
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(DbTimeout):
            async with db.uow(timeout_ms=50) as tx:
                await tx.execute("SELECT pg_sleep(0.5)")
        async with db.uow() as tx:  # the same single server connection
            assert await tx.fetchval("SELECT current_setting('statement_timeout')") == "5s"


async def test_timeouts_by_class_and_org_limits_only_lower(env, make_with_limits) -> None:
    async def hook(org: str) -> OrgLimits | None:
        if org == "aaaaaaaaaaaaaaaaaaaaaaaa":
            return OrgLimits(statement_timeout_ms=200, work_mem_kb=1024)
        if org == "bbbbbbbbbbbbbbbbbbbbbbbb":
            return OrgLimits(statement_timeout_ms=10**7, work_mem_kb=10**7)
        raise RuntimeError("limits store down")

    db = await make_with_limits(hook)
    async def settings(org: str) -> asyncpg.Record:
        with as_org(org):
            async with db.uow() as tx:
                return await tx.fetchrow("SELECT current_setting('statement_timeout') st, current_setting('work_mem') wm")

    assert dict(await settings("aaaaaaaaaaaaaaaaaaaaaaaa")) == {"st": "200ms", "wm": "1MB"}
    assert (await settings("bbbbbbbbbbbbbbbbbbbbbbbb"))["st"] == "5s"          # a higher limit never raises the class default
    assert (await settings("cccccccccccccccccccccccc"))["st"] == "5s"          # a failing hook leaves the defaults
    await db.close()
    for svc, want in (("indexing", "1min"), ("connectors", "1min")):
        d = KernelDB(DbConfig(service=svc, dsn=env.dsn(svc)))
        await d.start()
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with d.uow() as tx:
                assert await tx.fetchval("SELECT current_setting('statement_timeout')") == want
        await d.close()


async def test_lock_timeout_and_pool_wait_timeout(env, make, monkeypatch) -> None:
    monkeypatch.setitem(db_config.TIMEOUT_CLASSES, "indexing", db_config.TimeoutClass(60_000, 100))
    db = await make("indexing", pool_max=1)
    admin = await env.admin()
    holder = admin.transaction()
    await holder.start()
    await admin.execute("SELECT 1 FROM kx.docs WHERE id = 'aaaaaaaaaaaaaaaaaaaaaaaa-1' FOR UPDATE")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(LockTimeout):
            async with db.uow(write=True) as tx:
                await tx.execute("UPDATE kx.docs SET title = 'x' WHERE id = 'aaaaaaaaaaaaaaaaaaaaaaaa-1'")
    await holder.rollback()
    await admin.close()
    held, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow():
                held.set()
                await release.wait()

    holder_task = asyncio.create_task(hold())
    await held.wait()
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises(PoolTimeout):  # the only pooled connection is in use by another task
        async with db.uow(timeout_ms=150):
            pass
    release.set()
    await holder_task


# ---- pooling ------------------------------------------------------------------------------------------------
async def test_no_cross_org_rows_over_3000_pooled_transactions(env, make) -> None:
    db = await make(port=env.start_bouncer(pool_size=2), pool_max=20)
    pids: set[int] = set()
    bad: list = []
    unbound_ok = 0
    rnd = random.Random(7)
    plan = [rnd.choice((*ORGS, None)) for _ in range(3000)]
    in_flight = asyncio.Semaphore(40)  # bounded like a server's request concurrency; pool waits are limited by the class timeout

    async def one(i: int, org: str | None) -> None:
        async with in_flight:
            await one_inner(i, org)

    async def one_inner(i: int, org: str | None) -> None:
        nonlocal unbound_ok
        if org is None:
            try:
                async with db.uow():
                    bad.append((i, "unbound ran"))
            except oc.MissingOrgContext:
                unbound_ok += 1
            return
        with as_org(org, request_id=f"r{i}"):
            async def read(tx) -> asyncpg.Record:
                pids.add(await tx.fetchval("SELECT pg_backend_pid()"))
                if i % 3 == 0:
                    await tx.execute("INSERT INTO kx.log VALUES (current_setting('app.org_id'), $1)", f"r{i}")
                return await tx.fetchrow("SELECT count(*) n, count(DISTINCT org_id) d, min(org_id) lo, current_setting('app.org_id') cur FROM kx.docs")

            r = await db.run(read, write=(i % 3 == 0))
            if (r["n"], r["d"], r["lo"], r["cur"]) != (100, 1, org, org):
                bad.append((i, org, dict(r)))

    await asyncio.gather(*(one(i, o) for i, o in enumerate(plan)))
    assert bad == []
    assert unbound_ok == plan.count(None)
    assert len(pids) <= 2  # 3,000 transactions over two server connections
    admin = await env.admin()
    got = {r["org_id"]: r["n"] for r in await admin.fetch("SELECT org_id, count(*) n FROM kx.log GROUP BY 1")}
    want = collections.Counter(o for i, o in enumerate(plan) if o and i % 3 == 0)
    assert got == dict(want)
    await admin.close()


@pytest.mark.parametrize("max_prepared, pooler, works", [(200, "pgbouncer_txn", True), (0, "pgbouncer_txn", True), (0, "azure_pgbouncer", True), (0, "none", False)])
async def test_prepared_statements_behind_the_pooler(env, max_prepared, pooler, works) -> None:
    port = env.start_bouncer(max_prepared=max_prepared, pool_size=2)
    errors = 0
    db = KernelDB(DbConfig(service="query", dsn=env.dsn("query", port), pooler=pooler, pool_max=20))
    try:
        await db.start()
    except (DbError, asyncpg.PostgresError, asyncpg.InterfaceError):
        errors += 1
        db = None
    if db is not None:
        assert db.cfg.statement_cache_size == (100 if pooler == "none" else 0)  # the cache is on only for a direct connection

        async def one(i) -> None:
            nonlocal errors
            with as_org(ORGS[i % 4]):
                try:
                    async with db.uow() as tx:
                        await tx.fetchval(f"SELECT count(*) FROM kx.docs WHERE id <> ${1}" if i % 2 else "SELECT count(*) FROM kx.docs WHERE id = $1", "none")
                        await tx.fetchval("SELECT count(*) FROM kx.log WHERE note = $1", "n")
                except Exception:
                    errors += 1

        await asyncio.gather(*(one(i) for i in range(400)))
        await db.close()
    assert (errors == 0) is works, f"errors={errors}"


async def test_application_name_follows_each_client_through_the_pooler(env, make) -> None:
    port = env.start_bouncer(pool_size=1)
    a, b = await make("query", port), await make("indexing", port)
    for db, want in [(a, "pipeshub-query/dev"), (b, "pipeshub-indexing/dev")] * 5:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow() as tx:
                assert await tx.fetchval("SELECT current_setting('application_name')") == want


# One transaction (the kernel reset runs when the pooler releases the server connection, i.e. after each transaction).
LEAK_SCRIPT = ("BEGIN; SET ROLE pipeshub_indexing; SELECT set_config('app.leak', 'x', false); SELECT pg_advisory_lock(42); "
               "CREATE TEMP TABLE leak_t AS SELECT 1 AS a; DECLARE c CURSOR WITH HOLD FOR SELECT 1; COMMIT")
PROBE = ("SELECT (SELECT count(*) FROM pg_cursors WHERE name = 'c') cursors, (SELECT count(*) FROM pg_locks WHERE locktype = 'advisory') locks, "
         "coalesce(current_setting('app.leak', true), '') guc, current_setting('role') role, to_regclass('pg_temp.leak_t') IS NOT NULL temp")


async def _leak(env, port) -> None:
    leaker = await asyncpg.connect(env.dsn("indexing", port))
    await leaker.execute(LEAK_SCRIPT)
    await leaker.close()


async def test_session_state_left_by_one_client_is_invisible_to_the_next_with_the_kernel_reset(env, make) -> None:
    # the same script on a pooler WITHOUT the reset leaks everything (the test would pass vacuously otherwise)
    dirty = env.start_bouncer(reset=False, pool_size=1)
    await _leak(env, dirty)
    probe = await asyncpg.connect(env.dsn("indexing", dirty))
    assert dict(await probe.fetchrow(PROBE)) == {"cursors": 1, "locks": 1, "guc": "x", "role": "pipeshub_indexing", "temp": True}
    await probe.close()
    env.stop_bouncer(dirty)  # its server connection still holds the leaked advisory lock and temp table
    await asyncio.sleep(0.5)

    clean = env.start_bouncer(reset=True, pool_size=1)
    db = await make("indexing", clean)
    await _leak(env, clean)
    with as_org("bbbbbbbbbbbbbbbbbbbbbbbb"):
        async with db.uow() as tx:
            r = await tx.fetchrow(PROBE + ", current_setting('app.org_id') org")
    assert dict(r) == {"cursors": 0, "locks": 0, "guc": "", "role": "pipeshub_indexing", "temp": False, "org": "bbbbbbbbbbbbbbbbbbbbbbbb"}
    probe = await asyncpg.connect(env.dsn("indexing", clean))
    assert dict(await probe.fetchrow(PROBE)) == {"cursors": 0, "locks": 0, "guc": "", "role": "none", "temp": False}
    await probe.close()


# ---- cancellation -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pooled", [False, True])
async def test_cancellation_mid_unit_of_work_releases_the_connection_clean(env, make, pooled) -> None:
    port = env.start_bouncer(pool_size=1) if pooled else None
    db = await make("indexing", port, pool_max=1)
    closed: list[bool] = []
    real = db._release

    async def spy(pool, con) -> None:
        closed.append(connection_closed(con))
        await real(pool, con)

    db._release = spy
    started = asyncio.Event()

    async def job() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'cancelled')")
                started.set()
                await tx.execute("SELECT pg_sleep(30)")

    t = asyncio.create_task(job())
    await started.wait()
    await asyncio.sleep(0.1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert closed == [False]  # rolled back and kept: the server cancelled the statement
    admin = await env.admin()
    for _ in range(50):  # the server notices the dropped client
        left = await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
                                    "AND (state = 'idle in transaction' OR query LIKE '%pg_sleep(30)%' AND state = 'active')")
        if not left:
            break
        await asyncio.sleep(0.1)
    assert left == 0
    assert await admin.fetchval("SELECT count(*) FROM kx.log WHERE note = 'cancelled'") == 0
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):  # the pool of one works again
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT count(*) FROM kx.docs") == 100
    await admin.close()


# ---- query catalog, gate, lint ------------------------------------------------------------------------------
async def test_catalog_is_recorded_and_the_explain_gate_fails_rls_only_statements(make, tmp_path) -> None:
    db = await make()
    good = "/* pipeshub:t.get_node */ SELECT * FROM kx.big n WHERE n.org_id = $1 AND n.id = $2"
    rls_only = "/* pipeshub:t.get_node_rls_only */ SELECT * FROM kx.big n WHERE n.id = $1"
    with catalog.recording() as got, as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            await tx.fetch(good, "aaaaaaaaaaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaaaaaaaaaa-5")
            await tx.fetch(rls_only, "aaaaaaaaaaaaaaaaaaaaaaaa-5")
            await tx.fetchval("SELECT 1")
    entries = list(got.values())
    assert [e.tag for e in entries] == ["t.get_node", "t.get_node_rls_only", None] and len(catalog.untagged(entries)) == 1
    catalog.export(entries[:2], str(tmp_path / "query-catalog.json"))
    loaded = catalog.load(str(tmp_path / "query-catalog.json"))

    res = {r.tag: r for r in await catalog.explain_gate(db, loaded, org_id="aaaaaaaaaaaaaaaaaaaaaaaa", partitioned=["kx.big"], budget_ms=1000)}
    assert (res["t.get_node"].leaves, res["t.get_node"].ok) == (1, True)
    assert res["t.get_node_rls_only"].leaves >= 18 and not res["t.get_node_rls_only"].ok
    tight = {r.tag: r for r in await catalog.explain_gate(db, loaded[:1], org_id="aaaaaaaaaaaaaaaaaaaaaaaa", partitioned=["kx.big"], budget_ms=0.00001)}
    assert not tight["t.get_node"].ok  # the planning budget is part of the gate
    pytest.importorskip("sqlglot")
    assert sql_lint.org_predicate_violations(good, {"kx.big"}) == [] and sql_lint.org_predicate_violations(rls_only, {"kx.big"}) == ["n"]
