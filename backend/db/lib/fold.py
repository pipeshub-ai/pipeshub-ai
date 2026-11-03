"""The point count of each collection and space, kept as a folded counter plus deltas.

Every vector write adds a row under :data:`COUNT_TERM` to ``retrieval.lexical_df_delta`` (the change in the number of points that have terms, taken
from the rows the statement actually inserted, deleted or tombstoned), so writers never contend on a hot counter row. The search
adds the unfolded deltas to ``retrieval.collection_stats.n_points``, so the count is exact at once; ``fold_org`` only compacts them,
in one transaction per org. ``recount_org`` recomputes the counter from the points (the safety net against any drift). Both run as the
jobs role (the tables are org data behind row-level security; the jobs policies see every org). Document frequencies are not stored:
a term's frequency is the number of its postings.
"""
from collections.abc import Callable

import psycopg

COUNT_TERM = -1                 # lexical_df_delta rows under this term carry the change in a collection's point count
BATCH = 100_000                 # deltas taken per transaction: a long backlog is folded over several passes
MAX_BATCHES = 100               # transactions one org gets per pass, so a pass ends even under a steady stream of work
LOCK_CLASS = 7361               # pg_advisory_xact_lock(class, hashtext(org)): one fold or recount per org at a time

# Orgs that have deltas: a loose index scan over (org_id, seq), so a long backlog costs one probe per org, not one per row.
ORGS = """
WITH RECURSIVE o AS (
  SELECT min(org_id) AS org_id FROM retrieval.lexical_df_delta WHERE org_id > %s
  UNION ALL SELECT (SELECT min(d.org_id) FROM retrieval.lexical_df_delta d WHERE d.org_id > o.org_id) FROM o WHERE o.org_id IS NOT NULL)
SELECT org_id FROM o WHERE org_id IS NOT NULL LIMIT %s"""

FOLD = """
WITH taken AS (
  DELETE FROM retrieval.lexical_df_delta d WHERE d.org_id = %(org)s
     AND d.seq IN (SELECT t.seq FROM retrieval.lexical_df_delta t WHERE t.org_id = %(org)s ORDER BY t.seq LIMIT %(batch)s)
  RETURNING d.collection_id, d.space_id, d.d),
sums AS (SELECT collection_id, space_id, sum(d)::bigint AS d FROM taken GROUP BY collection_id, space_id HAVING sum(d) <> 0),
cnt AS (
  INSERT INTO retrieval.collection_stats AS c (org_id, collection_id, space_id, n_points)
  SELECT %(org)s, s.collection_id, s.space_id, s.d FROM sums s
  ON CONFLICT (org_id, collection_id, space_id) DO UPDATE SET n_points = c.n_points + EXCLUDED.n_points
  RETURNING 1)
SELECT (SELECT count(*) FROM taken)"""

# The true count of live points per (collection, space), from one snapshot, minus the deltas that snapshot already sees: the search
# adds the deltas back, and a delta committed later belongs to a point this snapshot does not count either.
RECOUNT = """
WITH live AS (
  SELECT p.collection_id, p.space_id, count(*) AS n FROM retrieval.points p
   WHERE p.org_id = %(org)s AND p.lex_terms IS NOT NULL AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = %(org)s AND t.point_sk = p.point_sk)
   GROUP BY p.collection_id, p.space_id),
pending AS (SELECT d.collection_id, d.space_id, sum(d.d) AS d FROM retrieval.lexical_df_delta d WHERE d.org_id = %(org)s GROUP BY d.collection_id, d.space_id),
keys AS (SELECT collection_id, space_id FROM live UNION SELECT collection_id, space_id FROM pending
         UNION SELECT s.collection_id, s.space_id FROM retrieval.collection_stats s WHERE s.org_id = %(org)s)
INSERT INTO retrieval.collection_stats AS c (org_id, collection_id, space_id, n_points)
SELECT %(org)s, k.collection_id, k.space_id, coalesce(l.n, 0) - coalesce(p.d, 0)
  FROM keys k LEFT JOIN live l USING (collection_id, space_id) LEFT JOIN pending p USING (collection_id, space_id)
ON CONFLICT (org_id, collection_id, space_id) DO UPDATE SET n_points = EXCLUDED.n_points
RETURNING c.collection_id, c.space_id, c.n_points + coalesce((SELECT sum(d.d) FROM retrieval.lexical_df_delta d
   WHERE d.org_id = %(org)s AND d.collection_id = c.collection_id AND d.space_id = c.space_id), 0)"""


