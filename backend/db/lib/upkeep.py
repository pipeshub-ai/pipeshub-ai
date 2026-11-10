"""Upkeep of the vector tables that no write does for itself. Every job is idempotent, resumable (a batch is one transaction, so a
kill loses at most that batch), and safe to run twice at once (one advisory lock per org and job, ``SKIP LOCKED`` on the rows).

Jobs role (``jobs``: a psycopg connection; the tables are org data behind row-level security and its policies see every org):
* :func:`group_gc`: membership rows of groups that no point refers to any more.
* :func:`purge_tombstones` / :func:`drop_tombstones`: the physical delete behind HNSW safe mode (pgvector before 0.8.4).
Owner credential (``owner``: a SQLAlchemy connection outside a transaction, as in ``lib.ddl``):
* :func:`reindex_leaf`: rebuild the vector indexes of a leaf and vacuum it once (safe mode's nightly step).

Safe-mode nightly, in this order so that a kill at any point is repaired by the next run: purge the points of tombstoned rows (the
tombstones stay), rebuild the vector index of every leaf that still has tombstones, then drop the tombstones that no point has.
"""
from collections.abc import Callable

import psycopg
from sqlalchemy import Connection

from db.lib import fold, placement

BATCH = 5000
GC_LOCK, PURGE_LOCK, NIGHTLY_LOCK = 7362, 7363, 7365
GROUP_GRACE = "1 hour"          # a group row younger than this is never collected: its writer may not have committed its points yet


GC = """
WITH dead AS (
  SELECT g.collection_id, g.group_key FROM retrieval.point_groups g
   WHERE g.org_id = %(org)s AND g.computed_at < now() - %(grace)s::interval
     AND NOT EXISTS (SELECT 1 FROM retrieval.points p WHERE p.org_id = %(org)s AND p.collection_id = g.collection_id AND p.group_key = g.group_key)
   ORDER BY g.collection_id, g.group_key LIMIT %(batch)s FOR UPDATE OF g SKIP LOCKED),
mem AS (DELETE FROM retrieval.group_members m USING dead d
         WHERE m.org_id = %(org)s AND m.collection_id = d.collection_id AND m.group_key = d.group_key RETURNING 1),
grp AS (DELETE FROM retrieval.point_groups g USING dead d
         WHERE g.org_id = %(org)s AND g.collection_id = d.collection_id AND g.group_key = d.group_key RETURNING 1)
SELECT (SELECT count(*) FROM grp)"""

# The points of tombstoned rows, locked first with their tombstones (a writer that holds one is skipped: it is reviving it; one that
# revived and committed since this statement's snapshot has deleted the tombstone, which the lock recheck then sees), with their text. Postings and
# the point count were settled when the tombstone was written. The tombstones stay until the index is rebuilt.
PURGE = """
WITH victims AS (
  SELECT p.point_sk FROM retrieval.points p JOIN retrieval.point_tombstones t ON t.org_id = p.org_id AND t.point_sk = p.point_sk
   WHERE p.org_id = %(org)s ORDER BY p.point_sk LIMIT %(batch)s FOR UPDATE OF p, t SKIP LOCKED),
txt AS (DELETE FROM retrieval.point_text x USING victims v WHERE x.org_id = %(org)s AND x.point_sk = v.point_sk RETURNING 1),
pts AS (DELETE FROM retrieval.points d USING victims v WHERE d.org_id = %(org)s AND d.point_sk = v.point_sk RETURNING 1)
SELECT (SELECT count(*) FROM pts)"""

DROP = """
WITH d AS (
  DELETE FROM retrieval.point_tombstones t WHERE t.org_id = %(org)s AND t.point_sk IN (
    SELECT s.point_sk FROM retrieval.point_tombstones s WHERE s.org_id = %(org)s
       AND (%(leaves)s::text[] IS NULL OR s.tableoid::regclass::text = ANY(%(leaves)s::text[]))
       AND NOT EXISTS (SELECT 1 FROM retrieval.points p WHERE p.org_id = %(org)s AND p.point_sk = s.point_sk) LIMIT %(batch)s)
  RETURNING 1)
SELECT count(*) FROM d"""

