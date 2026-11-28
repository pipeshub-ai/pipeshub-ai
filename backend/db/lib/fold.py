"""The point count of each collection and space, kept as a folded counter plus deltas.

Every vector write adds a row under :data:`COUNT_TERM` to ``retrieval.lexical_df_delta`` (the change in the number of points that have terms, taken
from the rows the statement actually inserted, deleted or tombstoned), so writers never contend on a hot counter row. The search
adds the unfolded deltas to ``retrieval.collection_stats.n_points``, so the count is exact at once; ``fold_org`` only compacts them,
in one transaction per org. ``recount_org`` recomputes the counter from the points (the safety net against any drift). Both run as the
jobs role (the tables are org data behind row-level security; the jobs policies see every org).

The document frequency of each term is kept the same way: triggers on the postings add ``retrieval.lexical_term_delta`` rows (retrieval_0005),
``fold_org`` folds them into ``retrieval.lexical_df`` in the same transaction as the point count, and ``recount_org`` recomputes them from
the postings (which also ends the counting of a collection whose postings predate the triggers, ``retrieval.lexical_df_pending``).
Beside each frequency they keep ``wmax``, the largest |weight| of the term's postings (retrieval_0008), the bound the lexical search prunes
with: the fold keeps the larger of the folded and the delta values, recount computes it, and NULL (a row written before retrieval_0008) means
not known, which the fold carries and the search does not prune on. ``fill_bounds`` (each fold pass) computes the missing ones from the
postings, a few terms per transaction, for the collections in ``retrieval.lexical_bound_pending`` (every collection at the upgrade, and any
that gets a frequency row without a bound later), so an upgraded install prunes without an operator step. These writers say so in their
transaction (``keeps_bounds``); a writer that does not (an earlier release's fold or recount) has its bounds cleared by the triggers.
"""
from collections.abc import Callable

import psycopg

BUSY = -1                       # what a step returns when another run of the same job holds the org (not an empty batch)
COUNT_TERM = -1                 # lexical_df_delta rows under this term carry the change in a collection's point count
BATCH = 100_000                 # deltas taken per transaction: a long backlog is folded over several passes
MAX_BATCHES = 100               # transactions one org gets per pass, so a pass ends even under a steady stream of work
LOCK_CLASS = 7361               # pg_advisory_xact_lock(class, hashtext(org)): one fold or recount per org at a time

# Orgs that have deltas of either kind: a loose index scan over (org_id, seq) of both tables, so a long backlog costs one probe per org,
# not one per row (least() skips the table that has none left).
ORGS = """
WITH RECURSIVE o AS (
  SELECT least((SELECT min(org_id) FROM retrieval.lexical_df_delta WHERE org_id > %(after)s),
               (SELECT min(org_id) FROM retrieval.lexical_term_delta WHERE org_id > %(after)s)) AS org_id
  UNION ALL SELECT least((SELECT min(d.org_id) FROM retrieval.lexical_df_delta d WHERE d.org_id > o.org_id),
                         (SELECT min(t.org_id) FROM retrieval.lexical_term_delta t WHERE t.org_id > o.org_id)) FROM o WHERE o.org_id IS NOT NULL)
SELECT org_id FROM o WHERE org_id IS NOT NULL LIMIT %(limit)s"""

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

# A frequency that folds to 0 loses its row instead (the search reads a missing row as 0), so the table does not keep every term that
# ever came and went. The current values are read in the statement's snapshot: only a fold or a recount changes them, under the org lock.
# A delta that nets to nothing still folds its bound (a weight that grew); a bound not known on either side stays not known.
FOLD_TERMS = """
WITH taken AS (
  DELETE FROM retrieval.lexical_term_delta d WHERE d.org_id = %(org)s
     AND d.seq IN (SELECT t.seq FROM retrieval.lexical_term_delta t WHERE t.org_id = %(org)s ORDER BY t.seq LIMIT %(batch)s)
  RETURNING d.collection_id, d.space_id, d.term, d.d, d.wmax),
sums AS (
  SELECT s.collection_id, s.space_id, s.term, s.d, coalesce(f.df, 0) + s.d AS df,
         CASE WHEN NOT s.unknown AND (f.term IS NULL OR f.wmax IS NOT NULL) THEN greatest(f.wmax, s.wmax) END AS wmax
    FROM (SELECT collection_id, space_id, term, sum(d)::bigint AS d, max(wmax) AS wmax, bool_or(wmax IS NULL) AS unknown FROM taken GROUP BY 1, 2, 3) s
    LEFT JOIN retrieval.lexical_df f ON f.org_id = %(org)s AND f.collection_id = s.collection_id AND f.space_id = s.space_id AND f.term = s.term),
gone AS (
  DELETE FROM retrieval.lexical_df f USING sums s
   WHERE f.org_id = %(org)s AND f.collection_id = s.collection_id AND f.space_id = s.space_id AND f.term = s.term AND s.df = 0
  RETURNING 1),
up AS (
  INSERT INTO retrieval.lexical_df AS f (org_id, collection_id, space_id, term, df, wmax)
  SELECT %(org)s, s.collection_id, s.space_id, s.term, s.d, s.wmax FROM sums s WHERE s.df <> 0
  ON CONFLICT (org_id, collection_id, space_id, term) DO UPDATE SET df = f.df + EXCLUDED.df, wmax = EXCLUDED.wmax
  RETURNING 1)
SELECT (SELECT count(*) FROM taken)"""

