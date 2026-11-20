"""Migration runner: ``python -m db.migrate [upgrade|current|downgrade <rev>]`` (default: upgrade).

Connects with ``PG_MIGRATE_URL`` straight to PostgreSQL (a session advisory lock and CREATE INDEX
CONCURRENTLY need one session; never through a pooler). Concurrent runners serialise on the lock.

Exit codes: 0 done | 1 a migration failed | 2 bad configuration | 3 lock wait timed out |
4 post-run catalog check found violations.
"""
import contextlib
import functools
import os
import sys
import time
from collections.abc import Iterator

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool

from db import surface
from db.lib import ddl, vetted
from db.locations import DB_DIR, version_locations
from db.schema_requirements import heads

EXIT_FAILED, EXIT_CONFIG, EXIT_LOCK, EXIT_CHECK = 1, 2, 3, 4
LOCK_KEY = "pipeshub:migrate"
# A dead peer is noticed within about a minute, so a killed runner does not keep its lock session.
KEEPALIVES = {"keepalives": 1, "keepalives_idle": 20, "keepalives_interval": 10, "keepalives_count": 3}
OWNER_ROLE = "pipeshub_owner"


class RunnerError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    print(f"migrate: {msg}", flush=True)


def migrate_url() -> str:
    raw = os.environ.get("PG_MIGRATE_URL", "").strip()
    if not raw:
        raise RunnerError(EXIT_CONFIG, "PG_MIGRATE_URL is not set")
    url = make_url(raw)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


@contextlib.contextmanager
def locked(url: str, wait_s: float | None = None) -> Iterator[psycopg.Connection]:
    """Hold the migrate advisory lock on a dedicated session; yield that (autocommit) connection."""
    if wait_s is None:
        raw = os.environ.get("PG_MIGRATE_LOCK_TIMEOUT_S", "600")
        try:
            wait_s = float(raw)
        except ValueError:
            raise RunnerError(EXIT_CONFIG, f"PG_MIGRATE_LOCK_TIMEOUT_S must be a number of seconds, got {raw!r}") from None
    # lock_timeout = 0 means wait forever, and a negative value is a server error: refuse both before connecting.
    if not wait_s >= 0.001:
        raise RunnerError(EXIT_CONFIG, f"PG_MIGRATE_LOCK_TIMEOUT_S must be at least 0.001 seconds, got {wait_s:g}")
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10, **KEEPALIVES)
    except psycopg.Error as exc:
        raise RunnerError(EXIT_CONFIG, f"cannot connect: {str(exc).strip().splitlines()[0]}") from exc
    try:
        pids = {conn.execute("SELECT pg_backend_pid()").fetchone()[0] for _ in range(3)}
        if len(pids) > 1:
            raise RunnerError(EXIT_CONFIG, "PG_MIGRATE_URL points at a connection pooler; use the direct address")
        # Polled, not waited for in pg_advisory_lock: a waiting statement holds a snapshot, and the holder's CREATE INDEX CONCURRENTLY waits for
        # every older snapshot, so the two runs would stall each other until this one gave up.
        deadline = time.monotonic() + wait_s
        while not conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,)).fetchone()[0]:
            if time.monotonic() >= deadline:
                raise RunnerError(EXIT_LOCK, f"another migration run held the lock for {wait_s:g}s")
            time.sleep(min(1.0, wait_s / 10))
        # A pooler can hand back the same backend and still drop a session lock at transaction end, so
        # prove the lock is held by looking at it from a separate statement. Fail closed.
        held = conn.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                            "AND pid = pg_backend_pid()").fetchone()[0]
        if not held:
            raise RunnerError(EXIT_CONFIG, "the session advisory lock is not held after it was taken: "
                              "PG_MIGRATE_URL points at a connection pooler; use the direct address")
        yield conn
    finally:
        conn.close()


