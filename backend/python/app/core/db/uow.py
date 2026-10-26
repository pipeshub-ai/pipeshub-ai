"""KernelDB: pools, the unit of work, the prologue and the retry table.

Every statement runs inside a unit of work (UoW): one transaction whose first statement (the prologue) switches to the
service role and writes the org, request id, timeouts and plan mode with ``set_config(..., true)``. Nothing is ever set
at session level, so a pooled server connection carries no state from one client to the next.

The org comes only from the ambient :mod:`app.core.org_context`; there is no parameter to pass one.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import random
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import asyncpg

from app.core import org_context
from app.core.db import capabilities as caps_mod
from app.core.db import catalog
from app.core.db.config import TIMEOUT_CLASSES, DbConfig
from app.core.db.errors import (
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfigError,
    DbError,
    DbTimeout,
    DeadlockDetected,
    FailedTransaction,
    LockTimeout,
    PoolTimeout,
    ProfileEpochMismatch,
    PrologueFailed,
    RetryableConflict,
    SerializationConflict,
    SessionRoleLeak,
    UnsupportedIsolation,
    UowUsageError,
)
from app.core.db.pool import create_pool
from app.telemetry.backend import METRICS_BACKEND

logger = logging.getLogger(__name__)
T = TypeVar("T")
Isolation = Literal["read_committed", "repeatable_read", "serializable"]

_BUCKETS = (0.001, 0.005, 0.02, 0.1, 0.5, 2, 10, 60)
QUERY_SECONDS = METRICS_BACKEND.histogram("pipeshub_pg_query_seconds", "Statement duration", ["service", "site", "class"], _BUCKETS)
UOW_SECONDS = METRICS_BACKEND.histogram("pipeshub_pg_uow_seconds", "Unit of work duration", ["service", "outcome"], _BUCKETS)
POOL_WAIT = METRICS_BACKEND.histogram("pipeshub_pg_pool_wait_seconds", "Wait for a pooled connection", ["service"], _BUCKETS)
RETRIES = METRICS_BACKEND.counter("pipeshub_pg_retries_total", "Unit-of-work retries", ["code"])
ROLE_LEAKS = METRICS_BACKEND.counter("pipeshub_pg_session_role_leak_total", "Session-level SET ROLE found on a pooled connection", ["service"])
ORG_MISSING = METRICS_BACKEND.counter("pipeshub_org_context_missing_total", "Unit of work started without a usable org context", ["service"])

_SITE = re.compile(r"/\*\s*pipeshub:([A-Za-z0-9_.]{1,80})\s*\*/")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_IDENT = re.compile(r"[a-z_][a-z0-9_]{0,62}")
# A statement may not change the unit's identity (org, role) or its request id once the prologue has set them.
# The session form of SET counts only where a statement can start (text start, after ';', a quote or '$' so that EXECUTE 'SET ROLE ..' and
# dollar-quoted bodies are covered, or a PL/pgSQL BEGIN/THEN/ELSE/LOOP): `UPDATE t SET role = $1` sets a column and is allowed.
_REBIND = re.compile(
    r"set_config\s*\(\s*'(app\.|role|session_authorization)"
    r"|(?:^|;|'|\$|\b(?:begin|then|else|loop)\b)\s*set\s+(local\s+|session\s+)?(app\.|role\b|session\s+authorization)",
    re.I,
)
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)
# Transaction control belongs to the runtime (and to tx.savepoint); a statement that ends or splits the unit is refused.
_TXN_CONTROL = re.compile(r"(begin|commit|rollback|end|abort|savepoint|release|start\s+transaction|prepare\s+transaction)\b")
# Comments, dollar-quoted bodies (DO blocks, function bodies) and string literals are blanked before looking at statements.
_SQL_NOISE = re.compile(r"/\*.*?\*/|--[^\n]*|(\$[A-Za-z_]*\$).*?\1|'(?:[^']|'')*'", re.S)
COMMIT_PROBE = True  # ask the server before COMMIT whether the transaction is still alive (write units)
_current_tx: contextvars.ContextVar[Tx | None] = contextvars.ContextVar("pg_current_tx", default=None)
_COLTYPE = re.compile(r"[a-z][a-z0-9_ ]{0,40}(\(\d{1,4}(,\d{1,4})?\))?(\[\])?")
_LOST_STATES = {"57P01", "57P02", "57P03", "25P03"}
_ISOLATION = ("read_committed", "repeatable_read", "serializable")
RETRIES_MAX = 3
BACKOFF_BASE_S = 0.01
JOBS_ROLE = "pipeshub_jobs"
ABORT_TIMEOUT_S = 5.0

# One round trip. ``pre`` is read before the role switch; the LATERAL subquery is correlated so it runs after it.
# A role other than 'none' here is a session-level SET ROLE left behind by an earlier client.
# The statement is parsed as the LOGIN, which holds no privileges until the switch, so only write UoWs (which call the
# fence function) reference ``kernel``; the login needs USAGE on that schema and EXECUTE on the fence for them.
_PROLOGUE = """
WITH pre AS MATERIALIZED (SELECT current_setting('role') AS r)
SELECT pre.r, now() AS started{fence_out} FROM pre, LATERAL (
  SELECT set_config('role', $1, true), set_config('app.org_id', $2, true), set_config('app.request_id', $3, true),
         set_config('statement_timeout', $4, true), set_config('lock_timeout', $5, true),
         set_config('plan_cache_mode', $6, true),
         CASE WHEN $7 <> '' THEN set_config('work_mem', $7, true) END{fence}
  WHERE pre.r IS NOT NULL) s