class PoolerError(RuntimeError):
    """The connection does not keep a session lock (a transaction-mode pooler)."""


def nightly_lock(jobs: psycopg.Connection) -> bool:
    """Take the session-level lock of the whole nightly run on this connection (False: another nightly is running). It is released when
    the connection closes, so a killed run never leaves it held. Raises PoolerError when the connection is behind a pooler that would
    drop it: the lock is proved held by a separate statement, as the migrate lock is."""
    if len({jobs.execute("SELECT pg_backend_pid()").fetchone()[0] for _ in range(3)}) > 1:
        raise PoolerError("PG_JOBS_URL points at a connection pooler; use the direct address")
    if not jobs.execute("SELECT pg_try_advisory_lock(%s, 0)", (NIGHTLY_LOCK,)).fetchone()[0]:
        return False
    if not jobs.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid() "
                        "AND classid = %s", (NIGHTLY_LOCK,)).fetchone()[0]:
        raise PoolerError("the session advisory lock is not held after it was taken: PG_JOBS_URL points at a connection pooler; use the direct address")
    return True


def _one(jobs: psycopg.Connection, org: str, lock_class: int, sql: str, leaves: list[str] | None = None) -> int:
    with jobs.transaction():
        if not fold.org_lock(jobs, org, lock_class):
            return fold.BUSY
        return jobs.execute(sql, {"org": org, "batch": BATCH, "grace": GROUP_GRACE, "leaves": leaves}).fetchone()[0]


def group_gc_org(jobs: psycopg.Connection, org: str) -> int:
    return _one(jobs, org, GC_LOCK, GC)


def purge_org(jobs: psycopg.Connection, org: str) -> int:
    return _one(jobs, org, PURGE_LOCK, PURGE)


def drop_org(jobs: psycopg.Connection, org: str, leaves: list[str] | None = None) -> int:
    return _one(jobs, org, PURGE_LOCK, DROP, leaves)


Result = tuple[dict[str, int], dict[str, str]]       # (org -> rows done, org -> first line of the error)


def group_gc(jobs: psycopg.Connection) -> Result:
    """Delete the membership rows of groups that have no point (and are older than :data:`GROUP_GRACE`); points never lose their
    group, search treats a missing group as "no membership"."""
    return fold.each_org(jobs, fold.orgs_to_recount(jobs), group_gc_org, BATCH)


def tombstoned_orgs(jobs: psycopg.Connection) -> list[str]:
    return [r[0] for r in jobs.execute("SELECT DISTINCT org_id FROM retrieval.point_tombstones ORDER BY 1")]


def purge_tombstones(jobs: psycopg.Connection) -> Result:
    return fold.each_org(jobs, tombstoned_orgs(jobs), purge_org, BATCH)


def drop_tombstones(jobs: psycopg.Connection, tombstone_leaves: list[str] | None = None) -> Result:
    """Drop the tombstones whose point is gone (only those held in ``tombstone_leaves`` when given)."""
    return fold.each_org(jobs, tombstoned_orgs(jobs), lambda j, org: drop_org(j, org, tombstone_leaves), BATCH)


