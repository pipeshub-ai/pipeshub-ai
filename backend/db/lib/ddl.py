"""DDL helpers for revisions and the maintenance job. Each takes a SQLAlchemy Connection that is
OUTSIDE a transaction (autocommit): CREATE INDEX CONCURRENTLY cannot run inside one. In a revision of a database that is
upgraded and verified in one transaction (see env.py) ``autocommit_block`` is refused; the maintenance job uses these helpers. In a revision:

    with op.get_context().autocommit_block():
        ensure_index(op.get_bind(), "app.ix", "CREATE INDEX CONCURRENTLY ix ON app.t (org_id, k)")
"""
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import OperationalError


def refuse_autocommit() -> None:
    """Stands in for ``autocommit_block`` during an upgrade that is checked and rolled back as one transaction: leaving the
    block would commit the revisions applied so far before the checks have run."""
    raise RuntimeError("autocommit_block is not allowed in an upgrade that is verified as one transaction: it would commit "
                       "earlier revisions before the checks run. Build the index outside the upgrade, or in its own release.")


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")
_LOCK_NOT_AVAILABLE = "55P03"
POSTINGS = "retrieval.postings"
# index-only scans of postings need a current visibility map: vacuum after 2 % inserted rows, not 20 % (see retrieval_0006)
GRAPH_OPTIONS = "autovacuum_analyze_scale_factor = 0.02, autovacuum_analyze_threshold = 200, autovacuum_vacuum_scale_factor = 0.05"
POSTINGS_OPTIONS = "autovacuum_vacuum_insert_scale_factor = 0.02, autovacuum_vacuum_insert_threshold = 2000"


def _ident(name: str) -> str:
    if not _IDENT.match(name) or len(name.split(".")[-1]) > 63:
        raise ValueError(f"not a plain (optionally schema-qualified) lower-case identifier: {name!r}")
    return name


def _scalar(conn: Connection, sql: str, *params: object) -> object:
    return conn.exec_driver_sql(sql, params or None).scalar()


def _index_valid(conn: Connection, qname: str) -> bool | None:
    """True/False for an existing index, None when it does not exist."""
    return _scalar(conn, "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(%s)", qname)


def ensure_index(conn: Connection, qname: str, ddl: str) -> None:
    """Make ``qname`` exist and be valid. A failed or cancelled CREATE INDEX CONCURRENTLY leaves an
    INVALID index that ``IF NOT EXISTS`` would keep; this drops it and rebuilds."""
    _ident(qname)
    if _index_valid(conn, qname) is False:
        conn.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {qname}")
    if _index_valid(conn, qname) is None:
        conn.exec_driver_sql(ddl)
    if _index_valid(conn, qname) is not True:
        raise RuntimeError(f"index {qname} is not valid after the build")


def ensure_partitioned_index(conn: Connection, table: str, name: str, cols: str, *, unique: bool = False, where: str = "") -> None:
    """Index a partitioned table without blocking writers: ``ON ONLY`` the parent, a concurrent build
    per leaf, then ATTACH. Handles nested trees (an intermediate partitioned table gets its own
    ``ON ONLY`` index). ``where`` makes the index partial (a literal predicate written in the revision). Idempotent."""
    _ident(table), _ident(name)
    schema = table.split(".")[0] if "." in table else "public"
    _build(conn, table, schema, name, cols, "UNIQUE " if unique else "", f" WHERE {where}" if where else "")


def _build(conn: Connection, table: str, schema: str, idx: str, cols: str, u: str, w: str = "") -> None:
    children = [r[0] for r in conn.exec_driver_sql(
        "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = %s::regclass ORDER BY 1", (table,))]
    if _scalar(conn, "SELECT relkind = 'p' FROM pg_class WHERE oid = %s::regclass", table):
        conn.exec_driver_sql(f"CREATE {u}INDEX IF NOT EXISTS {idx} ON ONLY {table} ({cols}){w}")
        for child in children:
            child_idx = f"{child.split('.')[-1]}_{idx}"
            _build(conn, child, schema, child_idx, cols, u, w)
            attached = _scalar(conn, "SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = %s::regclass "
                               "AND inhparent = %s::regclass)", f"{schema}.{child_idx}", f"{schema}.{idx}")
            if not attached:
                conn.exec_driver_sql(f"ALTER INDEX {schema}.{idx} ATTACH PARTITION {schema}.{child_idx}")
    else:
        if conn.exec_driver_sql("SELECT pg_relation_size(%s::regclass) = 0", (table,)).scalar():
            # A leaf that never held a row (a new install): a plain build takes its lock for no time and, unlike CONCURRENTLY, does not wait
            # for every other session's snapshot (a second migration run waiting for the advisory lock holds one).
            conn.exec_driver_sql(f"CREATE {u}INDEX IF NOT EXISTS {idx} ON {table} ({cols}){w}")
        else:
            ensure_index(conn, f"{schema}.{idx}", f"CREATE {u}INDEX CONCURRENTLY {idx} ON {table} ({cols}){w}")