def _ensure_kernel_schema(conn: psycopg.Connection) -> None:
    """The version table lives in schema kernel, so the schema must exist before the first revision."""
    has_owner = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (OWNER_ROLE,)).fetchone()[0]
    try:
        if has_owner:
            conn.execute(f"SET ROLE {OWNER_ROLE}")
        conn.execute("CREATE SCHEMA IF NOT EXISTS kernel")
    except psycopg.Error as exc:
        raise RunnerError(EXIT_CONFIG, f"cannot prepare schema kernel: {str(exc).strip().splitlines()[0]}") from exc
    finally:
        conn.execute("RESET ROLE")


def kv_access_mode() -> str | None:
    """``PG_KV_ACCESS``: ``shadow`` logs key-value access outside a service role's prefixes, ``enforce`` refuses it; unset keeps the stored mode."""
    raw = os.environ.get("PG_KV_ACCESS", "").strip()
    if raw not in ("", "shadow", "enforce"):
        raise RunnerError(EXIT_CONFIG, f"PG_KV_ACCESS must be shadow or enforce (or unset to keep the stored mode), got {raw!r}")
    return raw or None


def _set_kv_access(conn: psycopg.Connection, mode: str) -> None:
    conn.execute(f"SET ROLE {OWNER_ROLE}")
    try:
        conn.execute("UPDATE platform.kv_access SET enforce = %s, updated_at = now() WHERE enforce IS DISTINCT FROM %s",
                     (mode == "enforce", mode == "enforce"))
    finally:
        conn.execute("RESET ROLE")
    log(f"key-value access by prefix: {mode}")


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(DB_DIR))
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("version_locations", os.pathsep.join(version_locations()))
    cfg.attributes["url"] = url
    return cfg


def _upgrade_with_retries(cfg: Config, target: str) -> None:
    """A revision that times out waiting for a lock rolled back its own transaction: re-run it."""
    base = float(os.environ.get("PG_MIGRATE_RETRY_BASE_S", "1"))
    for attempt in range(5):
        try:
            return command.upgrade(cfg, target)
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) != "55P03" or attempt == 4:
                raise
            log(f"lock timeout, retrying ({attempt + 1}/4)")
            time.sleep(base * 2**attempt)
    raise AssertionError("unreachable")


