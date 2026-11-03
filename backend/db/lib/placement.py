"""Where an org's vector rows live: the placement worker's side.

``retrieval`` tables are partitioned LIST (org_id). An org without its own leaf shares the DEFAULT tree (hash leaves). The placement
policy gives an org a leaf of its own BEFORE it writes, so nothing is ever moved: a per-org index, plan-time pruning and an O(1) drop.
Creating a leaf is DDL, which no service may run, so the writing service files a request (``retrieval.org_leaf_request``) and waits for
this worker to close it (``retrieval.org_leaf_status``).

Run as the migrate/owner credential (``owner``: a SQLAlchemy connection outside a transaction, as in ``lib.ddl``) with the jobs
credential beside it (``jobs``: the request table is org data behind row-level security, which the owner cannot read). One transaction
per table, so a kill leaves nothing behind. An org that already has rows in the shared tree is refused (moving live rows is a separate,
offline operation, not this worker's).
"""
import logging
import re

import psycopg
from sqlalchemy import Connection

from db.lib import ddl

ROOTS = ("retrieval.points", "retrieval.point_text", "retrieval.point_groups", "retrieval.group_members",
         "retrieval.point_tombstones", "retrieval.postings", "retrieval.lexical_df_delta")
POINTS = "retrieval.points"
POSTINGS, POSTINGS_OPTIONS = ddl.POSTINGS, ddl.POSTINGS_OPTIONS
SAFE_BELOW = (0, 8, 4)      # pgvector versions below this have HNSW vacuum bugs: deletes are tombstoned (HNSW safe mode)
log = logging.getLogger(__name__)
_ORG = re.compile(r"^[0-9a-f]{24}$")


class Refused(Exception):
    """The placement rules refuse this request (the org already has rows in the shared tree, or an orphan leaf holds rows)."""


def leaf_name(root: str, org: str) -> str:
    if root not in ROOTS:
        raise ValueError(f"not a partitioned root: {root!r}")
    if not _ORG.match(org):
        raise ValueError(f"not an org id: {org!r}")
    return f"{root}_o_{org}"


def _attached(owner: Connection, leaf: str) -> bool:
    return bool(owner.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(%s))", (leaf,)).scalar())


def _caps(owner: Connection) -> tuple[int, tuple[int, ...]]:
    major = int(owner.exec_driver_sql("SELECT current_setting('server_version_num')::int / 10000").scalar())
    v = owner.exec_driver_sql("SELECT extversion FROM pg_extension WHERE extname = 'vector'").scalar()
    if v is None:
        raise RuntimeError("the vector extension is not installed")
    return major, tuple(int(x) for x in v.split("."))


def safe_mode(owner: Connection) -> bool:
    """pgvector before 0.8.4: vacuuming an HNSW index can leave the graph unrepaired, so deletes are tombstoned and autovacuum does not
    clean the vector indexes (``vacuum_index_cleanup = off`` on the leaves)."""
    return _caps(owner)[1] < SAFE_BELOW


def _index_specs(owner: Connection, leaf: str) -> list[tuple[str, str]]:
    """(name, definition after the index name) of the indexes of one points leaf: per embedding space the partial bit HNSW (hamming) on
    the binary-quantised column (the halfvec rerank reads the heap). Left out on PostgreSQL 18 before pgvector 0.8.3 (a Hamming
    regression)."""
    major, ver = _caps(owner)
    if major >= 18 and ver < (0, 8, 3):
        return []
    short = leaf.split(".")[1]
    spaces = [(int(r[0]), int(r[1])) for r in owner.exec_driver_sql("SELECT space_id, dim FROM retrieval.embedding_spaces ORDER BY 1")]
    return [(f"{short}_bq_s{sid}", f"ON {leaf} USING hnsw ((bq::bit({int(dim)})) ext.bit_hamming_ops) "
                                   f"WITH (m = 16, ef_construction = 128) WHERE space_id = {int(sid)}") for sid, dim in spaces]


