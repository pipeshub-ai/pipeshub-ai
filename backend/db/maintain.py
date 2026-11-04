"""Maintenance with the migrate credential and the same lock as the migrate job:

    python -m db.maintain placement [--watch [SECONDS]]   create the LIST leaf of every org that asked for one (retrieval.placement_requests);
                                                          needs PG_JOBS_URL. --watch keeps running (the placement worker: a writing service
                                                          waits for it before an org's first vector write)

Exit codes of a one-shot run: 0 every request placed (or none pending), 1 a request failed (it is retried by the next run), 2 configuration,
3 lock wait timed out, 5 a request was refused by the placement rules.
"""
import os
import sys
import time

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.exc import InterfaceError, OperationalError

from db.lib import placement
from db.migrate import (
    EXIT_CONFIG,
    EXIT_FAILED,
    OWNER_ROLE,
    RunnerError,
    locked,
    log,
    migrate_url,
)

EXIT_REFUSED = 5
USAGE = ("usage: python -m db.maintain placement [--watch [SECONDS]]\n"
         "placement needs PG_JOBS_URL (the jobs role, direct address) and PG_MIGRATE_URL")


def _jobs_url() -> str:
    raw = os.environ.get("PG_JOBS_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not raw:
        raise RunnerError(EXIT_CONFIG, "PG_JOBS_URL is not set (the placement run reads and closes the requests as the jobs role)")
    return raw


def _placement_pass(url: str) -> tuple[list[str], list[str], list[str]]:
    """One pass over the pending requests. The migrate lock is taken only when there is something to do, so an idle worker never
    holds up a deployment. Returns (done, failed, refused)."""
    with psycopg.connect(_jobs_url(), autocommit=True) as jobs:
        if not placement.has_pending(jobs):
            return [], [], []
        with locked(url):
            engine = create_engine(url, isolation_level="AUTOCOMMIT")
            try:
                with engine.connect() as conn:
                    conn.exec_driver_sql(f"SET ROLE {OWNER_ROLE}")
                    return placement.run_requests(conn, jobs)
            finally:
                engine.dispose()


def _summary(done: list[str], failed: list[str], refused: list[str]) -> str:
    return (f"leaves created: {', '.join(done) or 'none'}" + (f"; failed (retried later): {', '.join(failed)}" if failed else "")
            + (f"; refused: {', '.join(refused)}" if refused else ""))


def _watch(url: str, interval: float) -> None:
    log(f"placement worker running (every {interval:g}s)")
    while True:
        try:
            done, failed, refused = _placement_pass(url)
            if done or failed or refused:
                log(_summary(done, failed, refused))
            if len(done) + len(failed) + len(refused) >= placement.MAX_PER_PASS:
                continue                                            # a full pass: more wait behind the cap, and a new org's writer waits too
        except (RunnerError, psycopg.Error, OperationalError, InterfaceError, OSError) as exc:   # the database is away or the lock is busy: try again
            log(f"placement pass failed: {str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__}")
        time.sleep(interval)


def main(argv: list[str]) -> int:
    try:
        if not argv or argv[0] != "placement" or len(argv) > 3 or (len(argv) > 1 and argv[1] != "--watch"):
            raise RunnerError(EXIT_CONFIG, USAGE)
        url = migrate_url()
        if len(argv) > 1:
            try:
                interval = float(argv[2]) if len(argv) == 3 else 1.0
            except ValueError:
                raise RunnerError(EXIT_CONFIG, USAGE) from None
            if not 0 < interval < float("inf"):
                raise RunnerError(EXIT_CONFIG, USAGE)
            _jobs_url()                       # a missing PG_JOBS_URL never fixes itself: exit 2 here, not an endless retry
            _watch(url, interval)
            return 0
        done, failed, refused = _placement_pass(url)
        log(_summary(done, failed, refused))
        return EXIT_REFUSED if refused else EXIT_FAILED if failed else 0
    except RunnerError as exc:
        log(str(exc))
        return exc.code
    except Exception as exc:
        log(f"failed: {str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
