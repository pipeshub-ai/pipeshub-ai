"""Escapes of the failed-unit detection: COPY errors, cancellation, transaction control, deferred errors."""
from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import pytest

from app.core.db import DbTimeout

from .test_failure_modes import as_org

pytestmark = pytest.mark.integration


async def outcome(db: object, body: object, **kw: object) -> str:
    """Run body in a write unit; return ('OK'|exception name, rows with note)"""
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        try:
            async with db.uow(write=True, **kw) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'base')")
                await body(tx)
            return "OK"
        except Exception as e:
            return type(e).__name__


async def committed(env: object, note: str = "base") -> int:
    a = await env.admin()
    n = await a.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", note)
    await a.execute("DELETE FROM kx.log")
    await a.close()
    return n


async def swallow(coro, exc=Exception) -> None:
    with contextlib.suppress(exc):
        await coro


# --- must raise FailedTransaction (or any exception) and commit nothing ------------------------------------
CASES = {
    "execute": lambda tx: swallow(tx.execute("SELECT 1/0")),
    "fetch": lambda tx: swallow(tx.fetch("SELECT 1/0")),
    "fetchrow": lambda tx: swallow(tx.fetchrow("SELECT 1/0")),
    "fetchval": lambda tx: swallow(tx.fetchval("SELECT 1/0")),
    "executemany": lambda tx: swallow(tx.executemany("INSERT INTO kx.docs VALUES ($1,$2,'x')", [("aaaaaaaaaaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaaaaaaaaaa-1")])),
    "copy_into_temp": lambda tx: swallow(tx.copy_into_temp("st", {"a": "int"}, [("notint",)])),
    "timeout": None,
    "wait_for_timeout": lambda tx: swallow(asyncio.wait_for(tx.fetchval("SELECT pg_sleep(5)"), 0.2), asyncio.TimeoutError),
    "swallow_cancel": None,
    "raw_rollback": lambda tx: tx.execute("ROLLBACK"),
    "raw_commit": lambda tx: tx.execute("COMMIT"),
    "error_outside_after_savepoint_ok": None,
}


@pytest.mark.parametrize("name", [k for k, v in CASES.items() if v])
async def test_swallowed_must_not_commit_or_report_ok(env, make, name) -> None:
    db = await make("indexing")
    r = await outcome(db, CASES[name])
    n = await committed(env)
    print(f"\nR2[{name}] -> {r} base_row_committed={n}")
    assert r != "OK" or n == 1, f"{name}: reported OK but base row lost"
    assert r != "OK", f"{name}: reported OK"


async def test_swallowed_statement_timeout(env, make) -> None:
    db = await make("indexing")

    async def body(tx) -> None:
        await swallow(tx.fetchval("SELECT pg_sleep(2)"), DbTimeout)

    r = await outcome(db, body, timeout_ms=100)
    n = await committed(env)
    print(f"\nR2[stmt_timeout] -> {r} {n}")
    assert r == "FailedTransaction"


async def test_swallowed_cancelerror_inside_unit(env, make) -> None:
    db = await make("indexing")

    async def body(tx) -> None:
        t = asyncio.create_task(tx.fetchval("SELECT pg_sleep(5)"))
        await asyncio.sleep(0.2)
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t

    r = await outcome(db, body)
    n = await committed(env)
    print(f"\nR2[swallow_cancel_task] -> {r} {n}")
    assert r != "OK"


# --- must NOT poison -------------------------------------------------------------------------------------------
async def test_savepoint_rolled_back_does_not_poison(env, make) -> None:
    db = await make("indexing")

    async def body(tx) -> None:
        try:
            async with tx.savepoint("s1"):
                await tx.execute("INSERT INTO kx.docs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','aaaaaaaaaaaaaaaaaaaaaaaa-1','dup')")
        except asyncpg.UniqueViolationError:
            pass
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','after')")

    r = await outcome(db, body)
    a = await env.admin()
    after = await a.fetchval("SELECT count(*) FROM kx.log WHERE note='after'")
    base = await a.fetchval("SELECT count(*) FROM kx.log WHERE note='base'")
    await a.close()
    print(f"\nR2[savepoint ok] {r} base={base} after={after}")
    assert (r, base, after) == ("OK", 1, 1)