def build_memory() -> str | None:
    """``PG_INDEX_BUILD_MEMORY_MB``: ``maintenance_work_mem`` of the session that builds indexes online (the whole build's budget, workers
    included); unset keeps the server's."""
    raw = os.environ.get("PG_INDEX_BUILD_MEMORY_MB", "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 2_000_000:
        raise RunnerError(EXIT_CONFIG, f"PG_INDEX_BUILD_MEMORY_MB must be a whole number of megabytes from 1, got {raw!r}")
    return f"{int(raw)}MB"


def _online(url: str, memory: str | None) -> Engine:
    """Sessions outside the upgrade's transaction, as the owner role, for index builds and drops. No statement_timeout (a build reads a whole
    leaf) and no lock_timeout for CREATE INDEX CONCURRENTLY (its lock lets readers and writers through; it waits for older transactions, which a
    timeout would turn into an invalid index); every statement that takes a lock writers wait for runs under ddl._retry's 2 s."""
    opts = (f"-c role={OWNER_ROLE} -c statement_timeout=0 -c lock_timeout=0 -c client_connection_check_interval=5s"
            + (f" -c maintenance_work_mem={memory}" if memory else ""))
    return create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT", connect_args={"options": opts, **KEEPALIVES})


def _modules(cfg: Config, conn: psycopg.Connection, *, pending: bool) -> list:
    """The revision modules of this tree, oldest first: those the database has not applied (``pending``) or all of them. A revision
    ``<branch>_<NNNN>`` is applied when the branch's epoch is at least NNNN (env.py writes the epoch with each revision)."""
    epochs = dict(conn.execute("SELECT branch, epoch FROM kernel.schema_epoch").fetchall()) if pending else {}
    revs = reversed(list(ScriptDirectory.from_config(cfg).walk_revisions()))
    return [r.module for r in revs if int(r.revision.rsplit("_", 1)[1]) > epochs.get(r.revision.rsplit("_", 1)[0], 0)]


def _build_online(c: Connection, modules: list, started: list[str]) -> None:
    """Build the ``ONLINE_INDEXES`` of the pending revisions before their transaction, without blocking writers (``ensure_partitioned_index``:
    ``ON ONLY`` the parent, CREATE INDEX CONCURRENTLY per leaf, ATTACH). The revision's own call then finds them valid and does nothing. An
    index a stopped run left invalid is rebuilt. A table a pending revision creates is skipped: that revision builds on it, empty."""
    for ix in (ix for m in modules for ix in getattr(m, "ONLINE_INDEXES", ())):
        schema = ix["table"].split(".")[0]
        qname = f"{schema}.{ix['name']}"
        if c.exec_driver_sql("SELECT to_regclass(%s) IS NULL", (ix["table"],)).scalar() or ddl._index_valid(c, qname):
            continue
        started.append(qname)
        t0 = time.monotonic()
        log(f"index {qname}: building online")
        try:
            ddl.ensure_partitioned_index(c, **ix, blocking=False)
        except DBAPIError as exc:
            # A column, function or operator class the index names comes from an earlier pending revision (a database several releases behind).
            # The first statement failed, so nothing was built: the revision builds it in its transaction, as before this step existed.
            if getattr(exc.orig, "sqlstate", None) not in ("42703", "42883", "42704"):
                raise
            log(f"index {qname}: built in the upgrade's transaction instead (an earlier pending revision changes {ix['table']}: "
                f"{str(exc.orig).strip().splitlines()[0]})")
            continue
        log(f"index {qname}: built in {time.monotonic() - t0:.1f}s")


def drop_online(c: Connection, qname: str) -> None:
    """Drop an index and every leaf index a stopped build of it left unattached. A partitioned index cannot be dropped CONCURRENTLY, nor can a
    leaf index while it is attached: its drop is a catalog change of a moment, under ddl._retry's 2 s lock_timeout."""
    schema, name = ddl._ident(qname).split(".")
    stray = c.exec_driver_sql(
        "SELECT c.oid::regclass::text FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE NOT i.indisvalid AND c.relnamespace = %s::regnamespace "
        "AND c.relname LIKE %s AND NOT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = c.oid)", (schema, "%\\_" + name.replace("_", "\\_"))).scalars().all()
    for leaf in stray:
        c.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {leaf}")
    if c.exec_driver_sql("SELECT relkind = 'I' FROM pg_class WHERE oid = to_regclass(%s)", (qname,)).scalar():
        ddl._retry(c, lambda: c.exec_driver_sql(f"DROP INDEX IF EXISTS {qname}"), attempts=5)
    else:
        c.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {qname}")


def _retiring(cfg: Config, conn: psycopg.Connection) -> frozenset[str]:
    """``ONLINE_DROPS`` of every revision of this tree: indexes a revision retires. They are dropped after the upgrade commits, never
    inside it, and a run that could not drop one drops it the next time."""
    return frozenset(q for m in _modules(cfg, conn, pending=False) for q in getattr(m, "ONLINE_DROPS", ()))


def _drop_all(engine: Engine, names: list[str] | frozenset[str], why: str, hint: str) -> None:
    """Drop each of these indexes that exists. A failure is logged with ``hint``: it never fails a run that committed, nor hides the error
    of one that did not."""
    for q in sorted(set(names)):
        try:
            with engine.connect() as c:
                if c.exec_driver_sql("SELECT to_regclass(%s) IS NULL", (q,)).scalar():
                    continue
                drop_online(c, q)
            log(f"index {q}: dropped ({why})")
        except Exception as exc:
            log(f"WARNING index {q} was not dropped ({why}): {str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__}; {hint}")


def _has_revisions(conn: psycopg.Connection) -> bool:
    if not conn.execute("SELECT to_regclass('kernel.alembic_version') IS NOT NULL").fetchone()[0]:
        return False
    return conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.alembic_version)").fetchone()[0]