def _retry(conn: Connection, step: Callable[[], Any], attempts: int = 3, backoff: float = 0.5) -> None:
    """Run ``step`` under a 2 s lock_timeout; a lock timeout is retried with backoff."""
    conn.exec_driver_sql("SET lock_timeout = '2s'")
    try:
        for i in range(attempts):
            try:
                return step()
            except OperationalError as exc:
                if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE or i == attempts - 1:
                    raise
                time.sleep(backoff * 2**i)
    finally:
        conn.exec_driver_sql("RESET lock_timeout")


def add_partition(conn: Connection, root: str, leaf: str, bounds: str, check: str) -> None:
    """Add a partition without blocking readers or writers of ``root``:
    standalone leaf -> bounds CHECK -> kernel.apply_rls_partition(leaf, root) -> ATTACH -> drop CHECK.

    ``bounds`` is the text after ``FOR VALUES`` ("FROM ('2026-01-01') TO ('2026-02-01')") and ``check``
    a predicate equal to it ("created_at >= '2026-01-01' AND created_at < '2026-02-01'"), so ATTACH
    skips its validation scan. Resumes a half-made leaf. Needs the migrate credential."""
    _ident(root), _ident(leaf)
    if _scalar(conn, "SELECT to_regprocedure('kernel.apply_rls_partition(regclass,regclass,text)') IS NULL"):
        raise RuntimeError("kernel.apply_rls_partition does not exist; partitions need the org isolation kernel")
    schema, short = leaf.split(".") if "." in leaf else ("public", leaf)
    con = f"{short}_bounds"
    if _scalar(conn, "SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(%s))", leaf):
        return
    if _scalar(conn, "SELECT to_regclass(%s) IS NULL", leaf):
        _retry(conn, lambda: conn.exec_driver_sql(f"CREATE TABLE {leaf} (LIKE {root} INCLUDING ALL EXCLUDING IDENTITY)"))
    # Every step below is safe to repeat. A leaf that exists but is not attached was left by a run that stopped after CREATE TABLE
    # (a statement of its own): it has no row security yet and must get all of it before it can join the tree.
    if root == POSTINGS:
        conn.exec_driver_sql(f"ALTER TABLE {leaf} SET ({POSTINGS_OPTIONS})")
    if not _scalar(conn, "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND conname = %s)", leaf, con):
        conn.exec_driver_sql(f"ALTER TABLE {leaf} ADD CONSTRAINT {con} CHECK ({check})")
    _retry(conn, lambda: conn.exec_driver_sql("SELECT kernel.apply_rls_partition(%s::regclass, %s::regclass)", (leaf, root)))
    _retry(conn, lambda: conn.exec_driver_sql(f"ALTER TABLE {root} ATTACH PARTITION {leaf} FOR VALUES {bounds}"))
    conn.exec_driver_sql(f"ALTER TABLE {leaf} DROP CONSTRAINT IF EXISTS {con}")


def ensure_months(conn: Connection, root: str, ahead: int = 3, drop_before_months: int | None = None, today: date | None = None) -> tuple[list[str], list[str]]:
    """Month partitions of a RANGE(timestamptz) table: from last month to ``ahead`` months from now (leaf ``<root>_yYYYYmMM``, made with
    ``add_partition`` so it is secured before it is attached), and, when ``drop_before_months`` is set, drops every leaf whose month ended
    that many months ago. Returns (created, dropped). A row already in the DEFAULT partition for a month blocks that month's ATTACH:
    the error names it, and the rows are moved by an operator (the owner cannot read them: row security is forced)."""
    _ident(root)
    first = (today or datetime.now(timezone.utc).date()).replace(day=1)

    def month(n: int) -> date:
        y, m = divmod(first.year * 12 + first.month - 1 + n, 12)
        return date(y, m + 1, 1)

    made = []
    for n in range(-1, ahead + 1):
        lo, hi = month(n), month(n + 1)
        leaf = f"{root}_y{lo:%Y}m{lo:%m}"
        if _scalar(conn, "SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(%s))", leaf):
            continue
        add_partition(conn, root, leaf, f"FROM ('{lo}') TO ('{hi}')", f"created_at >= '{lo}' AND created_at < '{hi}'")
        made.append(leaf)
    dropped = []
    if drop_before_months is not None:
        cutoff = month(-drop_before_months)
        for (leaf,) in conn.exec_driver_sql(
                "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE i.inhparent = %s::regclass "
                "AND c.relname ~ '_y[0-9]{4}m[0-9]{2}$'", (root,)).all():
            ym = re.search(r"_y(\d{4})m(\d{2})$", leaf)
            if date(int(ym[1]), int(ym[2]), 1) < cutoff:
                conn.exec_driver_sql(f"DROP TABLE {leaf}")
                dropped.append(leaf)
    return made, dropped
