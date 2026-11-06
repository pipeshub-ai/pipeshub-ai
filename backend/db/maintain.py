"""Maintenance with the migrate credential and the same lock as the migrate job:

    python -m db.maintain placement [--watch [SECONDS]]   create the LIST leaf of every org that asked for one (retrieval.placement_requests);
                                                          needs PG_JOBS_URL. --watch keeps running (the placement worker: a writing service
                                                          waits for it before an org's first vector write)

    python -m db.maintain fold [--watch [SECONDS]]    fold the point-count deltas of every org into retrieval.collection_stats, one transaction
                                                      per org; needs PG_JOBS_URL. --watch repeats (default 30 s)
    python -m db.maintain recount [<org>]             recompute the point counts from the live points (every org, or one); needs PG_JOBS_URL
    python -m db.maintain group-gc                    delete the membership of groups that have no point (every org); needs PG_JOBS_URL
    python -m db.maintain build-indexes               the vector indexes of every points leaf for every embedding space, sized to the leaf
    python -m db.maintain nightly [--all]             HNSW safe mode (pgvector before 0.8.4): delete the points of tombstoned rows, rebuild the vector
                                                      index of each leaf that had tombstones (--all: of every leaf) and vacuum it, drop the
                                                      tombstones; also indexes the leaves that lack an index. With the mode off only the delete and the drop run. One
                                                      nightly at a time (a second one exits 6, busy). Needs PG_JOBS_URL

Every job is per organisation, idempotent and resumable; none runs unless it is invoked. A pass is bounded (batches per org), so rerun a job that reports more to do. Exit codes of a one-shot placement run: 0 every request placed (or none pending), 1 a request failed (it is retried by the next run), 2
configuration, 3 lock wait timed out, 5 a request was refused by the placement rules. The other jobs: 0 done, 1 failed, 2 configuration, 3
lock wait timed out, 6 done but some org or leaf failed or was busy (the failure is on the log; the next run resumes).
"""
import os
import sys
import time
from collections.abc import Callable
from typing import TypeVar

import psycopg
from sqlalchemy import Connection, create_engine
from sqlalchemy.exc import InterfaceError, OperationalError

from db.lib import fold, placement, upkeep
from db.migrate import (
    EXIT_CONFIG,
    EXIT_FAILED,
    KEEPALIVES,
    OWNER_ROLE,
    RunnerError,
    locked,
    log,
    migrate_url,
)

T = TypeVar("T")
EXIT_REFUSED = 5
EXIT_PARTIAL = 6                # a job finished but some orgs or leaves failed; the next run resumes
USAGE = ("usage: python -m db.maintain placement [--watch [SECONDS]] | fold [--watch [SECONDS]] | recount [<org>] | build-indexes | group-gc | nightly [--all]\n"
         "placement, fold, recount, group-gc and nightly need PG_JOBS_URL (the jobs role, direct address); placement, build-indexes and nightly also PG_MIGRATE_URL")


def _interval(args: list[str], default: float) -> float:
    try:
        value = float(args[0]) if args else default
    except ValueError:
        raise RunnerError(EXIT_CONFIG, USAGE) from None
    if not 0 < value < float("inf"):
        raise RunnerError(EXIT_CONFIG, USAGE)
    return value