def _report(conn: psycopg.Connection, retiring: frozenset[str] = frozenset()) -> None:
    """The epochs and the catalog and surface checks. On an installed database they run inside the upgrade's transaction (see
    env.py): a violation raises before COMMIT, so nothing the run applied stays. On an empty one they run after the revisions.
    ``retiring``: indexes a revision retires; they are dropped after the commit, so until then their lines are not unexpected."""
    for branch, epoch, min_code in conn.execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch ORDER BY 1"):
        log(f"epoch {branch}={epoch} min_code={min_code}")
    rows: list[tuple[str, str]] = []
    if conn.execute("SELECT to_regclass('kernel.rls_violations') IS NOT NULL").fetchone()[0]:
        conn.execute("SET search_path = pg_catalog")      # policy texts are deparsed with the caller's search_path
        # the catalog views are big expressions run once: compiling them (JIT, on by default from PostgreSQL 12 where it is built in) costs
        # seconds to tens of seconds and saves nothing
        conn.execute("SET jit = off")
        rows = [tuple(r) for r in conn.execute("SELECT * FROM kernel.rls_violations")]
        for rule, obj in conn.execute("SELECT * FROM kernel.rls_warnings"):
            log(f"WARNING {rule}: {obj}")
        conn.execute("RESET search_path")
        rows += vetted.problems(conn)                                   # the vetted function manifests agree with the registry
        conn.execute("RESET jit")
        extra = os.environ.get("PG_MIGRATE_SURFACE_FILES", "").split(os.pathsep)
        if os.environ.get("PG_MIGRATE_SURFACE_CHECK") == "skip":
            log("WARNING *** the security surface check was SKIPPED (PG_MIGRATE_SURFACE_CHECK=skip): development only ***")
            if conn.execute("SELECT to_regclass('kernel.surface_skips') IS NOT NULL").fetchone()[0]:
                conn.execute("INSERT INTO kernel.surface_skips (files) VALUES (%s)", (os.pathsep.join(extra),))
        else:                                                           # deny by default: the whole security surface is compared
            rows += [r for r in surface.violations(conn, extra)
                     if not (r[0] == "50 surface: unexpected" and r[1].startswith("index ") and r[1].split(" ")[1] in retiring)]
        if conn.execute("SELECT to_regclass('kernel.surface_skips') IS NOT NULL").fetchone()[0]:
            n, last = conn.execute("SELECT count(*), max(skipped_at) FROM kernel.surface_skips").fetchone()
            if n:
                log(f"WARNING the surface check was skipped {n} time(s) on this database, last at {last}; "
                    "run db.migrate without the skip switch and review")
    if rows:
        for row in rows:
            log(f"VIOLATION {row}")
        if any("sysfuncs@" in str(r) for r in rows):
            log("HINT the built-in functions match no reviewed sysfuncs line for this PostgreSQL major; a new minor release can add one. "
                "Start a stock server of a reviewed minor, run  PG_ADMIN_URL=<this server> python -m db.surface --sysfuncs <stock server URL>  "
                "to print the built-ins added (+) and removed (-), and if none is SECURITY DEFINER, LEAKPROOF or has settings, add the live "
                "sysfuncs line to backend/db/surface.snapshot.txt next to the existing ones (see backend/db/README.md)")
        if any("role pipeshub_import " in str(r) for r in rows):
            log("HINT the import login (pipeshub_import) can log in and no document-store move is running (a rollback leaves it open): "
                "shut it with  ALTER ROLE pipeshub_import NOLOGIN  as the administrative login, then run the migration again")
        raise RunnerError(EXIT_CHECK, f"{len(rows)} catalog violation(s) in kernel.rls_violations and the security surface")


_path: dict[str, bool | None] = {'existing_install': None}      # set by run(): which path an upgrade took (the lock-table hint differs)