# For each org with tombstones: the leaf they sit in, and the leaf of the points tree that holds the org's points (its own LIST leaf if it
# has one, else the hash leaf its id falls in).
LEAF_PAIRS = """
SELECT tl.org_id, tl.oid::regclass::text, pg_get_expr(tc.relpartbound, tc.oid),
       (SELECT pl.relid::regclass::text FROM pg_partition_tree('retrieval.points') pl JOIN pg_class pc ON pc.oid = pl.relid
         WHERE pl.isleaf AND pg_get_expr(pc.relpartbound, pc.oid) = pg_get_expr(tc.relpartbound, tc.oid)) AS same_bound,
       (SELECT pl.relid::regclass::text FROM pg_partition_tree('retrieval.points') pl JOIN pg_class pc ON pc.oid = pl.relid
         WHERE pl.isleaf AND (pg_get_expr(pc.relpartbound, pc.oid) = format('FOR VALUES IN (%L)', tl.org_id)
            OR (pg_get_expr(pc.relpartbound, pc.oid) ~ 'modulus'
                AND NOT EXISTS (SELECT 1 FROM pg_class o WHERE o.relnamespace = 'retrieval'::regnamespace AND o.relname = 'points_o_' || tl.org_id)
                AND satisfies_hash_partition('retrieval.points_default'::regclass::oid,
                      (regexp_match(pg_get_expr(pc.relpartbound, pc.oid), 'modulus (\\d+), remainder (\\d+)'))[1]::int,
                      (regexp_match(pg_get_expr(pc.relpartbound, pc.oid), 'modulus (\\d+), remainder (\\d+)'))[2]::int, tl.org_id)))) AS holder
  FROM (SELECT DISTINCT org_id, tableoid AS oid FROM retrieval.point_tombstones) tl JOIN pg_class tc ON tc.oid = tl.oid"""


def tombstoned_leaves(jobs: psycopg.Connection) -> dict[str, str | None]:
    """Tombstone leaf -> the points leaf to rebuild, from the catalog. None: the leaf holds tombstones of an org whose points are in
    another kind of leaf (an org that is only partly placed): nothing is rebuilt or dropped for it."""
    pairs: dict[str, str | None] = {}
    for _, tomb, _, same_bound, holder in jobs.execute(LEAF_PAIRS).fetchall():
        ok = holder is not None and holder == same_bound
        pairs[tomb] = (same_bound if ok else None) if pairs.get(tomb, same_bound) is not None else None
    return pairs


def reindex_leaf(owner: Connection, leaf: str, report: Callable[[str], None] | None = None) -> list[str]:
    """Rebuild the vector indexes of ``leaf`` (``REINDEX INDEX CONCURRENTLY``, so reads and writes go on) and vacuum it once: the
    fresh indexes hold no dead rows, so the vacuum does the cleanup that autovacuum is told not to do. Leftovers of an interrupted
    run are dropped first. Returns the indexes rebuilt."""
    for (ix,) in owner.exec_driver_sql(
            "SELECT i.indexrelid::regclass::text FROM pg_index i WHERE i.indrelid = %s::regclass AND NOT i.indisvalid "
            "AND i.indexrelid::regclass::text ~ '_cc(new|old)[0-9]*$'", (leaf,)).fetchall():
        owner.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {ix}")
    names = [r[0] for r in owner.exec_driver_sql(
        "SELECT i.indexrelid::regclass::text FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_am a ON a.oid = c.relam "
        "WHERE i.indrelid = %s::regclass AND i.indisvalid AND a.amname = 'hnsw' ORDER BY 1", (leaf,))]
    if names:
        plan = placement._plan(owner, leaf, None, placement._spaces(owner))
        if report:
            report(f"{leaf}: rebuilding {len(names)} index(es) with {plan.memory_mb} MB and {plan.workers} worker(s)")
        owner.exec_driver_sql(f"SET maintenance_work_mem = '{plan.memory_mb}MB'")
        owner.exec_driver_sql(f"SET max_parallel_maintenance_workers = {plan.workers}")
        try:
            for ix in names:
                owner.exec_driver_sql(f"REINDEX INDEX CONCURRENTLY {ix}")
        finally:
            owner.exec_driver_sql("RESET maintenance_work_mem")
            owner.exec_driver_sql("RESET max_parallel_maintenance_workers")
    owner.exec_driver_sql(f"VACUUM (INDEX_CLEANUP ON) {leaf}")
    return names
