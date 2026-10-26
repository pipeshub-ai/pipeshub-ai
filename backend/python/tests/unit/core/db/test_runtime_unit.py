"""Pure tests of the database runtime: error mapping, retry table, config, capabilities, SQL lint, plan counting."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import asyncpg
import pytest
import yaml

from app.core import org_context
from app.core.db import (
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfig,
    DbConfigError,
    DbTimeout,
    DeadlockDetected,
    KernelDB,
    LockTimeout,
    ProfileEpochMismatch,
    PrologueFailed,
    SerializationConflict,
    UowUsageError,
    catalog,
    safe_request_id,
    sql_lint,
)
from app.core.db import capabilities as caps
from app.core.db.pool import PooledConnection
from app.core.db.uow import Tx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

BACKEND = Path(__file__).resolve().parents[5]


def _db() -> KernelDB:
    return KernelDB(DbConfig(service="query", dsn="postgresql://x@h/db"))


def _tx(**kw) -> Tx:
    t = Tx(None, _db(), klass="query", idempotent=kw.get("idempotent", False), request_id="r")  # type: ignore[arg-type]
    t.completed = kw.get("completed", 0)
    t.commit_sent = kw.get("commit_sent", False)
    return t


def _pg(cls: type, state: str) -> Exception:
    e = cls("boom")
    e.sqlstate = state
    return e


# ---- config -------------------------------------------------------------------------------------------------
def test_config_validates_and_derives_from_the_pooler() -> None:
    c = DbConfig(service="indexing", dsn="postgresql://x@h/db")
    assert (c.role, c.timeout_class, c.statement_cache_size) == ("pipeshub_indexing", "indexing", 0)  # default: a pooler
    for pooler in ("pgbouncer_txn", "azure_pgbouncer", "pgbouncer_session"):
        assert DbConfig(service="query", dsn="x", pooler=pooler).statement_cache_size == 0
    assert DbConfig(service="query", dsn="x", pooler="none").statement_cache_size == 100  # only a direct connection
    assert DbConfig(service="whatever", dsn="x").timeout_class == "api"
    assert "x@h" not in repr(DbConfig(service="query", dsn="postgresql://x:secret@h/db"))
    for bad in ({"service": "Bad Name; drop"}, {"dsn": ""}, {"pooler": "pgpool"}):
        with pytest.raises(DbConfigError):
            DbConfig(**{"service": "query", "dsn": "x", **bad})


def test_pooled_connection_sends_no_client_reset() -> None:
    assert PooledConnection.get_reset_query(object()) == ""  # type: ignore[arg-type]


def test_uow_has_no_org_parameter() -> None:
    import inspect

    for fn in (KernelDB.uow, KernelDB.jobs_uow, KernelDB.run):
        assert not [p for p in inspect.signature(fn).parameters if "org" in p]


# ---- request id ---------------------------------------------------------------------------------------------
def test_write_prologue_references_the_fence_output_in_the_outer_select() -> None:
    from app.core.db import uow

    outer = uow.PROLOGUE_WRITE.split("FROM pre")[0]
    assert "s.fence" in outer and "kernel.assert_profile_epoch($8) AS fence" in uow.PROLOGUE_WRITE
    assert "fence" not in uow.PROLOGUE_READ


def test_request_id_is_sanitised() -> None:
    assert safe_request_id("req-123:abc") == "req-123:abc"
    evil = "x'); DROP TABLE t; --" + "A" * 5000
    got = safe_request_id(evil)
    assert got == "invalid-" + hashlib.sha256(evil.encode()).hexdigest()[:16] and len(got) <= 64
    assert safe_request_id(None).startswith("invalid-") and safe_request_id("a" * 65).startswith("invalid-")


# ---- error mapping ------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc, kind",
    [
        (_pg(asyncpg.SerializationError, "40001"), SerializationConflict),
        (_pg(asyncpg.DeadlockDetectedError, "40P01"), DeadlockDetected),
        (_pg(asyncpg.QueryCanceledError, "57014"), DbTimeout),
        (_pg(asyncpg.LockNotAvailableError, "55P03"), LockTimeout),
        (_pg(asyncpg.ConnectionDoesNotExistError, "08003"), ConnectionLost),
        (_pg(asyncpg.AdminShutdownError, "57P01"), ConnectionLost),
        (asyncpg.InterfaceError("connection is closed"), ConnectionLost),
        (ConnectionResetError("reset"), ConnectionLost),
    ],
)
def test_driver_errors_are_typed(exc, kind) -> None:
    assert isinstance(_db().map_error(exc, _tx()), kind)


def test_unclassified_errors_pass_through_and_timeouts_are_not_connection_loss() -> None:
    db, tx = _db(), _tx()
    unique = _pg(asyncpg.UniqueViolationError, "23505")
    assert db.map_error(unique, tx) is unique
    assert db.map_error(TimeoutError(), tx).__class__ is TimeoutError
    assert isinstance(db.map_error(_pg(asyncpg.InsufficientPrivilegeError, "42501"), tx, prologue=True), PrologueFailed)
    assert isinstance(db.map_error(_pg(asyncpg.ObjectNotInPrerequisiteStateError, "55000"), tx, prologue=True), ProfileEpochMismatch)
    assert db.map_error(_pg(asyncpg.ObjectNotInPrerequisiteStateError, "55000"), tx).__class__ is asyncpg.ObjectNotInPrerequisiteStateError


@pytest.mark.parametrize(
    "tx_kw, safe",
    [({}, True), ({"completed": 2}, False), ({"completed": 2, "idempotent": True}, True),
     ({"commit_sent": True, "idempotent": True}, False)],
)
def test_connection_loss_retry_safety(tx_kw, safe) -> None:
    lost = _db().map_error(asyncpg.InterfaceError("closed"), _tx(**tx_kw))
    assert lost.retry_safe is safe


# ---- retry table -------------------------------------------------------------------------------
class _FakeDb(KernelDB):
    """``run`` against a scripted ``uow``: each attempt raises the next item or returns."""

    def __init__(self, script) -> None:
        super().__init__(DbConfig(service="query", dsn="x"))
        self.script, self.attempts = list(script), 0

    def uow(self, **_) -> object:
        @asynccontextmanager
        async def cm() -> AsyncIterator[object]:
            self.attempts += 1
            item = self.script.pop(0)
            if item is not None:
                raise item
            yield object()

        return cm()


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def fake(s) -> None:
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return slept


async def _body(_tx) -> str:
    return "ok"


async def test_serialization_and_deadlock_retry_then_succeed(no_sleep) -> None:
    db = _FakeDb([SerializationConflict("a"), DeadlockDetected("b"), None])
    assert await db.run(_body) == "ok" and db.attempts == 3
    assert len(no_sleep) == 2 and 0.01 <= no_sleep[0] < 0.02 and 0.02 <= no_sleep[1] < 0.04  # jittered exponential, 10 ms base


async def test_retries_stop_after_three(no_sleep) -> None:
    db = _FakeDb([SerializationConflict("x")] * 4 + [None])
    with pytest.raises(SerializationConflict):
        await db.run(_body)
    assert db.attempts == 4


@pytest.mark.parametrize("err", [DbTimeout("t"), PrologueFailed("p"), CommitOutcomeUnknown("c"),
                                 ConnectionLost("gone", retry_safe=False), asyncpg.UniqueViolationError("u"), ValueError("v")])
async def test_everything_else_is_not_retried(err, no_sleep) -> None:
    db = _FakeDb([err, None])
    with pytest.raises(type(err)):
        await db.run(_body)
    assert db.attempts == 1 and not no_sleep


async def test_safe_connection_loss_is_retried(no_sleep) -> None:
    db = _FakeDb([ConnectionLost("gone", retry_safe=True), None])
    assert await db.run(_body) == "ok" and db.attempts == 2


# ---- fail closed without an org ----------------------------------------------------------------------------
async def test_no_org_context_raises_before_any_connection() -> None:
    db = _db()
    db._pool = object()  # type: ignore[assignment]  # would fail with AttributeError if a connection were acquired
    before = org_context.MissingOrgContext
    with pytest.raises(before):
        async with db.uow():
            pytest.fail("body must not run")
    with org_context.system("test"), pytest.raises(org_context.SystemContextNotAllowed):
        async with db.uow():
            pytest.fail("body must not run")


async def test_jobs_uow_needs_a_job_context_and_a_jobs_dsn() -> None:
    db = _db()
    with org_context.bind(org_context.OrgContext("o", "u", "user", "jwt", "r")), pytest.raises(org_context.SystemContextNotAllowed):
        async with db.jobs_uow():
            pytest.fail("a request cannot run jobs")
    db._pool = object()  # type: ignore[assignment]
    with org_context.for_org("o", job="t"), pytest.raises(DbConfigError):
        async with db.jobs_uow():
            pytest.fail("no jobs DSN")


# ---- capabilities -------------------------------------------------------------------------------------------
def _row(pg: int = 180006, ext: dict | None = None, preload: str | None = "", allow: str | None = None) -> dict:
    return {"pg_num": pg, "ext": ext or {}, "preload": preload, "azure_allowlist": allow}


_TRGM = {"pg_trgm": {"installed": "1.6", "available": "1.6"}}


def test_builtin_table_matches_the_shared_yaml() -> None:
    assert yaml.safe_load((BACKEND / "db" / "capabilities.yaml").read_text()) == caps.DEFAULT_TABLE


def test_capabilities_ok_and_flags() -> None:
    t = caps.DEFAULT_TABLE
    c = caps.evaluate(_row(ext={**_TRGM, "vector": {"installed": "0.8.2"}}), t, strict=True, want=("vector",))
    assert c.pg_major == 18 and c.flags == {"hnsw_vacuum_safe": False, "bm25": False, "fused_vector": True}
    c = caps.evaluate(_row(170011, {**_TRGM, "vector": {"installed": "0.8.7"}}), t, strict=True, want=("vector",))
    assert c.flags["hnsw_vacuum_safe"] is True and "pg=17" in c.summary()


@pytest.mark.parametrize(
    "row, want, text",
    [(_row(160009, _TRGM), (), "below the supported floor"),
     (_row(190000, _TRGM), (), "newer than the tested"),
     (_row(), (), "pg_trgm is absent"),
     (_row(ext={"pg_trgm": {"installed": None, "available": "1.6"}}), (), "pg_trgm is available"),
     (_row(ext={"pg_trgm": {"installed": None, "available": "1.6"}}, allow="vector"), (), "not_allow_listed"),
     (_row(ext=_TRGM), ("vector",), "vector is absent"),
     (_row(ext={**_TRGM, "vector": {"installed": "0.7.4"}}), ("vector",), "below the floor 0.8.0")],
)
def test_strict_capability_refusals_name_the_remedy(row, want, text) -> None:
    with pytest.raises(caps.CapabilityError, match=text):
        caps.evaluate(row, caps.DEFAULT_TABLE, strict=True, want=want)


def test_non_strict_downgrades_instead_of_refusing() -> None:
    c = caps.evaluate(_row(ext=_TRGM), caps.DEFAULT_TABLE, strict=False, want=("vector",))
    assert c.flags["fused_vector"] is False and any("vector" in w for w in c.warnings)


# ---- SQL lint -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql, rule",
    [("SET statement_timeout = 5", "session SET"), ("reset all", "session SET"), ("SET ROLE x", "session SET"),
     ("SELECT set_config('app.org_id', $1, false)", "set_config"), ("DECLARE c CURSOR FOR SELECT 1", "DECLARE"),
     ("DECLARE c CURSOR WITH HOLD FOR SELECT 1", "WITH HOLD"), ("LISTEN x", "LISTEN"), ("PREPARE p AS SELECT 1", "PREPARE"),
     ("DISCARD ALL", "DISCARD"), ("SAVEPOINT s1", "savepoint"), ("ROLLBACK TO SAVEPOINT s1", "savepoint"), ("COMMIT", "savepoint"),
     ("SELECT set_config('app.org_id', 'x', true)", "re-binding"), ("SELECT set_config('role', 'x', true)", "re-binding"),
     ("SET LOCAL app.org_id = 'x'", "session SET"), ("SET LOCAL ROLE x", "session SET"), ("SELECT pg_advisory_lock(1)", "advisory"), ("SELECT pg_advisory_unlock_all()", "advisory"),
     ("SELECT pg_advisory_xact_lock(7341, 1)", "reserved to the kernel"), ("SELECT pg_try_advisory_xact_lock_shared(7350, 5)", "reserved to the kernel"),
     ("SELECT pg_advisory_xact_lock(7340,1)", "reserved to the kernel"),
     ("CREATE TEMP TABLE t (a int)", "ON COMMIT DROP"), ("SELECT 1; SET ROLE x", "session SET")],
)
def test_session_sql_is_flagged(sql, rule) -> None:
    assert any(rule in r for r in sql_lint.sql_rules(sql)), sql_lint.sql_rules(sql)


@pytest.mark.parametrize(
    "sql",
    ["SELECT set_config('statement_timeout', $1, true)", "UPDATE t SET a = 1 WHERE b = 2", "INSERT INTO t VALUES (1) ON CONFLICT (a) DO UPDATE SET b = 2",
     "CREATE TEMP TABLE t (a int) ON COMMIT DROP", "SELECT pg_try_advisory_xact_lock(1)", "SELECT pg_advisory_xact_lock(7342, 1)", "SELECT 'set role' AS x /* SET ROLE */"],
)
def test_transaction_scoped_sql_is_clean(sql) -> None:
    assert sql_lint.sql_rules(sql) == []


def test_scan_source_finds_calls_not_prose() -> None:
    src = '''
async def f(con, redis, name):
    await con.fetch("/* pipeshub:m.ok */ SELECT 1")
    await con.fetch("SELECT 1")
    await con.execute(f"/* pipeshub:m.f */ SELECT * FROM t WHERE a = '{name}'")
    await con.execute("/* pipeshub:m.s */ SET ROLE x")
    await con.execute("SELECT %s" % name)
    await redis.execute("flush the cache")
    await con.fetchrow(query)
'''
    rules = sorted(f.rule for f in sql_lint.scan_source(src))
    assert rules == ["session SET / RESET", "statement without a /* pipeshub:<module>.<fn> */ tag",
                     "string-built SQL (use bound parameters)", "string-built SQL (use bound parameters)"]


def test_no_caller_of_the_runtime_breaks_the_rules() -> None:
    """Every module that imports the runtime (outside the runtime itself) is scanned; none exist yet."""
    app = BACKEND / "python" / "app"
    users = [p for p in app.rglob("*.py") if "app.core.db" in p.read_text(encoding="utf-8", errors="ignore") and "core/db" not in str(p)]
    assert sql_lint.scan_paths(users) == []


def test_org_predicate_lint_matches_the_documented_rule() -> None:
    pytest.importorskip("sqlglot")
    part = {"kx.big"}
    ok = ["SELECT * FROM kx.big n WHERE n.org_id = $1 AND n.id = $2",
          "SELECT c.id FROM kx.big p JOIN kx.big c ON c.org_id = $1 AND c.parent_id = p.id WHERE p.org_id = $1 AND p.id = $2"]
    bad = {"SELECT * FROM kx.big n WHERE n.id = $1": ["n"],
           "SELECT c.id FROM kx.big p JOIN kx.big c ON c.org_id = p.org_id AND c.parent_id = p.id WHERE p.org_id = $1": ["c"],
           "SELECT p.id FROM kx.big p WHERE p.org_id = $1 AND EXISTS (SELECT 1 FROM kx.big c WHERE c.parent_id = p.id AND c.org_id = p.org_id)": ["c"]}
    for sql in ok:
        assert sql_lint.org_predicate_violations(sql, part) == [], sql
    for sql, aliases in bad.items():
        assert sql_lint.org_predicate_violations(sql, part) == aliases, sql
    assert sql_lint.org_predicate_violations("SELECT * FROM other WHERE id = $1", part) == []


# ---- catalog and plan counting ------------------------------------------------------------------------------
def test_recording_exports_and_flags_untagged(tmp_path) -> None:
    with catalog.recording() as got:
        catalog.record("/* pipeshub:a.b */ SELECT $1", ("x",))
        catalog.record("SELECT 2", ())
        catalog.record("/* pipeshub:a.b */ SELECT $1", ("y",))  # same text: first sample kept
    entries = list(got.values())
    assert [e.tag for e in entries] == ["a.b", None] and entries[0].args == ("x",)
    assert [e.sql for e in catalog.untagged(entries)] == ["SELECT 2"]
    catalog.export(entries, str(tmp_path / "c.json"))
    assert catalog.load(str(tmp_path / "c.json")) == entries
    catalog.record("SELECT 3", ())  # not recording: no effect
    assert len(got) == 2


def test_plan_leaves_counts_surviving_and_removed_leaves() -> None:
    roots = {f"big_{i}": "kx.big" for i in range(65)}
    pruned = {"Node Type": "Append", "Subplans Removed": 64, "Plans": [{"Node Type": "Index Scan", "Relation Name": "big_3"}]}
    rls_only = {"Node Type": "Append", "Plans": [{"Node Type": "Seq Scan", "Relation Name": f"big_{i}"} for i in range(65)]}
    assert catalog.plan_leaves(pruned, roots, []) == [("kx.big", 65)] or catalog.plan_leaves(pruned, roots, []) == [("kx.big", 1 + 64)]
    assert catalog.plan_leaves(rls_only, roots, []) == [("kx.big", 65)]
    assert catalog.plan_leaves({"Node Type": "Index Scan", "Relation Name": "big_3"}, roots, []) == [("kx.big", 1)]


async def test_shared_db_is_one_per_config_and_closed_by_the_last_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.db import shared

    events: list[str] = []

    async def start(self: KernelDB) -> None:
        events.append("start")

    async def close(self: KernelDB) -> None:
        events.append("close")

    monkeypatch.setattr(KernelDB, "start", start)
    monkeypatch.setattr(KernelDB, "close", close)
    cfg = DbConfig(service="query", dsn="postgresql://x@h/db")
    dbs = [await shared.acquire_db(cfg) for _ in range(100)]
    assert len({id(d) for d in dbs}) == 1 and events == ["start"]
    for _ in range(99):
        await shared.release_db(cfg)
    assert events == ["start"]
    await shared.release_db(cfg)
    assert events == ["start", "close"] and not shared._entries


def test_metric_names_do_not_collide_with_the_other_telemetry_modules() -> None:
    """All metrics share one registry: importing every module must not raise 'Duplicated timeseries'."""
    import importlib
    import pkgutil

    import app.core.db.uow  # noqa: F401
    import app.telemetry.modules as modules

    for m in pkgutil.iter_modules(modules.__path__):
        importlib.import_module(f"app.telemetry.modules.{m.name}")


def test_statements_that_change_the_units_identity_are_refused_at_runtime() -> None:
    from app.core.db.uow import _COMMENTS, _REBIND

    def refused(sql: str) -> bool:
        return bool(_REBIND.search(_COMMENTS.sub(" ", sql)))

    for sql in ("SELECT set_config('app.org_id','org_b',true)", "select set_config( 'role', 'x', true)", "SET LOCAL app.org_id = 'x'",
                "SET ROLE x", "set local role x", "SELECT 1; SET ROLE x", "/* c */ SET ROLE x", "-- c\nSET ROLE x", "SET SESSION AUTHORIZATION x",
                "DO $$ BEGIN SET ROLE x; END $$", "DO $$ BEGIN EXECUTE 'SET ROLE x'; END $$", "DO $$ BEGIN IF true THEN SET LOCAL app.org_id = 'x'; END IF; END $$"):
        assert refused(sql), sql
    for sql in ("SELECT set_config('statement_timeout', '1s', true)", "UPDATE t SET a = 1", "SELECT 'x'",
                "UPDATE users SET role = $1 WHERE id = $2", "UPDATE t SET a = 1, role = 'x'",
                "INSERT INTO u (id, role) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET role = excluded.role"):
        assert not refused(sql), sql


@pytest.mark.parametrize("sql", ["BEGIN", "commit", "ROLLBACK", "END", "abort", "SAVEPOINT s", "RELEASE s", "ROLLBACK TO SAVEPOINT s",
                                 "START TRANSACTION", "PREPARE TRANSACTION 'x'", "COMMIT PREPARED 'x'", "/* c */ rollback",
                                 "-- c\nCOMMIT", "SELECT 1; COMMIT", "  \n begin ; select 1"])
async def test_transaction_control_is_refused_at_runtime(sql: str) -> None:
    tx = Tx(object(), _db(), klass="query", idempotent=False, request_id="r")  # type: ignore[arg-type]
    with pytest.raises(UowUsageError):
        await tx.execute(sql)
    assert not tx.failed


def test_transaction_control_check_ignores_bodies_and_literals() -> None:
    from app.core.db.uow import _SQL_NOISE, _TXN_CONTROL

    def hit(sql: str) -> bool:
        return any(_TXN_CONTROL.match(" ".join(st.lower().split())) for st in _SQL_NOISE.sub(" ", sql).split(";"))

    assert not hit("DO $$ BEGIN RAISE EXCEPTION 'x'; END $$")
    assert not hit("SELECT 'commit; rollback' AS a")
    assert not hit("DO $f$ BEGIN PERFORM 1; EXCEPTION WHEN others THEN NULL; END $f$")
    assert hit("SELECT 1; ROLLBACK")


class _FakePool:
    terminated = 0
    threads: ClassVar[list[int]] = []

    async def close(self) -> None:
        return None

    def terminate(self) -> None:
        import threading

        _FakePool.terminated += 1
        _FakePool.threads.append(threading.get_ident())


def _side_db(monkeypatch: pytest.MonkeyPatch) -> tuple[KernelDB, list[_FakePool]]:
    made: list[_FakePool] = []

    async def fake_create_pool(*_a: object, **_k: object) -> _FakePool:
        made.append(_FakePool())
        return made[-1]

    monkeypatch.setattr("app.core.db.uow.create_pool", fake_create_pool)
    db = KernelDB(DbConfig(service="indexing", dsn="postgresql://x@h/db"))
    db._pool = _FakePool()  # type: ignore[assignment]
    return db, made


def test_a_unit_started_on_another_loop_gets_a_pool_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    db, made = _side_db(monkeypatch)
    home = db._pool

    async def here() -> object:
        db._home_loop = asyncio.get_running_loop()
        return await db._pool_here(jobs=False)

    async def there() -> list[object]:
        return await asyncio.gather(db._pool_here(jobs=False), db._pool_here(jobs=False))

    assert asyncio.run(here()) is home
    a, b = asyncio.run(there())  # a different loop than the home one
    assert a is b and a is not home and len(made) == 1


def test_pools_of_ended_loops_are_terminated_and_do_not_pile_up(monkeypatch: pytest.MonkeyPatch) -> None:
    db, made = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()  # never the loop the lookups run on
    _FakePool.terminated = 0
    for _ in range(20):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(db._pool_here(jobs=False))
        loop.close()
        assert len(db._side_pools) <= 1
    assert len(made) == 20 and _FakePool.terminated == 19  # the last loop's pool goes at the next lookup or at close()
    asyncio.run(db.close())
    assert _FakePool.terminated == 20 and len(db._side_pools) == 0


def test_the_number_of_live_side_loops_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.db.uow import MAX_SIDE_LOOPS

    db, _ = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()
    loops = [asyncio.new_event_loop() for _ in range(MAX_SIDE_LOOPS + 1)]
    for lp in loops[:MAX_SIDE_LOOPS]:
        lp.run_until_complete(db._pool_here(jobs=False))
    with pytest.raises(DbConfigError):
        loops[-1].run_until_complete(db._pool_here(jobs=False))
    for lp in loops:
        lp.close()


def test_a_failed_side_pool_creation_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    db, made = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()
    calls = {"n": 0}

    async def flaky(*_a: object, **_k: object) -> _FakePool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("pgbouncer briefly unreachable")
        return _FakePool()

    monkeypatch.setattr("app.core.db.uow.create_pool", flaky)
    loop = asyncio.new_event_loop()
    with pytest.raises(OSError, match="briefly"):
        loop.run_until_complete(db._pool_here(jobs=False))
    pool = loop.run_until_complete(db._pool_here(jobs=False))          # the next lookup tries again
    assert isinstance(pool, _FakePool) and calls["n"] == 2
    assert loop.run_until_complete(db._pool_here(jobs=False)) is pool and calls["n"] == 2
    loop.close()


def test_a_cancelled_first_caller_does_not_cancel_or_poison_the_shared_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    db, _ = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()
    calls = {"n": 0}

    async def slow(*_a: object, **_k: object) -> _FakePool:
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return _FakePool()

    monkeypatch.setattr("app.core.db.uow.create_pool", slow)
    loop = asyncio.new_event_loop()

    async def scenario() -> tuple[object, object]:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(db._pool_here(jobs=False), timeout=0.05)
        return await db._pool_here(jobs=False), await db._pool_here(jobs=False)

    a, b = loop.run_until_complete(scenario())
    assert a is b and calls["n"] == 1            # the creation the first caller started was kept, not restarted
    loop.close()


def test_side_pool_lookups_from_several_threads_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    db, _ = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()
    errors: list[BaseException] = []
    loops = [asyncio.new_event_loop() for _ in range(4)]

    def work(lp: asyncio.AbstractEventLoop) -> None:
        try:
            for _ in range(50):
                lp.run_until_complete(db._pool_here(jobs=False))
                lp.run_until_complete(db._pool_here(jobs=True))
        except BaseException as e:
            errors.append(e)

    db.cfg = DbConfig(service="indexing", dsn="postgresql://x@h/db", jobs_dsn="postgresql://x@h/db")
    db._jobs_pool = _FakePool()  # type: ignore[assignment]
    threads = [threading.Thread(target=work, args=(lp,)) for lp in loops]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(db._side_pools) == 4
    for lp in loops:
        lp.close()


def test_close_terminates_closed_loop_pools_and_leaves_a_running_loops_to_that_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    db, made = _side_db(monkeypatch)
    db._home_loop = asyncio.new_event_loop()
    _FakePool.terminated = 0
    closed = asyncio.new_event_loop()
    closed.run_until_complete(db._pool_here(jobs=False))
    closed.close()
    live = asyncio.new_event_loop()
    live.run_until_complete(db._pool_here(jobs=False))
    ready, stop = threading.Event(), threading.Event()

    def run_live() -> None:
        async def idle() -> None:
            ready.set()
            await asyncio.get_running_loop().run_in_executor(None, stop.wait)
        live.run_until_complete(idle())

    t = threading.Thread(target=run_live)
    t.start()
    ready.wait()
    _FakePool.threads.clear()
    asyncio.run(db.close())                       # does not touch the running loop's pool from this thread
    stop.set()
    t.join()
    assert _FakePool.terminated == 2
    assert threading.get_ident() not in _FakePool.threads and t.ident in _FakePool.threads   # the live loop ran it (call_soon_threadsafe)
    assert db._side_pools == {}
    live.close()


def test_pool_sizes_idle_time_and_minimum_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PG_CORE_URL", "postgresql://x@h/db")
    for key in ("PG_POOL_MAX", "PG_JOBS_POOL_MAX", "PG_POOL_IDLE_SECONDS", "PG_POOL_MIN"):
        monkeypatch.delenv(key, raising=False)
    c = DbConfig.from_env("indexing")
    assert (c.pool_max, c.jobs_pool_max, c.pool_idle_seconds, c.pool_min) == (10, 3, 300.0, 1)  # unchanged for every other deployment
    monkeypatch.setenv("PG_POOL_MAX", "4")
    monkeypatch.setenv("PG_JOBS_POOL_MAX", "2")
    monkeypatch.setenv("PG_POOL_IDLE_SECONDS", "30")
    monkeypatch.setenv("PG_POOL_MIN", "0")
    c = DbConfig.from_env("indexing")
    assert (c.pool_max, c.jobs_pool_max, c.pool_idle_seconds, c.pool_min) == (4, 2, 30.0, 0)
    monkeypatch.setenv("PG_POOL_MAX", "0")
    with pytest.raises(DbConfigError):
        DbConfig.from_env("indexing")


def test_an_empty_pooler_setting_means_the_default(monkeypatch) -> None:
    # Compose passes unset variables as "${VAR:-}", so an empty value must behave like an absent one.
    monkeypatch.setenv("PG_CORE_URL", "postgresql://x@h/db")
    monkeypatch.setenv("PG_POOLER", "")
    assert DbConfig.from_env("indexing").pooler == "pgbouncer_txn"
    monkeypatch.setenv("PG_POOLER", " none ")
    assert DbConfig.from_env("indexing").pooler == "none"


def test_sql_start_pattern_is_linear_on_unterminated_comment_runs() -> None:
    import time

    from app.core.db import sql_lint

    assert sql_lint._SQL_START.match("/* tag */ /* more */\n  select 1")
    assert not sql_lint._SQL_START.match("not sql at all")
    started = time.monotonic()
    sql_lint._SQL_START.match("/*" + "*//*" * 20000 + "x")
    assert time.monotonic() - started < 1.0
