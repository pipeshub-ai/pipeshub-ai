"""Pre-commit probe, guard bypasses, shielded units, cancellation handlers, retries."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Never

import asyncpg
import pytest

from app.core.db import (
    FailedTransaction,
    UowUsageError,
)

from .test_failure_modes import as_org

pytestmark = pytest.mark.integration


class P2:
    """Proxy: match = bytes found in a client simple-query message; action 'drop' (close, no forward) or 'hold' (delay reply)."""

    def __init__(self, target, match, action, delay=0.0) -> None:
        self.target, self.match, self.action, self.delay, self.hits, self.armed = target, match, action, delay, 0, False
        self.ws = []

    async def start(self) -> None:
        self.srv = await asyncio.start_server(self._h, "127.0.0.1", 0)
        self.port = self.srv.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.srv.close()
        for w in self.ws:
            w.close()

    async def _h(self, r, w) -> None:
        ur, uw = await asyncio.open_connection("127.0.0.1", self.target)
        self.ws += [w, uw]
        st = {"hold": False}

        async def c2s() -> None:
            try:
                while d := await r.read(65536):
                    if self.armed and d[:1] in (b"Q", b"P") and self.match in d:
                        self.hits += 1
                        self.armed = False
                        if self.action == "drop":
                            break
                        st["hold"] = True
                    uw.write(d)
                    await uw.drain()
            except Exception:
                pass
            finally:
                w.close()
                uw.close()

        async def s2c() -> None:
            try:
                while d := await ur.read(65536):
                    if st["hold"]:
                        await asyncio.sleep(self.delay)
                        st["hold"] = False
                    w.write(d)
                    await w.drain()
            except Exception:
                pass
            finally:
                w.close()
                uw.close()

        await asyncio.gather(c2s(), s2c())


async def count(env: object, note: str) -> int:
    a = await env.admin()
    n = await a.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", note)
    await a.execute("DELETE FROM kx.log")
    await a.close()
    return n


# ---- guard tricks -----------------------------------------------------------------------------------------
TRICKS = {
    "comment_prefix": "/* */COMMIT",
    "lower_semicolon": "commit;",
    "mixed_case": "  CoMmIt ",
    "multi": "SELECT 1; COMMIT",
    "multi_rollback_comment": "SELECT 'a'; /*c*/ rollback",
    "do_block": "DO $$ BEGIN COMMIT; END $$",
    "E_string": "SELECT E'\\''; COMMIT; SELECT 'x'",
    "nested_comment": "/* /* */ */ COMMIT",
    "dquote_ident_dashes": 'SELECT 1 AS "x--"; COMMIT',
    "dquote_ident_blockcomment": 'SELECT 1 AS "x/*"; COMMIT; SELECT 1 AS "*/"',
    "begin": "BEGIN",
    "end_kw": "END",
    "release": "RELEASE SAVEPOINT x",
    "start_txn": "START TRANSACTION",
}


@pytest.mark.parametrize("name", list(TRICKS))
async def test_guard_tricks(env, make, name) -> None:
    db = await make("indexing")
    r = "OK"
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        try:
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','base')")
                try:
                    await tx.execute(TRICKS[name])
                    ran = True
                except Exception as e:
                    ran = type(e).__name__
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','after')") if ran is True else None
        except Exception as e:
            r = type(e).__name__
    a = await env.admin()
    base = await a.fetchval("SELECT count(*) FROM kx.log WHERE note='base'")
    after = await a.fetchval("SELECT count(*) FROM kx.log WHERE note='after'")
    await a.execute("DELETE FROM kx.log")
    await a.close()
    print(f"\nR3guard[{name}] stmt={ran} unit={r} base={base} after={after}")
    # safe = refused by the guard, or (unit raised and nothing committed)
    assert ran != True or (r != "OK" and base == 0 and after == 0), f"{name} ran unchecked: unit={r} base={base} after={after}"  # noqa: E712


# ---- probe ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("idem", [False, True])
async def test_probe_connection_dropped(env, make, idem) -> None:
    px = P2(env.cluster.port, b"SELECT now()", "drop")
    await px.start()
    try:
        db = await make("indexing", px.port, pool_max=2)
        tries = 0

        async def fn(tx) -> None:
            nonlocal tries
            tries += 1
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','probe')")

        px.armed = True
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            try:
                await db.run(fn, write=True, idempotent=idem)
                r = "OK"
            except Exception as e:
                r = type(e).__name__
        n = await count(env, "probe")
        print(f"\nR3probe-drop idem={idem}: {r} tries={tries} rows={n} hits={px.hits}")
        # not reporting success unless the row is really there exactly once
        assert not (r == "OK" and n != 1)
        assert r != "CommitOutcomeUnknown"  # COMMIT was never sent
        assert (tries, r) == ((2, "OK") if idem else (1, "ConnectionLost"))
    finally:
        await px.stop()


async def test_cancel_during_probe(env, make) -> None:
    px = P2(env.cluster.port, b"SELECT now()", "hold", delay=2.0)
    await px.start()
    try:
        db = await make("indexing", px.port, pool_max=1)

        async def job() -> None:
            with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
                async with db.uow(write=True) as tx:
                    await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','cp')")

        px.armed = True
        t = asyncio.create_task(job())
        await asyncio.sleep(0.5)
        t.cancel()
        try:
            await t
            r = "completed"
        except asyncio.CancelledError:
            r = "cancelled"
        n = await count(env, "cp")
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow() as tx:
                ok = await tx.fetchval("SELECT 1")
        a = await env.admin()
        idle = await a.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in trans%'")
        await a.close()
        print(f"\nR3cancel-in-probe {r} rows={n} next_ok={ok} idle_in_tx={idle}")
        assert r == "cancelled" and n == 0 and idle == 0
    finally:
        await px.stop()


async def test_shield_around_unit(env, make) -> None:
    db = await make("indexing", pool_max=1)

    async def unit() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','sh')")
                await asyncio.sleep(0.5)

    async def outer() -> None:
        await asyncio.shield(unit())

    t = asyncio.create_task(outer())
    await asyncio.sleep(0.2)
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    await asyncio.sleep(1.0)
    n = await count(env, "sh")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        async with db.uow() as tx:
            ok = await tx.fetchval("SELECT 1")
    print(f"\nR3shield rows={n} next_ok={ok}")
    assert ok == 1 and n in (0, 1)
    # shield, with the unit swallowed-error afterwards: must still fail
    async def bad() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            async with db.uow(write=True) as tx:
                with contextlib.suppress(Exception):
                    await tx.execute("SELECT 1/0")
    with pytest.raises(FailedTransaction):
        await asyncio.shield(bad())


async def test_cleanup_unit_after_caught_cancellation(env, make) -> None:
    """A common pattern: catch CancelledError, run a cleanup unit, re-raise. task.cancelling() stays > 0."""
    db = await make("indexing", pool_max=2)
    res = {}

    async def job() -> None:
        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                try:
                    async with db.uow(write=True) as tx:
                        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','cleanup')")
                    res["cleanup"] = "OK"
                except Exception as e:
                    res["cleanup"] = type(e).__name__
                raise

    t = asyncio.create_task(job())
    await asyncio.sleep(0.2)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    n = await count(env, "cleanup")
    print(f"\nR3cleanup-after-cancel {res} rows={n}")
    assert res["cleanup"] == "OK" and n == 1


async def test_uncancelled_after_timeout_scope(env, make) -> None:
    db = await make("indexing", pool_max=2)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        try:
            async with asyncio.timeout(0.1):
                await asyncio.sleep(1)
        except TimeoutError:
            pass
        async with db.uow(write=True) as tx:
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','after_timeout')")
    assert await count(env, "after_timeout") == 1


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, GeneratorExit])
async def test_baseexception_in_body(env, make, exc) -> Never:
    db = await make("indexing", pool_max=1)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(exc):
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','be')")
                raise exc()
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT 1") == 1
    a = await env.admin()
    assert await a.fetchval("SELECT count(*) FROM kx.log WHERE note='be'") == 0
    assert await a.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in trans%'") == 0
    await a.close()


async def test_swallowed_baseexception_statement(env, make) -> None:
    db = await make("indexing", pool_max=1)
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(FailedTransaction):
            async with db.uow(write=True) as tx:
                await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa','se')")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(tx.fetchval("SELECT pg_sleep(5)"), 0.1)


# ---- retries around the new ordering -------------------------------------------------------------------------
async def test_loss_before_commit_vs_after(env, make) -> None:
    db = await make("indexing", pool_max=2)
    a = await env.admin()
    out = {}
    for mode in ("before_commit", "idle_then_return"):
        tries = 0

        async def fn(tx, mode=mode) -> None:
            nonlocal tries
            tries += 1
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa',$1)", mode)
            if tries == 1:
                await a.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'")
                await asyncio.sleep(0.2)

        with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):
            try:
                await db.run(fn, write=True, idempotent=True)
                r = "OK"
            except Exception as e:
                r = type(e).__name__
        out[mode] = (r, tries, await a.fetchval("SELECT count(*) FROM kx.log WHERE note=$1", mode))
    # the connection is lost before COMMIT, so the unit re-runs once and the row exists exactly once, whichever way the body idles
    assert out == {"before_commit": ("OK", 2, 1), "idle_then_return": ("OK", 2, 1)}, out
    await a.execute("DELETE FROM kx.log")
    await a.close()


@pytest.mark.parametrize("sql", ["SELECT E'\\''; COMMIT; BEGIN; SELECT 'x'", "SELECT 1 AS \"x--\"; COMMIT; BEGIN", "/* /* */ */ COMMIT; BEGIN"])
async def test_commit_then_begin_in_one_string_fails_the_unit(env, make, sql: str) -> None:
    db = await make("indexing")
    tries = 0

    async def fn(tx) -> None:
        nonlocal tries
        tries += 1
        await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'cb-first')")
        await tx.execute(sql)  # the text guard misses these; the transaction status looks open again afterwards

    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises((FailedTransaction, UowUsageError)):
        await db.run(fn, write=True)
    assert tries == 1
    a = await env.admin()
    # the early COMMIT cannot be undone (the first row is there), but the unit failed loudly
    await a.execute("DELETE FROM kx.log")
    await a.close()
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"):  # the pool still works on a fresh connection
        async with db.uow() as tx:
            assert await tx.fetchval("SELECT 1") == 1


async def test_do_block_cannot_end_the_transaction(env, make) -> None:
    db = await make("indexing")
    with as_org("aaaaaaaaaaaaaaaaaaaaaaaa"), pytest.raises((FailedTransaction, asyncpg.PostgresError)):
        async with db.uow(write=True) as tx:
            await tx.execute("INSERT INTO kx.log VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'do-commit')")
            await tx.execute("DO $$ BEGIN COMMIT; END $$")
    a = await env.admin()
    assert await a.fetchval("SELECT count(*) FROM kx.log WHERE note = 'do-commit'") == 0
    await a.close()
