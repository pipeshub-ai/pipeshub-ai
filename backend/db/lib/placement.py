"""Where an org's vector rows live: the placement worker's side, and the vector indexes of every points leaf (built when a leaf is
made, and later by the ``build-indexes`` job for a space registered after the leaf existed).

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
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from sqlalchemy import Connection

from db.lib import ddl

ROOTS = ("retrieval.points", "retrieval.point_text", "retrieval.point_groups", "retrieval.group_members",
         "retrieval.point_tombstones", "retrieval.postings", "retrieval.lexical_df_delta")
POINTS = "retrieval.points"
POSTINGS, POSTINGS_OPTIONS = ddl.POSTINGS, ddl.POSTINGS_OPTIONS
SAFE_BELOW = (0, 8, 4)      # pgvector versions below this have HNSW vacuum bugs: deletes are tombstoned (HNSW safe mode)
log = logging.getLogger(__name__)
_ORG = re.compile(r"[0-9a-f]{24}")


class Refused(Exception):
    """The placement rules refuse this request (the org already has rows in the shared tree, or an orphan leaf holds rows)."""


def is_org_id(org: str) -> bool:
    return bool(_ORG.fullmatch(org))


def leaf_name(root: str, org: str) -> str:
    if root not in ROOTS:
        raise ValueError(f"not a partitioned root: {root!r}")
    if not _ORG.fullmatch(org):
        raise ValueError(f"not an org id: {org!r}")
    return f"{root}_o_{org}"


def _attached(owner: Connection, leaf: str) -> bool:
    return bool(owner.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(%s))", (leaf,)).scalar())


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def _caps(owner: Connection) -> tuple[int, tuple[int, ...]]:
    major = int(owner.exec_driver_sql("SELECT current_setting('server_version_num')::int / 10000").scalar())
    v = owner.exec_driver_sql("SELECT extversion FROM pg_extension WHERE extname = 'vector'").scalar()
    if v is None:
        raise RuntimeError("the vector extension is not installed")
    return major, parse_version(v)


def safe_mode(owner: Connection) -> bool:
    """pgvector before 0.8.4: vacuuming an HNSW index can leave the graph unrepaired, so deletes are tombstoned and autovacuum does not
    clean the vector indexes (``vacuum_index_cleanup = off`` on the leaves)."""
    return _caps(owner)[1] < SAFE_BELOW


def _index_specs(owner: Connection, leaf: str, spaces: list[tuple[int, int]]) -> list[tuple[str, str]]:
    """(name, definition after the index name) of the indexes of one points leaf: per embedding space the partial bit HNSW (hamming) on
    the binary-quantised column (the halfvec rerank reads the heap). Left out on PostgreSQL 18 before pgvector 0.8.3 (a Hamming
    regression)."""
    major, ver = _caps(owner)
    if major >= 18 and ver < (0, 8, 3):
        return []
    short = leaf.split(".")[1]
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
            set_cleanup_option(owner, leaf)
            create_leaf_indexes(owner, leaf)
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
    growing delay (1, 2, 4 ... 60 seconds) and given up after MAX_ATTEMPTS, about two minutes in all). Requests that are closed or waiting out a delay are not looked at, so they never
    hold up newer ones. A pass handles at most ``limit`` requests (oldest first), so the migrate lock it runs under is never held for
    long; the worker's next pass takes the rest. A new ask re-opens a failed request (see ``retrieval.org_leaf_request``).
    A request whose org id is malformed is refused at once, never retried. Returns (done, failed, refused): ``failed`` are the ones
    that raised (waiting for a retry or given up), ``refused`` the ones closed by the rules."""
    done, failed, refused = [], [], []
    batch = [r[0] for r in jobs.execute(f"SELECT org_id FROM retrieval.placement_requests WHERE {PENDING} ORDER BY requested_at LIMIT %s", (limit or MAX_PER_PASS,))]
    for org in batch:
        try:
            if not _ORG.fullmatch(org):
                raise Refused("the request names a malformed organisation id")
            ensure_org_leaf(owner, jobs, org)
        except Refused as e:
            jobs.execute("UPDATE retrieval.placement_requests SET failed_at = now(), note = %s WHERE org_id = %s", (str(e), org))
            refused.append(org)
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
    return done, failed, refused


# ---- vector indexes -----------------------------------------------------------------------------------------------------