async def test_savepoint_nested_and_error_outside_poisons(env, make) -> None:
    db = await make("indexing")

    async def inner_ok(tx) -> None:
        async with tx.savepoint("a"):
            try:
                async with tx.savepoint("b"):
                    await tx.execute("SELECT 1/0")
            except asyncpg.DataError:
                pass
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','mid')")
    assert await outcome(db, inner_ok) == "OK"
    await committed(env)

    async def sp_then_outside_error(tx) -> None:
        try:
            async with tx.savepoint("a"):
                await tx.execute("SELECT 1/0")
        except asyncpg.DataError:
            pass
        await swallow(tx.execute("SELECT 1/0"))  # outside any savepoint

    assert await outcome(db, sp_then_outside_error) == "FailedTransaction"
    await committed(env)

    async def outside_first_then_savepoint(tx) -> None:
        await swallow(tx.execute("SELECT 1/0"))
        try:
            async with tx.savepoint("z"):
                await tx.execute("SELECT 1")
        except Exception:
            pass

    r = await outcome(db, outside_first_then_savepoint)
    print("\nR2 outside-then-savepoint ->", r)
    assert r != "OK"
    await committed(env)

    # error caught by the user's `except` that wraps the savepoint but the savepoint block caught it inside
    async def swallowed_inside_block(tx) -> None:
        async with tx.savepoint("q"):
            await swallow(tx.execute("SELECT 1/0"))

    assert await outcome(db, swallowed_inside_block) == "FailedTransaction"
    await committed(env)

    # savepoint error after an earlier rolled-back savepoint, then outside swallowed error
    async def clear_then_fail(tx) -> None:
        try:
            async with tx.savepoint("a"):
                await tx.execute("SELECT 1/0")
        except asyncpg.DataError:
            pass
        await swallow(tx.fetch("SELECT 1/0"))

    assert await outcome(db, clear_then_fail) == "FailedTransaction"


async def test_savepoint_user_cancelled_or_pg_catch(env, make) -> None:
    db = await make("indexing")

    async def plpgsql_catches(tx) -> None:  # server-side exception handled inside PL/pgSQL: txn healthy, no flag
        await tx.execute("DO $$ BEGIN PERFORM 1/0; EXCEPTION WHEN division_by_zero THEN NULL; END $$")
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','plpg')")

    assert await outcome(db, plpgsql_catches) == "OK"
    a = await env.admin()
    assert await a.fetchval("SELECT count(*) FROM kx.log WHERE note IN ('base','plpg')") == 2
    await a.close()


async def test_notice_does_not_poison(env, make) -> None:
    db = await make("indexing")

    async def body(tx) -> None:
        await tx.execute("DO $$ BEGIN RAISE NOTICE 'hi'; RAISE WARNING 'w'; END $$")

    assert await outcome(db, body) == "OK"


# --- errors at COMMIT --------------------------------------------------------------------------------------------
async def test_deferred_constraint_error_at_commit(env, make) -> None:
    a = await env.admin()
    await a.execute("""CREATE OR REPLACE FUNCTION kx.bad() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'nope' USING ERRCODE='23514'; END $$;
    CREATE CONSTRAINT TRIGGER bad_c AFTER INSERT ON kx.log DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION kx.bad()""")
    db = await make("indexing")
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','def')")

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(asyncpg.CheckViolationError):
            await db.run(fn, write=True)
    assert tries == 1
    assert await a.fetchval("SELECT count(*) FROM kx.log") == 0
    # connection usable
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT 1") == 1
    await a.close()


# --- reachability of raw connection ------------------------------------------------------------------------------
async def test_raw_connection_reachability(env, make) -> None:
    db = await make("indexing")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow(write=True) as tx:
            public = [a for a in dir(tx) if not a.startswith("_")]
            print("\nR2 public Tx attrs:", public)
            assert "con" not in public and "connection" not in public