def _place(owner: Connection, root: str, leaf: str, org: str) -> None:
    """Create the (empty) leaf, secure it and ATTACH it: one transaction, so a kill or a failure leaves no table and no constraint."""
    owner.exec_driver_sql("BEGIN")
    try:
        owner.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        if owner.exec_driver_sql("SELECT to_regclass(%s) IS NOT NULL", (leaf,)).scalar():       # an unattached orphan of an older run
            owner.exec_driver_sql(f"ALTER TABLE {leaf} NO FORCE ROW LEVEL SECURITY")             # so the owner can see whether it holds rows
            if owner.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {leaf})").scalar():
                raise Refused(f"{leaf} exists, is not attached and holds rows (an older failed run): inspect it before dropping it")
            owner.exec_driver_sql(f"DROP TABLE {leaf}")
        owner.exec_driver_sql(f"CREATE TABLE {leaf} (LIKE {root} INCLUDING ALL EXCLUDING IDENTITY)")
        if root == POSTINGS:
            owner.exec_driver_sql(f"ALTER TABLE {leaf} SET ({POSTINGS_OPTIONS})")
        owner.exec_driver_sql("SELECT kernel.apply_rls_partition(%s::regclass, %s::regclass)", (leaf, root))
        if root == POINTS:                       # indexed with the leaf, before it is attached: plain CREATE INDEX on an empty table is instant
            if safe_mode(owner):
                owner.exec_driver_sql(f"ALTER TABLE {leaf} SET (vacuum_index_cleanup = off)")
            for name, tail in _index_specs(owner, leaf):
                owner.exec_driver_sql(f"CREATE INDEX {name} {tail}")
        owner.exec_driver_sql(f"ALTER TABLE {root} ATTACH PARTITION {leaf} FOR VALUES IN ('{org}')")
        owner.exec_driver_sql("COMMIT")
    except BaseException:
        owner.exec_driver_sql("ROLLBACK")
        raise


def ensure_org_leaf(owner: Connection, jobs: psycopg.Connection, org: str, roots: tuple[str, ...] = ROOTS) -> None:
    """Give ``org`` a LIST leaf in every root BEFORE it has written anything. Refuses (Refused) when the org already has rows in the
    shared tree. Each root is one transaction; a root that already has the org's leaf is left alone, so a run that stopped half way
    is finished by the next."""
    for root in roots:
        leaf = leaf_name(root, org)
        if _attached(owner, leaf):
            continue
        if jobs.execute(f"SELECT EXISTS (SELECT 1 FROM {root} WHERE org_id = %s)", (org,)).fetchone()[0]:
            raise Refused(f"org {org} already has rows in {root}: its rows must be moved out of the shared tree first (an offline operation)")
        _place(owner, root, leaf, org)


MAX_ATTEMPTS = 8
MAX_PER_PASS = 20          # requests one pass handles: the pass runs under the migrate lock, which a deployment waits for
PENDING = "done_at IS NULL AND failed_at IS NULL AND (next_try_at IS NULL OR next_try_at <= now())"


def has_pending(jobs: psycopg.Connection) -> bool:
    return jobs.execute(f"SELECT 1 FROM retrieval.placement_requests WHERE {PENDING} LIMIT 1").fetchone() is not None


def run_requests(owner: Connection, jobs: psycopg.Connection, limit: int | None = None) -> tuple[list[str], list[str]]:
    """Close every pending placement request: create the org's leaf (it has no rows yet). Every request ends: placed (done_at), or
    closed as failed with the reason in ``note`` (an org that already has rows is refused at once; a failing one is retried with a
    growing delay and given up after MAX_ATTEMPTS). Requests that are closed or waiting out a delay are not looked at, so they never
    hold up newer ones. A pass handles at most ``limit`` requests (oldest first), so the migrate lock it runs under is never held for
    long; the worker's next pass takes the rest. A new ask re-opens a failed request (see ``retrieval.org_leaf_request``).
    Returns (done, failed)."""
    done, failed = [], []
    batch = [r[0] for r in jobs.execute(f"SELECT org_id FROM retrieval.placement_requests WHERE {PENDING} ORDER BY requested_at LIMIT %s", (limit or MAX_PER_PASS,))]
    for org in batch:
        try:
            ensure_org_leaf(owner, jobs, org)
        except Refused as e:
            jobs.execute("UPDATE retrieval.placement_requests SET failed_at = now(), note = %s WHERE org_id = %s", (str(e), org))
            failed.append(org)
        except Exception as e:
            reason = (str(e).strip().splitlines() or [type(e).__name__])[0][:300]
            jobs.execute(
                "UPDATE retrieval.placement_requests SET attempts = attempts + 1, "
                "next_try_at = now() + make_interval(secs => least(power(2, attempts), 60)), "
                "failed_at = CASE WHEN attempts + 1 >= %s THEN now() END, "
                "note = CASE WHEN attempts + 1 >= %s THEN %s END WHERE org_id = %s",
                (MAX_ATTEMPTS, MAX_ATTEMPTS, f"gave up after {MAX_ATTEMPTS} attempts: {reason}", org))
            failed.append(org)
        else:
            jobs.execute("UPDATE retrieval.placement_requests SET done_at = now(), note = NULL WHERE org_id = %s", (org,))
            done.append(org)
    return done, failed
