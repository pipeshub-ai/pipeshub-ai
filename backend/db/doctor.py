"""Read-only health check of a migrated database: ``python -m db.doctor`` (from ``backend/``, ``PG_MIGRATE_URL``).

Runs what the migrate job checks after an upgrade, without changing anything and without the migrate lock: every branch's
epoch against this build's heads, ``kernel.rls_violations``, the vetted function manifests and the security surface snapshot.

Exit codes: 0 healthy | 1 the check itself failed | 2 bad configuration | 4 a problem was found.
"""
import os
import sys

import psycopg
from sqlalchemy.engine import make_url

from db import boot_assert, migrate
from db.migrate import EXIT_CHECK, EXIT_CONFIG, EXIT_FAILED, RunnerError
from db.schema_requirements import heads


def log(msg: str) -> None:
    print(f"doctor: {msg}", flush=True)


def check(conn: psycopg.Connection) -> None:
    rows: dict[str, tuple[int, int]] = {}
    if conn.execute("SELECT to_regclass('kernel.schema_epoch') IS NOT NULL").fetchone()[0]:
        rows = {b: (e, m) for b, e, m in conn.execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch")}
    # a branch the database has and this tree does not (an extra location, a newer build) is ignored, as at boot
    problems = boot_assert.schema_problems(rows, {b: {"requires": n, "code": n} for b, n in heads().items()})
    for p in problems:
        log(f"PROBLEM {p}: run python -m db.migrate with this build" if "<" in p else f"PROBLEM {p}: this build is older than the schema")
    if rows:                                                  # a database never migrated has no catalog checks to run yet
        try:
            migrate._report(conn)
        except RunnerError as exc:
            problems.append(str(exc))
    if problems:
        raise RunnerError(EXIT_CHECK, f"{len(problems)} problem(s) found")


def run() -> None:
    if os.environ.get("PG_MIGRATE_SURFACE_CHECK") == "skip":
        # the skip switch is a migrate-time escape hatch; a health check that skipped the surface would report a false all-clear
        log("PG_MIGRATE_SURFACE_CHECK=skip is ignored: the doctor always compares the security surface")
        del os.environ["PG_MIGRATE_SURFACE_CHECK"]
    dsn = make_url(migrate.migrate_url()).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        conn = psycopg.connect(dsn, connect_timeout=10, **migrate.KEEPALIVES)
    except psycopg.Error as exc:
        raise RunnerError(EXIT_CONFIG, f"cannot connect: {str(exc).strip().splitlines()[0]}") from exc
    try:
        # one read-only snapshot: every statement sees the same catalog, and nothing can be written (also through a pooler)
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        check(conn)
    finally:
        conn.rollback()
        conn.close()


def main() -> int:
    try:
        run()
    except RunnerError as exc:
        log(f"error: {exc}")
        return exc.code
    except Exception as exc:
        log(f"FAILED: {type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}")
        return EXIT_FAILED
    log("healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