def pending_orgs(jobs: psycopg.Connection, limit: int = 1000, after: str = "") -> list[str]:
    """Up to ``limit`` orgs with deltas whose id is greater than ``after``, in id order."""
    return [r[0] for r in jobs.execute(ORGS, (after, limit))]


def org_lock(jobs: psycopg.Connection, org: str, lock_class: int = LOCK_CLASS) -> bool:
    """Take the org's transaction-scoped advisory lock of this job class (False: another run of the same job holds it)."""
    return jobs.execute("SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))", (lock_class, org)).fetchone()[0]


def fold_org(jobs: psycopg.Connection, org: str) -> int:
    """Fold up to :data:`BATCH` deltas of ``org`` in one transaction; returns how many were folded (0: none, or another fold holds
    the org)."""
    with jobs.transaction():
        if not org_lock(jobs, org):
            return 0
        return jobs.execute(FOLD, {"org": org, "batch": BATCH}).fetchone()[0]


def recount_org(jobs: psycopg.Connection, org: str) -> list[tuple[int, int, int]] | None:
    """Recompute the point counts of ``org`` from its live points (None: another fold or recount holds the org, or a fold committed under it; nothing changed). Returns
    (collection, space, count) as the search will see it."""
    try:
        with jobs.transaction():
            jobs.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if not org_lock(jobs, org):
                return None
            return [tuple(r) for r in jobs.execute(RECOUNT, {"org": org}).fetchall()]
    except psycopg.errors.SerializationFailure:      # a fold committed between the lock and the snapshot: nothing changed, run again
        return None


def each_org(jobs: psycopg.Connection, orgs: list[str], step: Callable[[psycopg.Connection, str], int], batch: int) -> tuple[dict[str, int], dict[str, str]]:
    """Run ``step`` (one transaction, at most ``batch`` units, returns how many it did) for each org until its backlog is done or MAX_BATCHES transactions were spent:
    (org -> units done, org -> first line of the error). An org that fails is left for the next pass and does not hold up the others."""
    done: dict[str, int] = {}
    failed: dict[str, str] = {}
    for org in orgs:
        try:
            for _ in range(MAX_BATCHES):                     # a backlog goes in several transactions; the rest waits for the next pass
                if not (n := step(jobs, org)):
                    break
                done[org] = done.get(org, 0) + n
                if n < batch:
                    break
        except psycopg.Error as exc:
            failed[org] = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    return done, failed


def run(jobs: psycopg.Connection) -> tuple[dict[str, int], dict[str, str]]:
    """One pass over every org with deltas, each once (an org that keeps failing costs one attempt per pass and holds up none of the
    orgs behind it, however many there are): (org -> deltas folded, org -> error)."""
    done: dict[str, int] = {}
    failed: dict[str, str] = {}
    after = ""
    while page := pending_orgs(jobs, after=after):
        d, f = each_org(jobs, page, fold_org, BATCH)
        done |= d
        failed |= f
        after = page[-1]
    return done, failed


def orgs_to_recount(jobs: psycopg.Connection) -> list[str]:
    """Every org that has a space or a counter (the orgs that can hold points), for a full recount."""
    return [r[0] for r in jobs.execute(
        "SELECT org_id FROM retrieval.org_spaces UNION SELECT org_id FROM retrieval.collection_stats ORDER BY 1")]