def _first_line(exc: BaseException) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def _jobs_url() -> str:
    raw = os.environ.get("PG_JOBS_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not raw:
        raise RunnerError(EXIT_CONFIG, "PG_JOBS_URL is not set (the jobs role, direct address)")
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


def _owner_pass(url: str, work: Callable[[Connection], T]) -> T:
    """Run ``work(owner)`` as the owner role under the migrate lock."""
    with locked(url):
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql(f"SET ROLE {OWNER_ROLE}")
                return work(conn)
        finally:
            engine.dispose()


def _build_indexes(url: str) -> list[str]:
    """Index every points leaf that lacks an index of a registered embedding space (concurrently, so writers are not blocked), one
    leaf at a time under the migrate lock, so a deployment or a placement never waits for the whole run."""
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            todo = placement.leaves_missing_indexes(conn)
    finally:
        engine.dispose()
    built = []
    for leaf in todo:
        built += _owner_pass(url, lambda c, leaf=leaf: (placement.set_cleanup_option(c, leaf), placement.build_leaf_indexes(c, leaf, report=log))[1])
    return built


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
            log(f"placement pass failed: {_first_line(exc)}")
        time.sleep(interval)


def _fold_pass() -> tuple[dict[str, int], dict[str, str]]:
    with psycopg.connect(_jobs_url(), autocommit=True) as jobs:
        return fold.run(jobs)


def _fold_log(folded: dict[str, int], failed: dict[str, str]) -> int:
    """Log the result of a fold pass; the exit code (6 when any org failed)."""
    if folded or failed:
        log(f"point counts folded for {len(folded)} org(s), {sum(folded.values())} deltas" + _failed_line(failed))
    return EXIT_PARTIAL if failed else 0


def _fold_watch(interval: float) -> None:
    log(f"fold worker running (every {interval:g}s)")
    while True:
        try:
            _fold_log(*_fold_pass())
        except (RunnerError, psycopg.Error, OSError) as exc:        # the database is away: try again
            log(f"fold pass failed: {_first_line(exc)}")
        time.sleep(interval)


def _failed_line(failed: dict[str, str]) -> str:
    return "; failed: " + ", ".join(f"{k} ({m})" for k, m in failed.items()) if failed else ""


def _nightly(url: str, *, every_leaf: bool) -> int:
    """Safe-mode nightly. The order makes every kill repairable: the points of tombstoned rows go first (the tombstones stay), then the
    indexes of the leaves that still have tombstones are rebuilt, then the tombstones of the leaves that were rebuilt go (all of them
    when the mode is off). A leaf whose rebuild failed keeps its tombstones, so the next run rebuilds it."""
    bad = 0
    with psycopg.connect(_jobs_url(), autocommit=True, **KEEPALIVES) as jobs:      # keepalives: a run that lasts hours must notice a dead link
        try:
            if not upkeep.nightly_lock(jobs):               # held on this connection for the whole run
                log("busy: another nightly is running; nothing was done")
                return EXIT_PARTIAL
        except upkeep.PoolerError as exc:
            raise RunnerError(EXIT_CONFIG, str(exc)) from exc
        safe = placement.parse_version(jobs.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]) < placement.SAFE_BELOW

        def options(conn: Connection) -> list[str]:
            nonlocal bad
            leaves = placement.points_leaves(conn)
            for leaf in leaves:                         # in safe mode a leaf without the option gets it, whatever created the leaf or when
                try:
                    placement.set_cleanup_option(conn, leaf, "2s")     # a leaf that is busy is skipped: the next run retries it
                except Exception as exc:
                    bad += 1
                    log(f"{leaf}: autovacuum option not set: {_first_line(exc)}")
            return leaves

        all_leaves = _owner_pass(url, options)
        try:                                                # leaves that lack the index of a space registered after they were made
            _build_indexes(url)
        except Exception as exc:
            bad += 1
            log(f"index build failed: {_first_line(exc)}")
        purged, failed = upkeep.purge_tombstones(jobs)
        bad += len(failed)
        log(f"safe mode {'on' if safe else 'off'}; points purged: {sum(purged.values())} in {len(purged)} org(s)" + _failed_line(failed))
        pairs = upkeep.tombstoned_leaves(jobs)
        for tomb, leaf in pairs.items():
            if leaf is None:
                bad += 1
                log(f"{tomb}: no points leaf with the same orgs (an org that is only partly placed): left alone")
        todo = all_leaves if safe and every_leaf else sorted({lf for lf in pairs.values() if lf})
        rebuilt: set[str] = set()
        for leaf in todo if safe else []:
            try:                                          # one leaf at a time: a deployment never waits for the whole run
                log(f"{leaf}: rebuilt {len(_owner_pass(url, lambda c, leaf=leaf: upkeep.reindex_leaf(c, leaf, log)))} index(es), vacuumed")
                rebuilt.add(leaf)
            except Exception as exc:                        # the other leaves still run
                bad += 1
                log(f"{leaf}: failed: {_first_line(exc)}")
        droppable = [t for t, lf in pairs.items() if lf and (not safe or lf in rebuilt)]
        dropped, failed = upkeep.drop_tombstones(jobs, droppable)
        bad += len(failed)
        log(f"tombstones dropped: {sum(dropped.values())} in {len(dropped)} org(s)" + _failed_line(failed))
    return EXIT_PARTIAL if bad else 0


def main(argv: list[str]) -> int:
    try:
        cmd = argv[0] if argv else ""
        if cmd in ("placement", "build-indexes", "nightly"):
            try:
                placement.check_build_settings()
            except ValueError as exc:
                raise RunnerError(EXIT_CONFIG, str(exc)) from exc
        if cmd == "recount" and len(argv) in (1, 2):
            if len(argv) == 2 and not placement.is_org_id(argv[1]):
                raise RunnerError(EXIT_CONFIG, USAGE)
            busy = 0
            with psycopg.connect(_jobs_url(), autocommit=True) as jobs:
                for org in argv[1:] or fold.orgs_to_recount(jobs):
                    done = fold.recount_org(jobs, org)
                    busy += done is None
                    log(f"org {org}: " + ("busy (a fold or recount holds it; run again)" if done is None else ", ".join(f"collection {c} space {s}: {n}" for c, s, n in done) or "no points"))
            return EXIT_PARTIAL if busy else 0
        if cmd == "fold" and (len(argv) == 1 or (len(argv) in (2, 3) and argv[1] == "--watch")):
            if len(argv) == 1:
                return _fold_log(*_fold_pass())
            _fold_watch(_interval(argv[2:], 30.0))
            return 0
        if cmd == "group-gc" and len(argv) == 1:
            with psycopg.connect(_jobs_url(), autocommit=True) as jobs:
                done, failed = upkeep.group_gc(jobs)
            log(f"groups collected: {sum(done.values())} in {len(done)} org(s)" + _failed_line(failed))
            return EXIT_PARTIAL if failed else 0
        if cmd == "nightly" and (len(argv) == 1 or argv[1:] == ["--all"]):
            return _nightly(migrate_url(), every_leaf=len(argv) == 2)
        if cmd == "build-indexes" and len(argv) == 1:
            log(f"indexes ensured: {', '.join(_build_indexes(migrate_url())) or 'none'}")
            return 0
        if not (cmd == "placement" and (len(argv) == 1 or (len(argv) in (2, 3) and argv[1] == "--watch"))):
            raise RunnerError(EXIT_CONFIG, USAGE)
        url = migrate_url()
        if len(argv) > 1:
            _jobs_url()                       # a missing PG_JOBS_URL never fixes itself: exit 2 here, not an endless retry
            _watch(url, _interval(argv[2:], 1.0))
            return 0
        done, failed, refused = _placement_pass(url)
        log(_summary(done, failed, refused))
        return EXIT_REFUSED if refused else EXIT_FAILED if failed else 0
    except RunnerError as exc:
        log(str(exc))
        return exc.code
    except Exception as exc:
        log(f"failed: {_first_line(exc)}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
