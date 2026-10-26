"""Migration runner: ``python -m db.migrate [upgrade|current|downgrade <rev>]`` (default: upgrade).

Connects with ``PG_MIGRATE_URL`` straight to PostgreSQL (a session advisory lock and CREATE INDEX
CONCURRENTLY need one session; never through a pooler). Concurrent runners serialise on the lock.

Exit codes: 0 done | 1 a migration failed | 2 bad configuration | 3 lock wait timed out |
4 post-run catalog check found violations.
"""
import contextlib
import os
import sys
import time
from collections.abc import Iterator

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from db import surface
from db.lib import vetted
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
        conn.execute(f"SET lock_timeout = '{int(wait_s * 1000)}ms'")
        try:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
        except psycopg.errors.LockNotAvailable as exc:
            raise RunnerError(EXIT_LOCK, f"another migration run held the lock for {wait_s:g}s") from exc
        conn.execute("RESET lock_timeout")
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


def _has_revisions(conn: psycopg.Connection) -> bool:
    if not conn.execute("SELECT to_regclass('kernel.alembic_version') IS NOT NULL").fetchone()[0]:
        return False
    return conn.execute("SELECT EXISTS (SELECT 1 FROM kernel.alembic_version)").fetchone()[0]


def _report(conn: psycopg.Connection) -> None:
    """The epochs and the catalog and surface checks. On an installed database they run inside the upgrade's transaction (see
    env.py): a violation raises before COMMIT, so nothing the run applied stays. On an empty one they run after the revisions."""
    for branch, epoch, min_code in conn.execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch ORDER BY 1"):
        log(f"epoch {branch}={epoch} min_code={min_code}")
    rows: list[tuple[str, str]] = []
    if conn.execute("SELECT to_regclass('kernel.rls_violations') IS NOT NULL").fetchone()[0]:
        conn.execute("SET search_path = pg_catalog")      # policy texts are deparsed with the caller's search_path
        rows = [tuple(r) for r in conn.execute("SELECT * FROM kernel.rls_violations")]
        for rule, obj in conn.execute("SELECT * FROM kernel.rls_warnings"):
            log(f"WARNING {rule}: {obj}")
        conn.execute("RESET search_path")
        rows += vetted.problems(conn)                                   # the vetted function manifests agree with the registry
        extra = os.environ.get("PG_MIGRATE_SURFACE_FILES", "").split(os.pathsep)
        if os.environ.get("PG_MIGRATE_SURFACE_CHECK") == "skip":
            log("WARNING *** the security surface check was SKIPPED (PG_MIGRATE_SURFACE_CHECK=skip): development only ***")
            if conn.execute("SELECT to_regclass('kernel.surface_skips') IS NOT NULL").fetchone()[0]:
                conn.execute("INSERT INTO kernel.surface_skips (files) VALUES (%s)", (os.pathsep.join(extra),))
        else:                                                           # deny by default: the whole security surface is compared
            rows += surface.violations(conn, extra)
        if conn.execute("SELECT to_regclass('kernel.surface_skips') IS NOT NULL").fetchone()[0]:
            n, last = conn.execute("SELECT count(*), max(skipped_at) FROM kernel.surface_skips").fetchone()
            if n:
                log(f"WARNING the surface check was skipped {n} time(s) on this database, last at {last}; "
                    "run db.migrate without the skip switch and review")
    if rows:
        for row in rows:
            log(f"VIOLATION {row}")
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
    with locked(url) as conn:
        _ensure_kernel_schema(conn)
        cfg = alembic_config(url)
        if cmd == "upgrade":
            _path["existing_install"] = _has_revisions(conn)
            if _path["existing_install"] and (ahead := _ahead_of_this_build(conn, cfg)):
                log(ahead)
                return
            if _path["existing_install"]:
                cfg.attributes["verify"] = _report
                _upgrade_with_retries(cfg, "heads")
            else:               # an empty database has nothing to protect, and all revisions in one transaction hold ~6,600 locks
                _upgrade_with_retries(cfg, "heads")
                _report(conn)
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