# Every term's frequency from the postings of one snapshot (the recount's), minus the deltas that snapshot sees, as RECOUNT does for the
# point count; a delta committed later belongs to a posting the snapshot does not count either. The collections it covered stop counting.
# The bound is the largest |weight| the snapshot holds (0 for none). A delta it sees without one (written before retrieval_0008) keeps the
# term not known until it is folded: its posting may already be gone from the snapshot while the delta that adds it back is still pending.
RECOUNT_TERMS = """
WITH live AS (
  SELECT p.collection_id, p.space_id, p.term, count(*) AS n, max(abs(p.w)) AS wmax FROM retrieval.postings p WHERE p.org_id = %(org)s GROUP BY 1, 2, 3),
pending AS (SELECT d.collection_id, d.space_id, d.term, sum(d.d) AS d FROM retrieval.lexical_term_delta d WHERE d.org_id = %(org)s GROUP BY 1, 2, 3),
stale AS (
  DELETE FROM retrieval.lexical_df f WHERE f.org_id = %(org)s
     AND NOT EXISTS (SELECT 1 FROM live l WHERE l.collection_id = f.collection_id AND l.space_id = f.space_id AND l.term = f.term)
     AND NOT EXISTS (SELECT 1 FROM pending x WHERE x.collection_id = f.collection_id AND x.space_id = f.space_id AND x.term = f.term)
  RETURNING 1),
done AS (DELETE FROM retrieval.lexical_df_pending x WHERE x.org_id = %(org)s RETURNING 1),
bounded AS (DELETE FROM retrieval.lexical_bound_pending x WHERE x.org_id = %(org)s RETURNING 1),
up AS (
  INSERT INTO retrieval.lexical_df AS f (org_id, collection_id, space_id, term, df, wmax)
  SELECT %(org)s, k.collection_id, k.space_id, k.term, coalesce(l.n, 0) - coalesce(x.d, 0), coalesce(l.wmax, 0)
    FROM (SELECT collection_id, space_id, term FROM live UNION SELECT collection_id, space_id, term FROM pending) k
    LEFT JOIN live l USING (collection_id, space_id, term) LEFT JOIN pending x USING (collection_id, space_id, term)
  ON CONFLICT (org_id, collection_id, space_id, term) DO UPDATE SET df = EXCLUDED.df, wmax = EXCLUDED.wmax
  RETURNING 1)
SELECT (SELECT count(*) FROM up)"""
# The bound of up to BOUND_TERMS frequency rows that lack one: the largest |weight| of the term's postings in this statement's snapshot (a
# posting committed later carries its bound on its own delta). Under the org lock, so no fold rewrites the rows meanwhile; a delta without
# a bound still unfolded keeps the term not known for the search, and its fold marks the collection again.
BOUND_TERMS = 200
FILL_BOUNDS = """
WITH todo AS (
  SELECT f.collection_id, f.space_id, f.term FROM retrieval.lexical_df f
   WHERE f.org_id = %(org)s AND f.wmax IS NULL ORDER BY f.collection_id, f.space_id, f.term LIMIT %(terms)s),
filled AS (
  UPDATE retrieval.lexical_df f SET wmax = coalesce((SELECT max(abs(p.w)) FROM retrieval.postings p
      WHERE p.org_id = %(org)s AND p.collection_id = t.collection_id AND p.space_id = t.space_id AND p.term = t.term), 0)
    FROM todo t
   WHERE f.org_id = %(org)s AND f.collection_id = t.collection_id AND f.space_id = t.space_id AND f.term = t.term
  RETURNING 1)
SELECT count(*) FROM filled"""

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
    return [r[0] for r in jobs.execute(ORGS, {"after": after, "limit": limit})]


def keeps_bounds(jobs: psycopg.Connection) -> None:
    """Tell the triggers of retrieval_0008 that this transaction keeps the term bounds it writes (any other writer's are cleared)."""
    jobs.execute("SELECT set_config('retrieval.bounds_kept', 'on', true)")


PROFILE_BARRIER = 7341      # kernel.lock_keys: the profile fence's barrier, which a hold is recorded behind