def _ahead_of_this_build(conn: psycopg.Connection, cfg: Config) -> str | None:
    """A database migrated by a newer build (after a rollback of the deployment, say) names revisions this tree does not have.

    Returns the message to print when this build can run against it unchanged, ``None`` when the database holds no unknown
    revision. Raises when it cannot: the newer schema's ``min_code_epoch`` is above this build's code epoch (a contract
    revision removed something this build needs), or this build has revisions the database lacks as well (diverged)."""
    known = {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}
    stored = {r[0] for r in conn.execute("SELECT version_num FROM kernel.alembic_version")}
    unknown = sorted(stored - known)
    if not unknown:
        return None
    rows = {b: (e, m) for b, e, m in conn.execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch")}
    wanted = heads()
    if too_new := sorted(b for b, (_, m) in rows.items() if m > wanted.get(b, 0)):
        raise RunnerError(EXIT_CONFIG, f"the database was migrated by a newer build in a way this build cannot run against "
                          f"(min_code_epoch above this build's code epoch for {', '.join(too_new)}): roll forward to the newer release "
                          "instead of back, or restore a backup taken before the upgrade")
    if behind := sorted(b for b, n in wanted.items() if rows.get(b, (0, 0))[0] < n):
        raise RunnerError(EXIT_CONFIG, f"the database holds revisions this build does not know ({', '.join(unknown)}) and lacks some it has "
                          f"({', '.join(behind)}): it was not migrated by a build that includes this one; roll forward to the newer release")
    return f"database is ahead of this build (unknown revisions: {', '.join(unknown)}), nothing to do"


def run(argv: list[str]) -> None:
    cmd = argv[0] if argv else "upgrade"
    url = migrate_url()
    kv_mode = kv_access_mode()
    memory = build_memory()
    with locked(url) as conn:
        _ensure_kernel_schema(conn)
        cfg = alembic_config(url)
        if cmd == "upgrade":
            _path["existing_install"] = _has_revisions(conn)
            if _path["existing_install"] and (ahead := _ahead_of_this_build(conn, cfg)):
                log(ahead)
                if kv_mode:            # the newer schema is compatible (checked above), and it holds the mode table this build knows
                    _set_kv_access(conn, kv_mode)
                return
            engine = _online(url, memory)
            try:
                if _path["existing_install"]:
                    retiring = _retiring(cfg, conn)
                    cfg.attributes["verify"] = functools.partial(_report, retiring=retiring)
                    started: list[str] = []
                    try:
                        with engine.connect() as c:
                            _build_online(c, _modules(cfg, conn, pending=True), started)
                        _upgrade_with_retries(cfg, "heads")
                    except Exception:
                        # Nothing of the upgrade stays, so neither do the indexes built for it: the build this one replaces refuses them
                        # (unexpected surface lines) and would not start again.
                        _drop_all(engine, started, "the upgrade did not commit",
                                  "an earlier build refuses it as unexpected: run this build again, or drop it before going back")
                        raise
                else:           # an empty database has nothing to protect, and all revisions in one transaction hold ~6,600 locks
                    _upgrade_with_retries(cfg, "heads")
                    retiring = _retiring(cfg, conn)
                    _report(conn, retiring)
                _drop_all(engine, retiring, "retired by a revision", "the next run drops it")
            finally:
                engine.dispose()
            if kv_mode:
                _set_kv_access(conn, kv_mode)
        elif cmd == "current":
            command.current(cfg, verbose=True)
        elif cmd == "downgrade" and len(argv) == 2:
            if os.environ.get("PG_MIGRATE_ALLOW_DOWNGRADE") != "1":
                raise RunnerError(EXIT_CONFIG, "downgrade is for development only: set PG_MIGRATE_ALLOW_DOWNGRADE=1")
            command.downgrade(cfg, argv[1])
        else:
            raise RunnerError(EXIT_CONFIG, "usage: python -m db.migrate [upgrade|current|downgrade <revision>]")


def main(argv: list[str] | None = None) -> int:
    try:
        run(sys.argv[1:] if argv is None else argv)
    except RunnerError as exc:
        log(f"error: {exc}")
        return exc.code
    except Exception as exc:
        log(f"FAILED: {type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}")
        if getattr(getattr(exc, "orig", exc), "sqlstate", None) == "53200":       # out_of_memory: the shared lock table
            applied = ("Nothing was applied." if _path["existing_install"] else
                       "On an empty database revisions commit one by one, so some may be applied; the next run continues after them.")
            log(f"HINT: the upgrade needs more locks than the server's lock table holds. {applied} "
                "Raise max_locks_per_transaction (the table holds that many times max_connections plus max_prepared_transactions) "
                "and run db.migrate again.")
        return EXIT_FAILED
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