def points_leaves(owner: Connection) -> list[str]:
    """Every leaf (a partition with no partitions below it) of the points tree."""
    return [r[0] for r in owner.exec_driver_sql(
        "SELECT relid::regclass::text FROM pg_partition_tree(%s::regclass) WHERE isleaf ORDER BY 1", (POINTS,))]


def _spaces(owner: Connection) -> list[tuple[int, int]]:
    return [(int(r[0]), int(r[1])) for r in owner.exec_driver_sql("SELECT space_id, dim FROM retrieval.embedding_spaces ORDER BY 1")]


# ---- build sizing -------------------------------------------------------------------------------------------------------
# pgvector builds the HNSW graph in maintenance_work_mem and falls back to a slow on-disk insert when it does not fit. Measured on
# random bit vectors, 100k points (pgvector 0.8.6): the graph needs 843 B per point in memory at 1024 bits (the index is 435 B per
# point), 761 B at 256 bits and 1,137 B at 3072 bits; so memory per point is about 730 B + bits / 8 (within 2 %). A build that fits ran in 38.6 s serial, 16.1 s with
# 2 workers and 9.6 s with 4; one that spilled (64 MB) took 69.0 s serial.
GRAPH_BYTES = 730
BUILD_HEADROOM = 1.15           # the graph grows with the rows loaded while the build runs
DEFAULT_BUILD_MB = 512         # the ceiling for one build; parallel builds need as much shared memory (the compose files give 1 GB)
DEFAULT_BUILD_WORKERS = 2
UNANALYSED_ROW_BYTES = 600     # beside the vector, when a leaf has no row estimate yet


@dataclass(frozen=True)
class BuildPlan:
    rows: int
    needed_mb: int          # what the graph needs to stay in memory
    memory_mb: int          # what the build is given: the need, within the ceiling and never below the server's default
    workers: int
    fits: bool              # False: the ceiling is below the need, so the build spills (slower, still correct)


def _env_int(name: str, default: int, minimum: int) -> int:
    """An integer setting from the environment; empty means the default, anything else that is not an integer of at least ``minimum``
    is an error naming the setting (ValueError)."""
    raw = os.environ.get(name, "")
    if raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = minimum - 1
    if value < minimum:
        raise ValueError(f"{name}={raw!r} must be an integer of at least {minimum}")
    return value


def check_build_settings() -> None:
    """Fail at the start of a run, naming the setting, instead of in the middle of an index build."""
    _env_int("VECTOR_BUILD_MEMORY_MB", DEFAULT_BUILD_MB, 1)
    _env_int("VECTOR_BUILD_WORKERS", DEFAULT_BUILD_WORKERS, 0)