def org_lock(jobs: psycopg.Connection, org: str, lock_class: int = LOCK_CLASS) -> bool:
    """Take the org's transaction-scoped advisory lock of this job class (False: another run of the same job holds it). Raises
    ReadOnlySqlTransaction for an org whose writes are held (kernel.assert_org_writable): the step writes nothing and the org is
    reported as failed, for the next pass."""
    if not jobs.execute("SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))", (lock_class, org)).fetchone()[0]:
        return False
    return jobs.execute("SELECT kernel.assert_org_writable(%s)", (org,)).fetchone()[0]


def fold_org(jobs: psycopg.Connection, org: str) -> int:
    """Fold up to :data:`BATCH` deltas of ``org`` in one transaction; returns how many were folded (0: none; :data:`BUSY`: another fold
    holds the org)."""
    with jobs.transaction():
        if not org_lock(jobs, org):
            return BUSY
        keeps_bounds(jobs)
        return (jobs.execute(FOLD, {"org": org, "batch": BATCH}).fetchone()[0]
                + jobs.execute(FOLD_TERMS, {"org": org, "batch": BATCH}).fetchone()[0])


def recount_org(jobs: psycopg.Connection, org: str) -> list[tuple[int, int, int]] | None:
    """Recompute the point counts of ``org`` from its live points, and its term frequencies from its postings (None: another fold or recount holds the org, or a fold committed under it; nothing changed). Returns
    (collection, space, count) as the search will see it."""
    # The write barrier (7341) is taken shared for the session BEFORE the REPEATABLE READ snapshot: a hold recorded before it is
    # committed, so the snapshot sees it at the fence; one recorded after it waits for this recount. Taken inside the transaction,
    # the snapshot (fixed by its first statement) could predate a hold that committed before the fence ran. PG_JOBS_URL is a
    # direct address (no pooler), so a session lock is this connection's.
    if not jobs.execute("SELECT pg_try_advisory_lock_shared(%s, 1)", (PROFILE_BARRIER,)).fetchone()[0]:
        return None                                  # a hold is being recorded, or the profile switched: run again
    try:
        with jobs.transaction():
            jobs.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            if not org_lock(jobs, org):
                return None
            keeps_bounds(jobs)
            counts = [tuple(r) for r in jobs.execute(RECOUNT, {"org": org}).fetchall()]
            jobs.execute(RECOUNT_TERMS, {"org": org})
            return counts
    except psycopg.errors.SerializationFailure:      # a fold committed between the lock and the snapshot: nothing changed, run again
        return None
    finally:
        jobs.execute("SELECT pg_advisory_unlock_shared(%s, 1)", (PROFILE_BARRIER,))


def fill_bounds_org(jobs: psycopg.Connection, org: str) -> int:
    """Fill up to :data:`BOUND_TERMS` missing bounds of ``org`` in one transaction; when fewer were missing, the org leaves
    ``lexical_bound_pending``. Returns how many were filled (:data:`BUSY`: a fold or recount holds the org)."""
    with jobs.transaction():
        if not org_lock(jobs, org):
            return BUSY
        keeps_bounds(jobs)
        n = jobs.execute(FILL_BOUNDS, {"org": org, "terms": BOUND_TERMS}).fetchone()[0]
        if n < BOUND_TERMS:
            jobs.execute("DELETE FROM retrieval.lexical_bound_pending WHERE org_id = %(org)s", {"org": org})
        return n


def fill_bounds(jobs: psycopg.Connection) -> tuple[dict[str, int], dict[str, str], int]:
    """One pass over the orgs with collections in ``lexical_bound_pending``: (org -> bounds filled, org -> error, collections still pending)."""
    orgs = [r[0] for r in jobs.execute("SELECT DISTINCT org_id FROM retrieval.lexical_bound_pending ORDER BY 1")]
    done, failed = each_org(jobs, orgs, fill_bounds_org, BOUND_TERMS)
    return done, failed, jobs.execute("SELECT count(*) FROM retrieval.lexical_bound_pending").fetchone()[0]


def each_org(jobs: psycopg.Connection, orgs: list[str], step: Callable[[psycopg.Connection, str], int], batch: int) -> tuple[dict[str, int], dict[str, str]]:
    """Run ``step`` (one transaction, at most ``batch`` units, returns how many it did) for each org until its backlog is done or MAX_BATCHES transactions were spent:
    (org -> units done, org -> first line of the error). An org that fails is left for the next pass and does not hold up the others."""
    done: dict[str, int] = {}
    failed: dict[str, str] = {}
    for org in orgs:
        try:
            for _ in range(MAX_BATCHES):                     # a backlog goes in several transactions; the rest waits for the next pass
                if (n := step(jobs, org)) == BUSY:           # another run holds the org: reported, so the exit code says "run again"
                    failed[org] = "busy (another run of the job holds it)"
                    break
                if not n:
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