"""
PROLOGUE_READ = _PROLOGUE.format(fence="", fence_out="")
# The fence is VOLATILE (a STABLE one with an unused result would be dropped by the planner) and asserts READ COMMITTED; its
# output column is also referenced by the outer SELECT, so the call stays whatever the planner does with an unused column.
PROLOGUE_WRITE = _PROLOGUE.format(fence=",\n         kernel.assert_profile_epoch($8) AS fence", fence_out=", s.fence")


def safe_request_id(rid: str | None) -> str:
    """A request id comes from a client header: anything outside ``[A-Za-z0-9._:-]{1,64}`` is replaced by a hash."""
    if rid and _REQUEST_ID.fullmatch(rid):
        return rid
    return "invalid-" + hashlib.sha256((rid or "").encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class OrgLimits:
    """Guardrails a caller can supply for a unit of work. They can lower the class defaults, never raise them."""

    statement_timeout_ms: int | None = None
    work_mem_kb: int | None = None


OrgLimitsHook = Callable[[str], Awaitable[OrgLimits | None]]


class Tx:
    """The handle a UoW body uses. Valid only inside the ``async with`` / callable that received it."""

    def __init__(self, con: asyncpg.Connection, db: KernelDB, *, klass: str, idempotent: bool, request_id: str) -> None:
        self._con, self._db, self._class, self.idempotent, self.request_id = con, db, klass, idempotent, request_id
        self.completed = 0
        self.commit_sent = False
        task = None
        self.started: object = None  # transaction_timestamp() read by the prologue
        self.discard = False  # the connection left the transaction (early COMMIT or ROLLBACK): it must not be reused
        with suppress(RuntimeError):  # no running loop (unit tests build a Tx directly)
            task = asyncio.current_task()
        self.cancelling_at_start = task.cancelling() if task else 0
        self.failed = False  # a statement failed and no savepoint rollback has undone it since
        self._open = True

    async def _run(self, op: str, sql: str, *args: object, internal: bool = False) -> Any:  # noqa: ANN401
        if not self._open:
            raise RuntimeError("transaction handle used outside its unit of work")
        if not internal:
            if _REBIND.search(_COMMENTS.sub(" ", sql)):
                raise UowUsageError("a statement may not change the unit's org, role or settings")
            if any(_TXN_CONTROL.match(" ".join(st.lower().split())) for st in _SQL_NOISE.sub(" ", sql).split(";")):
                raise UowUsageError("transaction control statements are not allowed inside a unit; use tx.savepoint()")
        catalog.record(sql, args)
        site = _site(sql)
        t0 = time.perf_counter()
        try:
            result = await getattr(self._con, op)(sql, *args)
        except BaseException as e:  # a cancelled statement also aborts the transaction
            self.failed = True
            if isinstance(e, Exception):
                raise self._db.map_error(e, self) from e
            raise
        finally:
            dt = time.perf_counter() - t0
            QUERY_SECONDS.observe(self._db.cfg.service, site, self._class, value=dt)
            if dt * 1000 >= self._db.cfg.slow_statement_ms:
                logger.warning("slow statement site=%s ms=%d request_id=%s", site, dt * 1000, self.request_id)
        self._verify_in_transaction()
        self.completed += 1
        return result

    def _verify_in_transaction(self) -> None:
        """Protocol-level check behind the text guard: the server must still report an open transaction.

        An early COMMIT/ROLLBACK that got past the text guard cannot be undone, but it is detected at once: the unit fails
        loudly and the connection is discarded.
        """
        if not self._con.is_in_transaction():
            self.failed = True
            self.discard = True
            raise UowUsageError("the statement ended the unit's transaction; the connection was discarded")

    async def fetch(self, sql: str, *args: object) -> list[asyncpg.Record]:
        return await self._run("fetch", sql, *args)

    async def fetchrow(self, sql: str, *args: object) -> asyncpg.Record | None:
        return await self._run("fetchrow", sql, *args)

    async def fetchval(self, sql: str, *args: object) -> Any:  # noqa: ANN401
        return await self._run("fetchval", sql, *args)

    async def execute(self, sql: str, *args: object) -> str:
        return await self._run("execute", sql, *args)

    async def executemany(self, sql: str, args: Sequence[Sequence[Any]]) -> None:
        await self._run("executemany", sql, args)

    async def copy_into_temp(self, table: str, columns: Mapping[str, str], records: Sequence[Sequence[Any]]) -> None:
        """Stage rows in ``CREATE TEMP TABLE ... ON COMMIT DROP`` (COPY straight into an RLS table is refused).

        Needs the TEMP privilege, which only COPY writers hold. ``columns`` maps names to types; both are validated.
        """
        if not _IDENT.fullmatch(table) or not columns:
            raise ValueError(f"invalid staging table {table!r}")
        for name, typ in columns.items():
            if not _IDENT.fullmatch(name) or not _COLTYPE.fullmatch(typ):
                raise ValueError(f"invalid staging column {name!r} {typ!r}")
        ddl = ", ".join(f"{n} {t}" for n, t in columns.items())
        await self._run("execute", f"/* pipeshub:kernel.stage */ CREATE TEMP TABLE {table} ({ddl}) ON COMMIT DROP")
        try:
            await self._con.copy_records_to_table(table, records=records, columns=list(columns))
            self._verify_in_transaction()
        except UowUsageError:
            raise
        except BaseException as e:
            self.failed = True
            if isinstance(e, Exception):
                raise self._db.map_error(e, self) from e
            raise

    @asynccontextmanager
    async def savepoint(self, name: str) -> AsyncIterator[None]:
        """Recover from a statement error inside the unit: the only way to continue after one.

        The body's exception is re-raised after ``ROLLBACK TO``; the unit stays usable and no longer counts as failed.
        Swallowing the error inside the block (instead of letting it leave) leaves the unit failed.
        """
        if not _IDENT.fullmatch(name):
            raise ValueError(f"invalid savepoint name {name!r}")
        await self._run("execute", f"/* pipeshub:kernel.savepoint */ SAVEPOINT {name}", internal=True)
        try:
            yield
        except Exception:
            try:
                await self._con.execute(f"ROLLBACK TO SAVEPOINT {name}")
                await self._con.execute(f"RELEASE SAVEPOINT {name}")
            except Exception as e:
                self.failed = True
                raise self._db.map_error(e, self) from e
            self.failed = False
            raise
        else:
            if self.failed:  # the error was swallowed inside the block: nothing can be released or recovered
                raise FailedTransaction("a statement failed inside the savepoint block and its error was swallowed")
            await self._run("execute", f"/* pipeshub:kernel.savepoint */ RELEASE SAVEPOINT {name}", internal=True)

    def _close(self) -> None:
        self._open = False


def _site(sql: str) -> str:
    m = _SITE.search(sql[:200])
    return m.group(1) if m else "untagged"


MAX_SIDE_LOOPS = 4  # event loops besides the one that started KernelDB (the indexing consumers each run one)


class KernelDB:
    """One per process, built from the profile. Opt-in: nothing constructs it unless a feature selects PostgreSQL."""

    def __init__(self, cfg: DbConfig, *, org_limits: OrgLimitsHook | None = None) -> None:
        self.cfg = cfg
        self._org_limits = org_limits
        self._pool: asyncpg.Pool | None = None
        self._jobs_pool: asyncpg.Pool | None = None
        # asyncpg pools belong to the event loop that made them; a unit of work started from another loop (the indexing worker
        # thread runs its own) gets a pool of its own on that loop, created on first use. Keyed by the loop (a strong key, the creation
        # task holds the loop anyway) and pruned explicitly: every lookup drops, and terminates the pools of, loops that are closed; at
        # most MAX_SIDE_LOOPS live ones. The dict is shared by the threads of those loops, so every access holds `_side_lock`.
        # Per process the client connections to the pooler are therefore at most (1 + MAX_SIDE_LOOPS) * (pool_max + jobs_pool_max)
        # = 5 * 13 = 65 with the defaults; the server side stays under the pooler's max_db_connections whatever the number of loops.
        self._home_loop: asyncio.AbstractEventLoop | None = None
        self._side_lock = threading.Lock()
        self._side_pools: dict[asyncio.AbstractEventLoop, dict[bool, asyncio.Future[asyncpg.Pool]]] = {}
        self.capabilities: caps_mod.Capabilities | None = None
        self.profile_epoch: int | None = None

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    async def start(self) -> None:
        cfg = self.cfg
        name = f"pipeshub-{cfg.service}/{cfg.app_version}"
        self._home_loop = asyncio.get_running_loop()
        self._pool = await create_pool(cfg.dsn, max_size=cfg.pool_max, statement_cache_size=cfg.statement_cache_size, application_name=name,
                                       idle_seconds=cfg.pool_idle_seconds, min_size=cfg.pool_min)
        try:
            if cfg.jobs_dsn:
                self._jobs_pool = await create_pool(cfg.jobs_dsn, max_size=cfg.jobs_pool_max, statement_cache_size=cfg.statement_cache_size,
                                                    application_name=name + "/jobs", idle_seconds=cfg.pool_idle_seconds, min_size=cfg.pool_min)
            table = caps_mod.load_table()
            async with self._open(self._pool, role=cfg.role, org_id="", request_id="boot", klass=cfg.timeout_class, write=False) as tx:
                sql, names = caps_mod.probe_sql(table)
                row = await tx.fetchrow(sql, names)
                self.capabilities = caps_mod.evaluate(row, table, strict=cfg.strict, want=cfg.want_extensions)
                if cfg.fence:
                    self.profile_epoch = await tx.fetchval("/* pipeshub:kernel.profile_epoch */ SELECT kernel.profile_epoch()")
                    if self.profile_epoch is None:
                        raise DbConfigError("the deployment profile has no row")
            logger.info("database ready service=%s %s epoch=%s", cfg.service, self.capabilities.summary(), self.profile_epoch)
        except BaseException:
            await self.close()
            raise

    def terminate(self) -> None:
        """Release every pool without awaiting, for a database whose event loop has been closed (nothing is left to run ``close``)."""
        pools, self._pool, self._jobs_pool = [self._pool, self._jobs_pool], None, None
        for p in pools:
            if p is not None:
                with suppress(Exception):
                    p.terminate()
        with self._side_lock:
            for loop in list(self._side_pools):
                self._drop_side(loop)

    async def close(self) -> None:
        pools, self._pool, self._jobs_pool = [self._pool, self._jobs_pool], None, None
        for p in pools:
            if p is not None:
                await p.close()
        with self._side_lock:
            for loop in list(self._side_pools):
                self._drop_side(loop)

    # ---- public API --------------------------------------------------------------------------------------------
    def uow(self, *, write: bool = False, timeout_ms: int | None = None, isolation: Isolation = "read_committed",
            lineage: bool = False, custom_plan: bool = False, idempotent: bool = False) -> AbstractAsyncContextManager[Tx]:
        """One transaction for the ambient org. A single attempt: retry needs :meth:`run`, which can re-run the body.
        ``timeout_ms`` is one budget for waiting for a pooled connection and running the statements together."""
        return self._open_org(write=write, timeout_ms=timeout_ms, isolation=isolation, lineage=lineage,
                            custom_plan=custom_plan, idempotent=idempotent, jobs=False)

    def runtime_uow(self, *, write: bool = False, timeout_ms: int | None = None) -> AbstractAsyncContextManager[Tx]:
        """One transaction for the platform's own tables (key-value entries, the work queue): no org is bound, so no org row is visible.
        The service role's grants on those internal tables are the only access."""
        return self._open_runtime(write=write, timeout_ms=timeout_ms)

    def jobs_uow(self, *, write: bool = False, timeout_ms: int | None = None, isolation: Isolation = "read_committed",
                 lineage: bool = False, custom_plan: bool = False, idempotent: bool = False) -> AbstractAsyncContextManager[Tx]:
        """Job processes only: the jobs pool and role, under a ``for_org`` or ``system`` context."""
        return self._open_org(write=write, timeout_ms=timeout_ms, isolation=isolation, lineage=lineage,
                            custom_plan=custom_plan, idempotent=idempotent, jobs=True)

    async def run(self, fn: Callable[[Tx], Awaitable[T]], *, jobs: bool = False, **opts: object) -> T:
        """Run ``fn`` in a UoW and re-run it on serialization failure, deadlock or a safe connection loss (3x, jittered backoff).

        ``fn`` must have no side effects outside the transaction.
        """
        make = self.jobs_uow if jobs else self.uow
        for attempt in range(RETRIES_MAX + 1):
            try:
                async with make(**opts) as tx:
                    return await fn(tx)
            except (RetryableConflict, ConnectionLost) as e:
                if attempt == RETRIES_MAX or (isinstance(e, ConnectionLost) and not e.retry_safe):
                    raise
                RETRIES.inc(type(e).__name__)
                await asyncio.sleep(BACKOFF_BASE_S * 2**attempt * (1 + random.random()))
        raise AssertionError("unreachable")

    # ---- internals ---------------------------------------------------------------------------------------------
    @asynccontextmanager
    async def _open_org(self, *, write: bool, timeout_ms: int | None, isolation: str, lineage: bool, custom_plan: bool,
                      idempotent: bool, jobs: bool) -> AsyncIterator[Tx]:
        try:
            if jobs:
                ctx = org_context.current_or_system()
                if ctx.kind not in ("job", "system"):
                    raise org_context.SystemContextNotAllowed(f"jobs_uow needs a job or system context, got {ctx.kind!r}")
            else:
                ctx = org_context.current()
        except (org_context.MissingOrgContext, org_context.SystemContextNotAllowed):
            ORG_MISSING.inc(self.cfg.service)
            raise
        pool = await self._pool_here(jobs=jobs)
        if pool is None:
            raise DbConfigError("no jobs DSN configured for this process" if jobs else "KernelDB is not started")
        outer = _current_tx.get()
        if outer is not None and outer._open:
            raise UowUsageError("a unit of work cannot start inside another one; pass the open handle instead")
        async with self._open(pool, role=JOBS_ROLE if jobs else self.cfg.role, org_id=ctx.org_id or "",
                              request_id=safe_request_id(ctx.request_id), klass="jobs" if jobs else self.cfg.timeout_class,
                              write=write, timeout_ms=timeout_ms, isolation=isolation, lineage=lineage,
                              custom_plan=custom_plan, idempotent=idempotent) as tx:
            token = _current_tx.set(tx)
            try:
                yield tx
            finally:
                with suppress(ValueError):  # an async generator can be finished from another context
                    _current_tx.reset(token)

    @asynccontextmanager
    async def _open_runtime(self, *, write: bool, timeout_ms: int | None) -> AsyncIterator[Tx]:
        # Not tracked as the current unit: a key-value read may happen while an org unit is open (config lookups), on its own connection.
        pool = await self._pool_here(jobs=False)
        if pool is None:
            raise DbConfigError("KernelDB is not started")
        async with self._open(pool, role=self.cfg.role, org_id="", request_id="runtime", klass=self.cfg.timeout_class,
                              write=write, timeout_ms=timeout_ms) as tx:
            yield tx

    def _drop_side(self, loop: asyncio.AbstractEventLoop) -> None:
        """Forget the pools made for ``loop`` and release them (the caller holds ``_side_lock``). A closed loop can run nothing, so
        ``terminate()`` is called here; a running loop (shutdown) is asked to do it itself, as transports are not thread-safe."""
        for fut in self._side_pools.pop(loop, {}).values():
            if not fut.done():
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(fut.cancel)
            elif not fut.cancelled() and fut.exception() is None:
                pool = fut.result()
                if loop.is_closed():
                    with suppress(Exception):  # the sockets go with the objects
                        pool.terminate()
                else:
                    with suppress(RuntimeError):
                        loop.call_soon_threadsafe(pool.terminate)

    def _side_future(self, loop: asyncio.AbstractEventLoop, *, jobs: bool) -> asyncio.Future[asyncpg.Pool]:
        with self._side_lock:
            for old in [lp for lp in self._side_pools if lp.is_closed()]:
                self._drop_side(old)
            mine = self._side_pools.get(loop)
            if mine is None:
                if len(self._side_pools) >= MAX_SIDE_LOOPS:
                    raise DbConfigError(f"more than {MAX_SIDE_LOOPS} event loops use this database runtime besides the one that started it")
                mine = self._side_pools[loop] = {}
            fut = mine.get(jobs)
            if fut is not None and fut.done() and (fut.cancelled() or fut.exception() is not None):
                fut = None            # a creation that failed or was cancelled is tried again, never kept
            if fut is None:
                cfg = self.cfg
                name = f"pipeshub-{cfg.service}/{cfg.app_version}"
                fut = mine[jobs] = org_context.spawn_detached(create_pool(
                    cfg.jobs_dsn if jobs else cfg.dsn, max_size=cfg.jobs_pool_max if jobs else cfg.pool_max,
                    statement_cache_size=cfg.statement_cache_size, application_name=name + ("/jobs" if jobs else ""),
                    idle_seconds=cfg.pool_idle_seconds, min_size=cfg.pool_min))
                fut.add_done_callback(lambda f: f.cancelled() or f.exception())    # mark a failure as seen: the callers get it
            return fut

    async def _pool_here(self, *, jobs: bool) -> asyncpg.Pool | None:
        home = self._jobs_pool if jobs else self._pool
        loop = asyncio.get_running_loop()
        if home is None or loop is self._home_loop:
            return home
        # shielded: a caller that is cancelled (a timeout around the first unit on this loop) must not cancel the creation the
        # next callers wait for
        return await asyncio.shield(self._side_future(loop, jobs=jobs))

    async def _limits(self, org_id: str) -> OrgLimits | None:
        if not org_id or self._org_limits is None:
            return None
        try:
            return await self._org_limits(org_id)
        except Exception:
            logger.exception("org limits lookup failed; class defaults apply")
            return None

    @asynccontextmanager
    async def _open(self, pool: asyncpg.Pool, *, role: str, org_id: str, request_id: str, klass: str, write: bool,
                    timeout_ms: int | None = None, isolation: str = "read_committed", lineage: bool = False,
                    custom_plan: bool = False, idempotent: bool = False) -> AsyncIterator[Tx]:
        if isolation not in _ISOLATION:
            raise ValueError(f"isolation must be one of {_ISOLATION}")
        if write and isolation != "read_committed":
            raise UnsupportedIsolation("write units of work run at read_committed only")
        tc = TIMEOUT_CLASSES[klass]
        limits = await self._limits(org_id)
        statement_ms = min(x for x in (tc.statement_ms, timeout_ms, limits and limits.statement_timeout_ms) if x)
        work_kb = min((x for x in (tc.work_mem_kb, limits and limits.work_mem_kb) if x), default=None)
        t_start = time.perf_counter()
        try:
            con = await pool.acquire(timeout=statement_ms / 1000)
        except TimeoutError as e:
            raise PoolTimeout("no pooled connection became free in time") from e
        except OSError as e:  # connection refused and similar while opening a new connection
            raise ConnectionLost(f"cannot connect: {e}", retry_safe=True) from e
        waited = time.perf_counter() - t_start
        POOL_WAIT.observe(self.cfg.service, value=waited)
        if timeout_ms and statement_ms <= timeout_ms:      # the caller's budget covers the wait for a connection and the statement together
            statement_ms = max(1, statement_ms - int(waited * 1000))
        tx = Tx(con, self, klass=klass, idempotent=idempotent, request_id=request_id)
        txn = con.transaction(isolation=isolation, readonly=not write)
        outcome = "rollback"
        try:
            try:
                await txn.start()
            except Exception as e:
                raise self.map_error(e, tx) from e
            await self._prologue(con, tx, role, org_id, request_id, statement_ms, tc.lock_ms, work_kb, write=write, custom_plan=custom_plan)
            if lineage:
                await tx._run("execute", "/* pipeshub:kernel.lineage */ SELECT set_config('app.org_lineage', "
                              "array_to_string(kernel.org_lineage(current_setting('app.org_id')), ','), true)", internal=True)
            yield tx
            await self._check_alive(con, tx, write=write)
            tx.commit_sent = True
            try:
                await txn.commit()
            except Exception as e:
                mapped = self.map_error(e, tx)
                raise (CommitOutcomeUnknown("connection lost after COMMIT was sent") if isinstance(mapped, ConnectionLost) else mapped) from e
            outcome = "commit"
        except BaseException:
            await self._abort(con, txn, tx)
            raise
        finally:
            tx._close()
            await self._release(pool, con)
            UOW_SECONDS.observe(self.cfg.service, outcome, value=time.perf_counter() - t_start)

    async def _check_alive(self, con: asyncpg.Connection, tx: Tx, *, write: bool) -> None:
        """COMMIT of an aborted transaction answers ROLLBACK without an error, so ask first. The flag is the fast path;
        for write units the server is the authority (it covers every way a statement can fail, COPY and cancellation included)."""
        task = asyncio.current_task()
        if tx.failed or (task is not None and task.cancelling() > tx.cancelling_at_start):
            raise FailedTransaction("a statement failed or the task was cancelled inside the unit; the unit was rolled back")
        if write and COMMIT_PROBE:
            try:
                # One round trip: also proves it is still the same transaction (COMMIT; BEGIN in one string would not show
                # in the connection's status, but starts a new transaction with a new timestamp).
                now = await con.fetchval("SELECT now()")
            except asyncpg.InFailedSQLTransactionError as e:
                raise FailedTransaction("the transaction is aborted (a statement error was swallowed); the unit was rolled back") from e
            except Exception as e:
                raise self.map_error(e, tx) from e
            if tx.started is not None and now != tx.started:
                tx.discard = True
                raise FailedTransaction("the unit's transaction ended and another began; the unit was rolled back")

    async def _prologue(self, con: asyncpg.Connection, tx: Tx, role: str, org_id: str, request_id: str, statement_ms: int,
                        lock_ms: int, work_kb: int | None, *, write: bool, custom_plan: bool) -> None:
        args = [role, org_id, request_id, f"{statement_ms}ms", f"{lock_ms}ms",
                "force_custom_plan" if custom_plan else "auto", f"{work_kb}kB" if work_kb else ""]
        fenced = write and self.profile_epoch is not None
        try:
            row = await con.fetchrow(PROLOGUE_WRITE if fenced else PROLOGUE_READ, *args, *([self.profile_epoch] if fenced else []))
        except Exception as e:
            raise self.map_error(e, tx, prologue=True) from e
        pre = row["r"] if row else None
        if row:
            tx.started = row["started"]
        if pre != "none":
            ROLE_LEAKS.inc(self.cfg.service)
            logger.error("session role %r found on a pooled connection; discarding it", pre)
            raise SessionRoleLeak("a session-level role was left on the pooled connection")

    async def _abort(self, con: asyncpg.Connection, txn: asyncpg.transaction.Transaction, tx: Tx) -> None:
        """Roll back and keep the connection, unless that is unsafe: then drop it.

        Dropped without a rollback: a leaked session role (the pooler closes its server connection when the client vanishes
        inside a transaction) and a lost connection. After a cancellation asyncpg has already asked the server to cancel
        the running statement, and the rollback below waits for that.
        """
        exc = _current_exception()
        if not tx.discard and not connection_closed(con) and not isinstance(exc, (SessionRoleLeak, ConnectionLost, CommitOutcomeUnknown)):
            try:
                await asyncio.wait_for(txn.rollback(), ABORT_TIMEOUT_S)
                return
            except BaseException:
                pass
        _terminate(con)

    async def _release(self, pool: asyncpg.Pool, con: asyncpg.Connection) -> None:
        try:
            await pool.release(con)
        except asyncpg.InterfaceError:
            pass  # asyncpg already freed the holder: the connection died under us
        except Exception:
            logger.exception("releasing a pooled connection failed")
            _terminate(con)

    def map_error(self, e: Exception, tx: Tx, *, prologue: bool = False) -> Exception:
        """Translate a driver error into a typed one; unclassified errors pass through unchanged."""
        if isinstance(e, DbError):
            return e
        state = getattr(e, "sqlstate", None)
        if state == "40001":
            return SerializationConflict(str(e))
        if state == "40P01":
            return DeadlockDetected(str(e))
        if state == "57014":
            return DbTimeout(str(e))
        if state == "55P03":
            return LockTimeout(str(e))
        if prologue and state == "55000":
            return ProfileEpochMismatch(str(e))
        lost = isinstance(e, asyncpg.InterfaceError) or (
            not isinstance(e, TimeoutError) and isinstance(e, (OSError, asyncio.IncompleteReadError))
        ) or (state is not None and (state in _LOST_STATES or state.startswith("08")))
        if lost:
            safe = not tx.commit_sent and (tx.idempotent or tx.completed == 0)
            return ConnectionLost(str(e) or type(e).__name__, retry_safe=safe)
        if prologue:
            return PrologueFailed(f"{type(e).__name__}: {e}")
        return e


def connection_closed(con: asyncpg.Connection) -> bool:
    """True when the connection is closed, or asyncpg already took it back into the pool because it died."""
    try:
        return con.is_closed()
    except asyncpg.InterfaceError:
        return True


def _terminate(con: asyncpg.Connection) -> None:
    with suppress(asyncpg.InterfaceError):
        con.terminate()


def _current_exception() -> BaseException | None:
    import sys

    return sys.exc_info()[1]
