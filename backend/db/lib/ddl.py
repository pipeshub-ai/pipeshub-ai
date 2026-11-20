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

import psycopg
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
# index-only scans of postings need a current visibility map: vacuum after 2 % inserted rows, not 20 % (see retrieval_0001)
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


def ensure_partitioned_index(conn: Connection, table: str, name: str, cols: str, *, unique: bool = False, where: str = "", blocking: bool = False, using: str = "") -> None:
    """Index a partitioned table without blocking writers: ``ON ONLY`` the parent, a concurrent build
    per leaf, then ATTACH. Handles nested trees (an intermediate partitioned table gets its own
    ``ON ONLY`` index). ``where`` makes the index partial (a literal predicate written in the revision). ``blocking`` builds each leaf with a
    plain CREATE INDEX instead (writers wait for the build): for a revision of an upgrade that runs as one transaction, where a concurrent build is refused.
    ``using`` names the access method (``gin``); default btree. Idempotent: a valid index of that name is left alone without a statement on the
    table (``CREATE INDEX IF NOT EXISTS`` takes the table's lock before it finds the name, and in a revision that lock lasts to the commit)."""
    _ident(table), _ident(name)
    schema = table.split(".")[0] if "." in table else "public"
    _ident(using or "btree")
    if _index_valid(conn, f"{schema}.{name}"):
        return
    _build(conn, table, schema, name, cols, "UNIQUE " if unique else "", f" WHERE {where}" if where else "", blocking=blocking, using=f"USING {using} " if using else "")


def _build(conn: Connection, table: str, schema: str, idx: str, cols: str, u: str, w: str = "", *, blocking: bool = False, using: str = "") -> None:
    def run(sql: str) -> None:
        # Online, a statement that takes the table's lock (SHARE for a plain build, which writers wait behind) gives up after 2 s and retries, so it
        # never queues writers behind a long transaction; in a revision the session's own lock_timeout applies (a retry cannot run in a failed transaction).
        if blocking:
            conn.exec_driver_sql(sql)
        else:
            _retry(conn, lambda: conn.exec_driver_sql(sql))
    children = [r[0] for r in conn.exec_driver_sql(
        "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = %s::regclass ORDER BY 1", (table,))]
    if _scalar(conn, "SELECT relkind = 'p' FROM pg_class WHERE oid = %s::regclass", table):
        run(f"CREATE {u}INDEX IF NOT EXISTS {idx} ON ONLY {table} {using}({cols}){w}")
        for child in children:
            child_idx = f"{child.split('.')[-1]}_{idx}"
            _build(conn, child, schema, child_idx, cols, u, w, blocking=blocking, using=using)
            attached = _scalar(conn, "SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = %s::regclass "
                               "AND inhparent = %s::regclass)", f"{schema}.{child_idx}", f"{schema}.{idx}")
            if not attached:
                run(f"ALTER INDEX {schema}.{idx} ATTACH PARTITION {schema}.{child_idx}")
    else:
        plain = f"CREATE {u}INDEX IF NOT EXISTS {idx} ON {table} {using}({cols}){w}"
        if blocking:
            conn.exec_driver_sql(plain)
        elif not (conn.exec_driver_sql("SELECT pg_relation_size(%s::regclass) = 0", (table,)).scalar() and _retry(conn, lambda: _plain_if_empty(conn, table, plain))):
            ensure_index(conn, f"{schema}.{idx}", f"CREATE {u}INDEX CONCURRENTLY {idx} ON {table} {using}({cols}){w}")


def _plain_if_empty(conn: Connection, table: str, plain: str) -> bool:
    """A leaf that never held a row (a new organisation's): a plain build is instant and, unlike CONCURRENTLY, does not wait for every other
    session's snapshot. It is made under the leaf's SHARE lock, taken before the leaf is looked at again, so no writer can fill it in between and
    have the build read its rows with every writer waiting. False when the leaf holds rows by then (the caller builds it CONCURRENTLY). Inside a
    revision's transaction (a new install's baseline) the lock lasts to that commit and no transaction of its own is opened."""
    own = conn.connection.driver_connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    if own:
        conn.exec_driver_sql("BEGIN")
    try:
        conn.exec_driver_sql(f"LOCK TABLE {table} IN SHARE MODE")
        empty = conn.exec_driver_sql("SELECT pg_relation_size(%s::regclass) = 0", (table,)).scalar()
        if empty:
            conn.exec_driver_sql(plain)
        if own:
            conn.exec_driver_sql("COMMIT")
    except BaseException:
        if own:
            conn.exec_driver_sql("ROLLBACK")
        raise
    return bool(empty)


def _retry[T](conn: Connection, step: Callable[[], T], attempts: int = 3, backoff: float = 0.5) -> T:
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