def build_plan(rows: int, dim: int, version: tuple[int, ...], *, ceiling_mb: int | None = None, workers: int | None = None,
               floor_mb: int = 64) -> BuildPlan:
    """Memory and workers for one HNSW build of ``rows`` points of ``dim`` bits. Before pgvector 0.8.2 a parallel build can overflow,
    so it is serial there. ``VECTOR_BUILD_MEMORY_MB`` and ``VECTOR_BUILD_WORKERS`` set the ceiling and the workers (read at each
    call; a bad value is an error naming it); ``floor_mb`` is the least a build gets (the server's own ``maintenance_work_mem``)."""
    ceiling_mb = ceiling_mb if ceiling_mb is not None else _env_int("VECTOR_BUILD_MEMORY_MB", DEFAULT_BUILD_MB, 1)
    workers = workers if workers is not None else _env_int("VECTOR_BUILD_WORKERS", DEFAULT_BUILD_WORKERS, 0)
    need = -(-int(rows * (GRAPH_BYTES + dim / 8) * BUILD_HEADROOM) // 2**20)
    return BuildPlan(rows, need, max(floor_mb, min(need, ceiling_mb)), workers if version >= (0, 8, 2) else 0, need <= ceiling_mb)


def leaf_rows(owner: Connection, leaf: str, dim: int) -> int:
    """The rows of a leaf as the planner knows them (the owner cannot count them: row-level security applies to it)."""
    est, size = owner.exec_driver_sql("SELECT reltuples::bigint, pg_relation_size(oid) FROM pg_class WHERE oid = %s::regclass", (leaf,)).one()
    return int(est) if est >= 0 else int(size // (2 * dim + UNANALYSED_ROW_BYTES))


def _plan(owner: Connection, leaf: str, rows: int | None, spaces: list[tuple[int, int]]) -> BuildPlan:
    dim = max((d for _, d in spaces), default=0)
    floor = int(owner.exec_driver_sql("SELECT pg_size_bytes(current_setting('maintenance_work_mem')) / 1048576").scalar())
    return build_plan(leaf_rows(owner, leaf, dim) if rows is None else rows, dim, _caps(owner)[1], floor_mb=max(64, floor))


def set_cleanup_option(owner: Connection, leaf: str, lock_timeout: str | None = None) -> bool:
    """In HNSW safe mode, give a leaf that has no ``vacuum_index_cleanup`` setting the value ``off``. Idempotent, and a setting that is
    already there (ours, or an operator's) is never changed or reset, so a leaf that has one is not touched or locked; after a pgvector
    upgrade an operator removes the setting with ``ALTER TABLE ... RESET (vacuum_index_cleanup)``. With ``lock_timeout`` a leaf that cannot
    be locked in time raises instead of waiting (the caller holds the migrate lock). Returns whether the leaf was changed."""
    if not safe_mode(owner):
        return False
    have = owner.exec_driver_sql("SELECT coalesce(reloptions, '{}') FROM pg_class WHERE oid = %s::regclass", (leaf,)).scalar()
    if any(o.startswith("vacuum_index_cleanup=") for o in have):
        return False
    if lock_timeout:
        owner.exec_driver_sql(f"SET lock_timeout = '{lock_timeout}'")
    try:
        owner.exec_driver_sql(f"ALTER TABLE {leaf} SET (vacuum_index_cleanup = off)")
    finally:
        if lock_timeout:
            owner.exec_driver_sql("RESET lock_timeout")
    return True


def create_leaf_indexes(owner: Connection, leaf: str, rows: int = 0) -> None:
    """Inside the caller's transaction, on a leaf that is not attached yet: plain CREATE INDEX (instant on an empty leaf, waits for
    no other transaction's snapshot, and a kill leaves nothing). ``rows`` is how many rows were moved into it (sizes the build)."""
    spaces = _spaces(owner)
    specs = _index_specs(owner, leaf, spaces)
    if specs:
        plan = _plan(owner, leaf, rows, spaces)
        owner.exec_driver_sql(f"SET LOCAL maintenance_work_mem = '{plan.memory_mb}MB'")
        owner.exec_driver_sql(f"SET LOCAL max_parallel_maintenance_workers = {plan.workers}")
    for name, tail in specs:
        owner.exec_driver_sql(f"CREATE INDEX {name} {tail}")


def build_leaf_indexes(owner: Connection, leaf: str, spaces: list[tuple[int, int]] | None = None,
                       report: Callable[[str], None] | None = None) -> list[str]:
    """Outside a transaction, on a live leaf: the same indexes built concurrently and checked valid (idempotent), with the memory and
    workers :func:`build_plan` gives them. Returns the names."""
    spaces = _spaces(owner) if spaces is None else spaces
    specs = _index_specs(owner, leaf, spaces)
    if not specs:
        return []
    plan = _plan(owner, leaf, None, spaces)
    if report:
        report(f"{leaf}: ~{plan.rows} rows, graph needs ~{plan.needed_mb} MB, build gets {plan.memory_mb} MB and {plan.workers} worker(s)"
               + ("" if plan.fits else " (does not fit: the build spills to disk and is slower)"))
    owner.exec_driver_sql(f"SET maintenance_work_mem = '{plan.memory_mb}MB'")
    owner.exec_driver_sql(f"SET max_parallel_maintenance_workers = {plan.workers}")
    try:
        for name, tail in specs:
            ddl.ensure_index(owner, f"retrieval.{name}", f"CREATE INDEX CONCURRENTLY {name} {tail}")
    finally:
        owner.exec_driver_sql("RESET maintenance_work_mem")
        owner.exec_driver_sql("RESET max_parallel_maintenance_workers")
    return [n for n, _ in specs]


def leaves_missing_indexes(owner: Connection) -> list[str]:
    """The leaves of the points tree that lack an index of some registered embedding space. A leaf is indexed when it is made, for the spaces
    that exist then: the shared hash leaves, and any leaf made before its org's space was registered, need this later."""
    spaces = _spaces(owner)
    if not spaces:
        return []
    out = []
    for leaf in points_leaves(owner):
        names = [n for n, _ in _index_specs(owner, leaf, spaces)]
        have = owner.exec_driver_sql("SELECT count(*) FROM pg_indexes WHERE schemaname = 'retrieval' AND indexname = ANY(%s)", (names,)).scalar() if names else 0
        if names and have < len(names):
            out.append(leaf)
    return out
