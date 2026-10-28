"""
PostgreSQL DataSource - Database metadata and query operations

Provides async wrapper methods for PostgreSQL operations:
- Database and schema operations
- Table and view metadata
- Column information
- Foreign key relationships
- Index information
"""

import asyncio
import logging
import re
import time
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from app.sources.client.postgres.postgres import PostgreSQLClient, PostgreSQLResponse

logger = logging.getLogger(__name__)


DEFAULT_TABLE_ROW_FETCH_LIMIT = 1000

# Constraints are read from pg_constraint rather than information_schema: the
# information_schema views hide constraints from a user who can only SELECT the
# table, and they join a constraint to its columns by name, which mixes up
# same-named constraints and cross-multiplies multi-column foreign keys.
_CONSTRAINTS_OF_TABLE = """
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
"""


# One row per column pair, in key order: unnest() walks conkey and confkey in step.
_FOREIGN_KEYS_QUERY = """
    SELECT
        con.conname AS constraint_name,
        a.attname AS column_name,
        fn.nspname AS foreign_table_schema,
        fc.relname AS foreign_table_name,
        fa.attname AS foreign_column_name
""" + _CONSTRAINTS_OF_TABLE + """
    JOIN pg_class fc ON fc.oid = con.confrelid
    JOIN pg_namespace fn ON fn.oid = fc.relnamespace
    CROSS JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(attnum, fattnum, ord)
    JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
    JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = k.fattnum
    WHERE con.contype = 'f'
      AND n.nspname = $1
      AND c.relname = $2{top_level}
    ORDER BY con.conname, k.ord;
"""

# A foreign key to a partitioned table is cloned once per partition
# (pg_constraint.conparentid set, PostgreSQL 11 on); only the declared one counts.
_TOP_LEVEL_CONSTRAINTS = " AND con.conparentid = 0"


def quote_ident(name: str) -> str:
    """Quote an identifier for SQL, doubling any embedded double quote."""
    return '"' + name.replace('"', '""') + '"'


# Bookkeeping columns fetch_table_rows adds and strips again.
_ROW_NUMBER = "_pipeshub_row_number"
_RUNNING_BYTES = "_pipeshub_running_bytes"
_ROW_ALIAS = "_pipeshub_row"


def _order_list(alias: str, columns: list[str]) -> str:
    return ", ".join(f"{alias}.{quote_ident(col)}" for col in columns)


def _sample_query(
    schema_name: str,
    table_name: str,
    limit: int | None,
    order_by: list[str] | None,
    *,
    by_row_text: bool = False,
) -> str:
    """The row sample: the first ``limit`` rows, in ``order_by`` order when given.

    ``by_row_text`` orders a table without keys by each row's text form. That
    works for every column type, and rows that tie are identical, so the
    sample is the same on every read of unchanged data.
    """
    row_limit = int(limit if limit is not None else DEFAULT_TABLE_ROW_FETCH_LIMIT)
    query = f"SELECT * FROM {quote_ident(schema_name)}.{quote_ident(table_name)}"
    if order_by:
        query += " ORDER BY " + ", ".join(quote_ident(col) for col in order_by)
    elif by_row_text:
        query += f" AS {_ROW_ALIAS} ORDER BY {_ROW_ALIAS}::text"
    return query + f" LIMIT {row_limit}"


def _byte_capped_query(sample: str, max_bytes: int, row_order: str) -> str:
    """``sample``'s rows, in ``row_order``, until their text form reaches ``max_bytes``.

    ``row_order`` must repeat the sample's own order: row_number() is not
    bound to the order of the subquery it reads.
    """
    over = f"ORDER BY {row_order}" if row_order else ""
    return f"""
        SELECT * FROM (
            SELECT numbered.*,
                   sum(octet_length(numbered::text)) OVER (ORDER BY {_ROW_NUMBER}) AS {_RUNNING_BYTES}
            FROM (SELECT sampled.*, row_number() OVER ({over}) AS {_ROW_NUMBER} FROM ({sample}) sampled) numbered
        ) sized
        WHERE {_RUNNING_BYTES} <= {int(max_bytes)}
    """


def build_table_column_stats_sql(
    schema: str,
    table: str,
    column_names: list[str],
) -> str:
    """One heap scan: row count + COUNT(DISTINCT) + null counts per column."""
    tbl = f"{quote_ident(schema)}.{quote_ident(table)}"
    parts = ["COUNT(*) AS total_rows"]
    # Positional aliases: a name-based alias longer than PostgreSQL's 63-byte
    # identifier limit is truncated, so long names lost their stats or collided.
    for pos, name in enumerate(column_names):
        if not name:
            continue
        col = quote_ident(name)
        parts.append(f"COUNT(DISTINCT {col}) AS d{pos}")
        parts.append(f"SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS n{pos}")
    return f"SELECT {', '.join(parts)} FROM {tbl}"


def _first_present(row: dict[str, Any], *keys: str) -> Any:  # noqa: ANN401 - a row value of any column type
    for key in keys:
        if key in row:
            return row[key]
    return None


def parse_table_column_stats_row(
    stats_row: dict[str, Any],
    column_names: list[str],
    *,
    fallback_total_rows: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """Parse a row from :func:`build_table_column_stats_sql`."""
    total_raw = stats_row.get("TOTAL_ROWS") or stats_row.get("total_rows") or 0
    try:
        total_rows = int(total_raw)
    except (TypeError, ValueError):
        total_rows = fallback_total_rows

    column_stats: list[dict[str, Any]] = []
    for pos, name in enumerate(column_names):
        if not name:
            continue
        safe = name.replace('"', "")
        distinct_raw = _first_present(
            stats_row, f"d{pos}", f"D{pos}", f"DISTINCT_{safe.upper()}", f"distinct_{safe}",
        )
        null_raw = _first_present(
            stats_row, f"n{pos}", f"N{pos}", f"NULLS_{safe.upper()}", f"nulls_{safe}",
        )
        try:
            distinct_count = int(distinct_raw) if distinct_raw is not None else None
        except (TypeError, ValueError):
            distinct_count = None
        try:
            null_count = int(null_raw) if null_raw is not None else None
        except (TypeError, ValueError):
            null_count = None
        ratio = None
        if distinct_count is not None and total_rows > 0:
            ratio = distinct_count / total_rows
        column_stats.append({
            "name": name,
            "ordinal_position": pos,
            "distinct_count": distinct_count,
            "null_count": null_count,
            "cardinality_ratio": ratio,
        })
    return total_rows, column_stats


# ---------------------------------------------------------------------------
# Pydantic models for structured data returned by PostgreSQL queries
# All use extra='allow' so unexpected columns from the DB are preserved.
# ---------------------------------------------------------------------------

class DatabaseInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    encoding: str | None = None
    collation: str | None = None
    size: str | None = None


class SchemaInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    owner: str | None = None


class TableListEntry(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    type: str | None = None


class ColumnInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    data_type: str | None = None
    udt_name: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    datetime_precision: int | None = None
    nullable: bool = False
    default: str | None = None
    is_primary_key: bool = False
    is_unique: bool = False
    is_part_of_composite_primary_key: bool = False
    is_part_of_composite_unique: bool = False


class CheckConstraintInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    check_clause: str = ""


class TableDetail(BaseModel):
    """Result of get_table_info: table metadata + columns + constraints."""
    model_config = ConfigDict(extra='allow')
    name: str = ""
    type: str | None = None
    columns: list[ColumnInfo] = []
    primary_keys: list[str] = []
    check_constraints: list[CheckConstraintInfo] = []


class ViewInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    definition: str | None = None


class ForeignKeyInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    column_name: str = ""
    foreign_table_schema: str = ""
    foreign_table_name: str = ""
    foreign_column_name: str = ""


class PrimaryKeyInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    column_name: str = ""


class DDLResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    ddl: str = ""


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    version: str | None = None
    database: str | None = None
    user: str | None = None


class TableStats(BaseModel):
    model_config = ConfigDict(extra='allow')
    schema_name: str = ""
    table_name: str = ""
    # pg_class.relkind: 'r' ordinary table, 'p' partitioned table
    kind: str = "r"
    n_live_tup: int = 0
    n_tup_ins: int = 0
    n_tup_upd: int = 0
    n_tup_del: int = 0
    # ANALYZE runs, manual and automatic: a change means fresher pg_stats.
    analyze_count: int = 0
    # pg_class.oid: unchanged by a rename or a move to another schema.
    oid: int = 0
    # pg_class.relfilenode, or a digest of the partitions' for a partitioned
    # table: changes when the rows are rewritten wholesale, as TRUNCATE does.
    storage_id: str = ""


# Internal models used only by the DDL builder
class _DDLColumnDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    column_name: str = ""
    quoted_name: str = ""
    qualified_table: str = ""
    data_type: str = ""
    not_null: bool = False
    default_value: str | None = None
    ordinal: int = 0


class _DDLConstraintDef(BaseModel):
    model_config = ConfigDict(extra='allow')
    constraint_name: str = ""
    quoted_name: str = ""
    definition: str = ""


class _TableKeyRow(TypedDict):
    key_type: Literal["primary", "unique"]
    key_width: int
    column_names: list[str]


_TABLE_KEYS_QUERY = """
    WITH target AS (
        SELECT c.oid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p')
    ),
    constraint_keys AS (
        SELECT
            CASE con.contype WHEN 'p' THEN 'primary' ELSE 'unique' END AS key_type,
            cardinality(con.conkey) AS key_width,
            array_agg(a.attname ORDER BY ord.k) AS column_names
        FROM pg_constraint con
        JOIN target t ON con.conrelid = t.oid
        JOIN pg_index i ON i.indexrelid = con.conindid
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ord(attnum, k)
        JOIN pg_attribute a
          ON a.attrelid = con.conrelid
         AND a.attnum = ord.attnum
         AND NOT a.attisdropped
        WHERE con.contype IN ('p', 'u')
          AND pg_get_expr(i.indpred, i.indrelid) IS NULL
        GROUP BY con.oid, con.contype
    ),
    index_only_keys AS (
        SELECT
            'unique' AS key_type,
            -- indkey lists the INCLUDE columns after the key ones; only the
            -- first indnkeyatts are the key (PostgreSQL 11, as conparentid).
            i.indnkeyatts AS key_width,
            array_agg(a.attname ORDER BY ord.k) AS column_names
        FROM pg_index i
        JOIN target t ON i.indrelid = t.oid
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS ord(attnum, k)
        JOIN pg_attribute a
          ON a.attrelid = i.indrelid
         AND a.attnum = ord.attnum
         AND NOT a.attisdropped
        WHERE i.indisunique
          AND ord.k <= i.indnkeyatts
          AND pg_get_expr(i.indpred, i.indrelid) IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM pg_constraint con
              WHERE con.conindid = i.indexrelid
                AND con.contype IN ('p', 'u')
          )
        GROUP BY i.indexrelid, i.indnkeyatts
    )
    SELECT key_type, key_width, column_names FROM constraint_keys
    UNION ALL
    SELECT key_type, key_width, column_names FROM index_only_keys;
"""


def _empty_column_key_flags() -> dict[str, bool]:
    return {
        "is_primary_key": False,
        "is_unique": False,
        "is_part_of_composite_primary_key": False,
        "is_part_of_composite_unique": False,
    }


def apply_key_metadata_to_columns(
    columns: list[ColumnInfo],
    keys: list[_TableKeyRow],
) -> None:
    """Set PK/unique flags on columns from pg_catalog key definitions."""
    flags: dict[str, dict[str, bool]] = {
        col.name: _empty_column_key_flags() for col in columns if col.name
    }

    for key in keys:
        cols = key.get("column_names") or []
        width = key.get("key_width") or 0
        if not cols or len(cols) != width:
            continue

        composite = len(cols) >= 2
        key_type = key.get("key_type")

        if key_type == "primary":
            for name in cols:
                if name not in flags:
                    continue
                flags[name]["is_primary_key"] = True
                if composite:
                    flags[name]["is_part_of_composite_primary_key"] = True
                else:
                    flags[name]["is_unique"] = True
        elif key_type == "unique":
            if composite:
                for name in cols:
                    if name in flags:
                        flags[name]["is_part_of_composite_unique"] = True
            elif cols[0] in flags:
                flags[cols[0]]["is_unique"] = True

    for col in columns:
        if not col.name:
            continue
        col_flags = flags.get(col.name, _empty_column_key_flags())
        col.is_primary_key = col_flags["is_primary_key"]
        col.is_unique = col_flags["is_unique"]
        col.is_part_of_composite_primary_key = col_flags["is_part_of_composite_primary_key"]
        col.is_part_of_composite_unique = col_flags["is_part_of_composite_unique"]


def primary_key_columns_from_key_defs(keys: list[_TableKeyRow]) -> list[str]:
    """Return ordered PK column names from pg_catalog key definitions."""
    for key in keys:
        if key.get("key_type") != "primary":
            continue
        cols = key.get("column_names") or []
        width = key.get("key_width") or 0
        if cols and len(cols) == width:
            return list(cols)
    return []


TOP_VALUES_LIMIT = 20
# Longer values are dropped, not cut: a stored value can be used verbatim as
# an equality literal, and a cut one matches nothing.
TOP_VALUE_MAX_CHARS = 80
# Columns without usable pg_stats get their top values from a GROUP BY scan
# each; past these limits they get none.
TOP_VALUES_SCAN_MAX_ROWS = 1_000_000
TOP_VALUES_MAX_SCANS_PER_TABLE = 10
# MIN/MAX of every numeric and date/time column in one unsorted scan.
MIN_MAX_SCAN_MAX_CELLS = 50_000_000

# Top values are copied into the graph and into prompts, so only columns that
# look like a vocabulary qualify: few distinct values, each repeated. The
# ratio is over non-null rows, or a mostly-null column of unique emails passes.
_SMALL_VOCABULARY = (50, 0.5)
_LARGE_VOCABULARY = (1000, 0.2)
# Second line of defence for columns that hold personal data yet pass the
# vocabulary test, matched against the column name with non-alphanumerics as _.
# [flm]name catches abbreviations like admfname2 (and the odd false positive).
_PERSONAL_COLUMN_RE = re.compile(
    r"e_?mail|phone|mobile|(?:^|_)fax(?:_|$)|(?:^|_)ssn(?:_|$)|social_?security|passw(?:or)?d"
    r"|(?:first|last|middle|full|display|user|given|family|sur|fore|nick|maiden)_?name|[flm]name"
    r"|(?:customer|contact|client|employee|patient|person|member|owner|author|recipient"
    r"|sender|manager|assignee|reporter)_?name"
    r"|(?:^|_)(?:owner|author|assignee|reporter|assigned_to|created_by|updated_by|modified_by)(?:_|$)"
    r"|birth|(?:^|_)dob(?:_|$)"
    r"|login|address|street|(?:^|_)ip(?:_|$)|ip_?addr"
    r"|passport|national_?id|tax_?id|(?:^|_)iban(?:_|$)|(?:credit_?)?card_?(?:no|num|number)(?:_|$)"
    r"|salary|(?:^|_)wage"
)
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}|\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")
# A column is free text when most of its common values are long or wordy.
_FREE_TEXT_CHARS = 40
_FREE_TEXT_WORDS = 5

_NUMERIC_TYPES = frozenset({"smallint", "integer", "bigint", "numeric", "real", "double precision"})
_TEMPORAL_TYPES = frozenset({
    "date",
    "timestamp without time zone",
    "timestamp with time zone",
    "time without time zone",
    "time with time zone",
})
_TEXT_TYPES = frozenset({"text", "character varying", "character", "name"})
_TEXT_UDTS = frozenset({"citext"})
# Binary, structured or geometric values are not a vocabulary, and a box
# array separates its elements with ';', which the array parser doesn't read.
_NO_TOP_VALUE_TYPES = frozenset({
    "bytea", "ARRAY", "json", "jsonb", "xml", "tsvector", "tsquery",
    "point", "line", "lseg", "box", "path", "polygon", "circle",
    # Network addresses identify machines and, through them, people.
    "inet", "cidr", "macaddr", "macaddr8",
})

_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

# Profiling reads the source's tables during sync: a scan must not wait
# behind a DDL lock (and so block the application queued after it), run
# unbounded, or render dates by the session's locale.
PROFILING_QUERY_SETTINGS = {
    "statement_timeout": "30s",
    "lock_timeout": "2s",
    "DateStyle": "ISO, MDY",
    "TimeZone": "UTC",
}

# A partitioned table or inheritance parent has its stats for the whole tree
# under inherited = true, which is what a SELECT from it reads.
_COLUMN_STATISTICS_QUERY = """
    SELECT
        c.reltuples::float8 AS reltuples,
        s.attname AS column_name,
        s.null_frac::float8 AS null_frac,
        s.n_distinct::float8 AS n_distinct,
        s.most_common_vals::text AS most_common_vals,
        s.most_common_freqs::float8[] AS most_common_freqs,
        s.histogram_bounds::text AS histogram_bounds
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN LATERAL (
        SELECT DISTINCT ON (st.attname)
            st.attname, st.null_frac, st.n_distinct,
            st.most_common_vals, st.most_common_freqs, st.histogram_bounds
        FROM pg_stats st
        WHERE st.schemaname = n.nspname AND st.tablename = c.relname
        ORDER BY st.attname, st.inherited DESC
    ) s ON true
    WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p');
"""


class PgColumnStatistics(BaseModel):
    """A column's pg_stats row, with its arrays parsed."""
    model_config = ConfigDict(extra='allow')
    column_name: str = ""
    null_frac: float | None = None
    n_distinct: float | None = None
    most_common_vals: list[str | None] | None = None
    most_common_freqs: list[float] | None = None
    histogram_bounds: list[str | None] | None = None


class TableColumnStatistics(BaseModel):
    model_config = ConfigDict(extra='allow')
    # pg_class.reltuples: -1 (or 0 before PostgreSQL 14) until first analyzed.
    reltuples: float | None = None
    columns: dict[str, PgColumnStatistics] = {}


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra='allow')
    top_values: list[str] | None = None
    top_value_frequencies: list[float] | None = None
    min_value: str | None = None
    max_value: str | None = None
    numeric_text: bool | None = None
    profiled_at: int | None = None


def parse_pg_array_literal(literal: str | None) -> list[str | None] | None:
    """Elements of a one-dimensional array in PostgreSQL's text form.

    ``{a,"b,c","say \\"hi\\"",NULL}`` gives ``["a", "b,c", 'say "hi"', None]``;
    a quoted ``"NULL"`` is the string. Returns None for anything else,
    multi-dimensional arrays included.
    """
    if literal is None:
        return None
    text = literal.strip()
    if text.startswith("["):
        # Dimension decoration, e.g. [0:2]={...}
        _, _, text = text.partition("=")
        text = text.strip()
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return None
    body = text[1:-1]
    if not body.strip():
        return []

    values: list[str | None] = []
    i, end = 0, len(body)
    while True:
        while i < end and body[i].isspace():
            i += 1
        if i < end and body[i] == '"':
            chars: list[str] = []
            i += 1
            while i < end and body[i] != '"':
                if body[i] == "\\":
                    i += 1
                    if i >= end:
                        return None
                chars.append(body[i])
                i += 1
            if i >= end:
                return None
            i += 1
            values.append("".join(chars))
            while i < end and body[i].isspace():
                i += 1
        else:
            start = i
            while i < end and body[i] != ",":
                if body[i] in '{}"\\':
                    return None
                i += 1
            token = body[start:i].strip()
            if not token:
                return None
            values.append(None if token.upper() == "NULL" else token)
        if i >= end:
            return values
        if body[i] != ",":
            return None
        i += 1


# No equality operator: COUNT(DISTINCT) on one fails the whole stats query.
_NO_EQUALITY_UDTS = frozenset({"json", "xml", "point", "line", "lseg", "box", "path", "polygon", "circle"})


def countable_column_names(columns: list[ColumnInfo]) -> list[str]:
    """Names of the columns :func:`build_table_column_stats_sql` can count."""
    return [
        c.name for c in columns
        if c.name and (c.udt_name or "").lstrip("_") not in _NO_EQUALITY_UDTS
    ]


def _column_kind(column: ColumnInfo) -> str | None:
    data_type = column.data_type or ""
    if data_type in _NUMERIC_TYPES:
        return "numeric"
    if data_type in _TEMPORAL_TYPES:
        return "temporal"
    if data_type in _TEXT_TYPES or (column.udt_name or "") in _TEXT_UDTS:
        return "text"
    return None


def estimate_distinct_and_ratio(
    exact: dict[str, Any] | None,
    pg: PgColumnStatistics | None,
    row_count: float | None,
) -> tuple[float | None, float | None]:
    """Distinct count, and distinct values per non-null row.

    Exact when the sync counted them, ``row_count`` then being the counted
    total; else from pg_stats, where a negative n_distinct is a fraction of
    all rows and null_frac the share of nulls.
    """
    if row_count is None or row_count <= 0:
        return None, None
    if exact and exact.get("distinct_count") is not None and exact.get("null_count") is not None:
        distinct = float(exact["distinct_count"])
        non_null = row_count - float(exact["null_count"])
    elif pg is not None and pg.n_distinct:
        distinct = pg.n_distinct if pg.n_distinct > 0 else -pg.n_distinct * row_count
        non_null = row_count * (1.0 - (pg.null_frac or 0.0))
    else:
        return None, None
    if non_null <= 0:
        return distinct, None
    return distinct, distinct / non_null


def looks_personal(column_name: str) -> bool:
    normalized = re.sub(r"[^0-9a-z]+", "_", column_name.lower())
    return bool(_PERSONAL_COLUMN_RE.search(normalized))


def top_values_eligible(
    column: ColumnInfo,
    distinct_count: float | None,
    non_null_ratio: float | None,
) -> bool:
    if column.is_unique or (column.is_primary_key and not column.is_part_of_composite_primary_key):
        return False
    if (column.data_type or "") in _NO_TOP_VALUE_TYPES or looks_personal(column.name):
        return False
    if distinct_count is None or non_null_ratio is None or distinct_count <= 0:
        return False
    max_small, ratio_small = _SMALL_VOCABULARY
    max_large, ratio_large = _LARGE_VOCABULARY
    return (
        (distinct_count <= max_small and non_null_ratio <= ratio_small)
        or (distinct_count <= max_large and non_null_ratio <= ratio_large)
    )


def _frequency(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def top_values_from_pg_stats(pg: PgColumnStatistics) -> tuple[list[str], list[float]] | None:
    """Every most common value and its share of non-null rows, unscreened.

    pg_stats gives each value's share of all rows, nulls included.
    """
    values, freqs = pg.most_common_vals, pg.most_common_freqs
    if not values or not freqs or len(values) != len(freqs):
        return None
    non_null = 1.0 - (pg.null_frac or 0.0)
    if non_null <= 0:
        return None
    pairs = [(v, _frequency(f / non_null)) for v, f in zip(values, freqs) if v is not None and f is not None]
    if not pairs:
        return None
    return [v for v, _ in pairs], [s for _, s in pairs]


def top_values_from_counts(rows: list[dict[str, Any]]) -> tuple[list[str], list[float]] | None:
    """Values from rows of ``value``, ``n`` and ``total`` (all non-null rows), unscreened."""
    top: list[str] = []
    shares: list[float] = []
    for row in rows:
        value, count, total = row.get("value"), row.get("n"), row.get("total")
        if value is None or not count or not total:
            continue
        top.append(str(value))
        shares.append(_frequency(float(count) / float(total)))
    return (top, shares) if top else None


def looks_like_contact_value(value: str) -> bool:
    """An e-mail address or phone number."""
    return bool(_EMAIL_RE.search(value) or _PHONE_RE.fullmatch(value.strip()))


def screen_top_values(values: list[str], shares: list[float]) -> tuple[list[str], list[float]] | None:
    """The values fit to store: none for a column of free text or contact
    details, and none longer than ``TOP_VALUE_MAX_CHARS``."""
    wordy = sum(1 for v in values if len(v) > _FREE_TEXT_CHARS or len(v.split()) > _FREE_TEXT_WORDS)
    if wordy * 2 > len(values):
        return None
    if any(looks_like_contact_value(v) for v in values):
        return None
    kept = [(v, s) for v, s in zip(values, shares) if len(v) <= TOP_VALUE_MAX_CHARS][:TOP_VALUES_LIMIT]
    if not kept:
        return None
    return [v for v, _ in kept], [s for _, s in kept]


def detect_numeric_text(column: ColumnInfo, observed: list[str | None]) -> bool | None:
    """Whether every observed value of a text column is a number."""
    if _column_kind(column) != "text":
        return None
    values = [v.strip() for v in observed if v is not None]
    if not values:
        return None
    # A leading zero (ZIP 02134, code 007) means the text is an identifier:
    # casting it to a number would drop the zero and break equality filters.
    if any(len(v) > 1 and v[0] == "0" and v[1].isdigit() for v in values):
        return False
    return all(_NUMBER_RE.fullmatch(v) for v in values)


def _set_top_values(profile: ColumnProfile, candidates: tuple[list[str], list[float]]) -> None:
    screened = screen_top_values(*candidates)
    if screened is not None:
        profile.top_values, profile.top_value_frequencies = screened


def _column_statistics_from_rows(rows: list[dict[str, Any]]) -> TableColumnStatistics:
    statistics = TableColumnStatistics()
    for row in rows:
        if statistics.reltuples is None and row.get("reltuples") is not None:
            statistics.reltuples = float(row["reltuples"])
        name = row.get("column_name")
        if not name:
            continue
        statistics.columns[name] = PgColumnStatistics(
            column_name=name,
            null_frac=row.get("null_frac"),
            n_distinct=row.get("n_distinct"),
            most_common_vals=parse_pg_array_literal(row.get("most_common_vals")),
            most_common_freqs=list(row["most_common_freqs"]) if row.get("most_common_freqs") else None,
            histogram_bounds=parse_pg_array_literal(row.get("histogram_bounds")),
        )
    return statistics


class PostgreSQLDataSource:
    """PostgreSQL DataSource for database operations.
    
    Provides methods for fetching metadata and executing queries against PostgreSQL.
    """
    
    def __init__(self, client: PostgreSQLClient) -> None:
        """Initialize with PostgreSQL client.
        
        Args:
            client: PostgreSQLClient instance with configured authentication
        """
        logger.debug("🔧 [PostgreSQLDataSource] __init__ called")
        self._client = client
        self._has_conparentid: bool | None = None
        logger.info("🔧 [PostgreSQLDataSource] Initialized successfully")

    async def _top_level_constraints(self) -> str:
        """The filter that leaves out per-partition clones, where the server has them."""
        if self._has_conparentid is None:
            rows = await self._client.execute_query("SHOW server_version_num")
            self._has_conparentid = int(rows[0]["server_version_num"]) >= 110000
        return _TOP_LEVEL_CONSTRAINTS if self._has_conparentid else ""
    
    def get_data_source(self) -> "PostgreSQLDataSource":
        """Return the data source instance."""
        return self
    
    def get_client(self) -> PostgreSQLClient:
        """Return the underlying PostgreSQL client."""
        return self._client

    async def _fetch_table_key_definitions(self, schema: str, table: str) -> list[_TableKeyRow]:
        rows = await self._client.execute_query(_TABLE_KEYS_QUERY, (schema, table))
        keys: list[_TableKeyRow] = []
        for row in rows:
            column_names = row.get("column_names") or []
            if isinstance(column_names, str):
                column_names = [column_names]
            keys.append({
                "key_type": row.get("key_type"),
                "key_width": int(row.get("key_width") or 0),
                "column_names": list(column_names),
            })
        return keys
    
    async def list_databases(self) -> PostgreSQLResponse:
        """List all accessible databases.
        
        Returns:
            PostgreSQLResponse with list of databases
        """
        logger.debug("🔧 [PostgreSQLDataSource] list_databases called")
        
        query = """
            SELECT datname as name,
                   pg_encoding_to_char(encoding) as encoding,
                   datcollate as collation,
                   pg_size_pretty(pg_database_size(datname)) as size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname;
        """
        
        try:
            results = await self._client.execute_query(query)
            databases = [DatabaseInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(databases)} databases")
            
            return PostgreSQLResponse(
                success=True,
                data=[db.model_dump() for db in databases],
                message=f"Successfully listed {len(databases)} databases"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_databases failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list databases"
            )
    
    async def list_schemas(self, database: str | None = None) -> PostgreSQLResponse:
        """List all schemas in the current database.
        
        Args:
            database: Database name (not used, kept for API compatibility)
            
        Returns:
            PostgreSQLResponse with list of schemas
        """
        logger.debug("🔧 [PostgreSQLDataSource] list_schemas called")
        
        query = """
            SELECT schema_name as name,
                   schema_owner as owner
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND schema_name !~ '^pg_(toast_)?temp_'
            ORDER BY schema_name;
        """
        
        try:
            results = await self._client.execute_query(query)
            schemas = [SchemaInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(schemas)} schemas")
            
            return PostgreSQLResponse(
                success=True,
                data=[s.model_dump() for s in schemas],
                message=f"Successfully listed {len(schemas)} schemas"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_schemas failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list schemas"
            )
    
    async def list_tables(self, schema: str = "public") -> PostgreSQLResponse:
        """List all tables in a schema.
        
        Args:
            schema: Schema name (default: public)
            
        Returns:
            PostgreSQLResponse with list of tables
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] list_tables called for schema: {schema}")
        
        # A partition is part of its parent table, not a table of its own:
        # listed separately, each would be indexed and could be queried for a
        # partial answer. information_schema reports partitions as BASE TABLE.
        query = """
            SELECT
                t.table_name as name,
                t.table_schema as schema,
                t.table_type as type
            FROM information_schema.tables t
            JOIN pg_namespace n ON n.nspname = t.table_schema
            JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
            WHERE t.table_schema = $1
              AND t.table_type = 'BASE TABLE'
              AND NOT c.relispartition
            ORDER BY t.table_name;
        """
        
        try:
            results = await self._client.execute_query(query, (schema,))
            tables = [TableListEntry.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(tables)} tables")
            
            return PostgreSQLResponse(
                success=True,
                data=[t.model_dump() for t in tables],
                message=f"Successfully listed {len(tables)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_tables failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list tables"
            )
    
    async def get_table_info(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get detailed information about a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with table information including columns with
            complete type info (precision, scale, length), constraints, and defaults
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_table_info called for {schema}.{table}")
        
        # Get table metadata
        table_query = """
            SELECT
                table_name as name,
                table_schema as schema,
                table_type as type
            FROM information_schema.tables
            WHERE table_schema = $1 AND table_name = $2;
        """
        
        # Enhanced column information query with full type details
        # Note: is_identity columns only exist in PostgreSQL 10+, so we omit them for compatibility
        columns_query = """
            SELECT
                c.column_name as name,
                c.data_type,
                c.udt_name,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale,
                c.datetime_precision,
                CASE WHEN c.is_nullable = 'YES' THEN true ELSE false END as nullable,
                c.column_default as "default"
            FROM information_schema.columns c
            WHERE c.table_schema = $1 AND c.table_name = $2
            ORDER BY c.ordinal_position;
        """

        # CHECK constraints (NOT NULL is not one; it has its own contype)
        check_query = """
            SELECT
                con.conname AS constraint_name,
                pg_get_expr(con.conbin, con.conrelid) AS check_clause
        """ + _CONSTRAINTS_OF_TABLE + """
            WHERE con.contype = 'c'
              AND n.nspname = $1
              AND c.relname = $2
            ORDER BY con.conname;
        """
        
        try:
            table_info_raw = await self._client.execute_query(table_query, (schema, table))
            if not table_info_raw:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )

            columns_raw, check_raw, key_defs = await asyncio.gather(
                self._client.execute_query(columns_query, (schema, table)),
                self._client.execute_query(check_query, (schema, table)),
                self._fetch_table_key_definitions(schema, table),
            )

            columns = [ColumnInfo.model_validate(row) for row in columns_raw]
            apply_key_metadata_to_columns(columns, key_defs)
            
            check_constraints = [CheckConstraintInfo.model_validate(row) for row in check_raw]
            
            table_detail = TableDetail.model_validate({
                **table_info_raw[0],
                'columns': columns,
                'primary_keys': primary_key_columns_from_key_defs(key_defs),
                'check_constraints': check_constraints,
            })
            
            logger.debug(f"🔧 [PostgreSQLDataSource] Table has {len(columns)} columns")
            
            return PostgreSQLResponse(
                success=True,
                data=table_detail.model_dump(),
                message=f"Successfully retrieved table info for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_info failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table info"
            )
    
    async def list_views(self, schema: str = "public") -> PostgreSQLResponse:
        """List all views in a schema.
        
        Args:
            schema: Schema name (default: public)
            
        Returns:
            PostgreSQLResponse with list of views
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] list_views called for schema: {schema}")
        
        query = """
            SELECT
                table_name as name,
                table_schema as schema,
                view_definition as definition
            FROM information_schema.views
            WHERE table_schema = $1
            ORDER BY table_name;
        """
        
        try:
            results = await self._client.execute_query(query, (schema,))
            views = [ViewInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(views)} views")
            
            return PostgreSQLResponse(
                success=True,
                data=[v.model_dump() for v in views],
                message=f"Successfully listed {len(views)} views"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] list_views failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to list views"
            )
    
    async def get_foreign_keys(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get foreign key relationships for a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with foreign key information
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_foreign_keys called for {schema}.{table}")
        
        try:
            query = _FOREIGN_KEYS_QUERY.format(top_level=await self._top_level_constraints())
            results = await self._client.execute_query(query, (schema, table))
            foreign_keys = [ForeignKeyInfo.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(foreign_keys)} foreign keys")
            
            return PostgreSQLResponse(
                success=True,
                data=[fk.model_dump() for fk in foreign_keys],
                message=f"Successfully retrieved foreign keys for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_foreign_keys failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get foreign keys"
            )
    
    async def get_primary_keys(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get primary key columns for a table.
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with primary key column names
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_primary_keys called for {schema}.{table}")
        

        try:
            key_defs = await self._fetch_table_key_definitions(schema, table)
            pk_columns = primary_key_columns_from_key_defs(key_defs)
            primary_keys = [PrimaryKeyInfo(column_name=name) for name in pk_columns]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found {len(primary_keys)} primary key columns")
            
            return PostgreSQLResponse(
                success=True,
                data=[pk.model_dump() for pk in primary_keys],
                message=f"Successfully retrieved primary keys for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_primary_keys failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get primary keys"
            )
    
    async def get_table_ddl(self, schema: str, table: str) -> PostgreSQLResponse:
        """Get the DDL (CREATE TABLE statement) for a table.
        
        Reconstructs the CREATE TABLE statement from system catalogs including:
        - Column definitions with full type info (length, precision, scale)
        - NOT NULL constraints
        - DEFAULT values
        - PRIMARY KEY constraints
        - UNIQUE constraints
        - FOREIGN KEY constraints
        - CHECK constraints
        
        Args:
            schema: Schema name
            table: Table name
            
        Returns:
            PostgreSQLResponse with complete DDL statement
        """
        logger.debug(f"🔧 [PostgreSQLDataSource] get_table_ddl called for {schema}.{table}")
        
        # Get column definitions with full type info
        columns_query = """
            SELECT
                a.attname as column_name,
                quote_ident(a.attname) as quoted_name,
                quote_ident(n.nspname) || '.' || quote_ident(c.relname) as qualified_table,
                format_type(a.atttypid, a.atttypmod) as data_type,
                a.attnotnull as not_null,
                CASE
                    WHEN a.atthasdef THEN pg_get_expr(d.adbin, d.adrelid)
                    ELSE NULL
                END as default_value,
                a.attnum as ordinal
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            LEFT JOIN pg_attrdef d ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE n.nspname = $1
              AND c.relname = $2
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum;
        """
        
        # Every PRIMARY KEY, UNIQUE, FOREIGN KEY and CHECK constraint, as
        # Postgres itself would write it.
        constraints_query = """
            SELECT
                con.conname AS constraint_name,
                quote_ident(con.conname) AS quoted_name,
                pg_get_constraintdef(con.oid) AS definition
        """ + _CONSTRAINTS_OF_TABLE + """
            WHERE con.contype IN ('p', 'u', 'f', 'c')
              AND n.nspname = $1
              AND c.relname = $2
            ORDER BY
                CASE con.contype WHEN 'p' THEN 0 WHEN 'u' THEN 1 WHEN 'f' THEN 2 ELSE 3 END,
                con.conname;
        """

        try:
            columns_raw, constraints_raw = await asyncio.gather(
                self._client.execute_query(columns_query, (schema, table)),
                self._client.execute_query(constraints_query, (schema, table)),
            )

            columns = [_DDLColumnDef.model_validate(c) for c in columns_raw]
            if not columns:
                return PostgreSQLResponse(
                    success=False,
                    error="Table not found",
                    message=f"Table {schema}.{table} not found"
                )

            constraints = [_DDLConstraintDef.model_validate(con) for con in constraints_raw]

            ddl_lines = [f"CREATE TABLE {columns[0].qualified_table} ("]

            col_defs = []
            for col in columns:
                col_def = f"  {col.quoted_name} {col.data_type}"
                if col.not_null:
                    col_def += " NOT NULL"
                if col.default_value:
                    col_def += f" DEFAULT {col.default_value}"
                col_defs.append(col_def)

            for con in constraints:
                col_defs.append(f"  CONSTRAINT {con.quoted_name} {con.definition}")

            ddl_lines.append(",\n".join(col_defs))
            ddl_lines.append(");")
            
            ddl = "\n".join(ddl_lines)
            logger.debug(f"🔧 [PostgreSQLDataSource] Generated complete DDL for {schema}.{table}")
            
            ddl_result = DDLResult(ddl=ddl)
            return PostgreSQLResponse(
                success=True,
                data=ddl_result.model_dump(),
                message=f"Successfully retrieved DDL for {schema}.{table}"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_ddl failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table DDL"
            )
    
    async def test_connection(self) -> PostgreSQLResponse:
        """Test the database connection.
        
        Returns:
            PostgreSQLResponse with connection test result
        """
        logger.debug("🔧 [PostgreSQLDataSource] test_connection called")
        
        query = "SELECT version() as version, current_database() as database, current_user as user;"
        
        try:
            results = await self._client.execute_query(query)
            logger.info("🔧 [PostgreSQLDataSource] Connection test successful")
            
            conn_info = ConnectionTestResult.model_validate(results[0]) if results else ConnectionTestResult()
            return PostgreSQLResponse(
                success=True,
                data=conn_info.model_dump(),
                message="Connection successful"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] Connection test failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Connection test failed"
            )

    async def get_database_oid(self) -> int:
        """``pg_database.oid`` of the connected database.

        A database dropped and created again under its name gets a new one, and
        its tables new oids that can collide with the old database's.
        """
        rows = await self._client.execute_query(
            "SELECT oid FROM pg_database WHERE datname = current_database()",
        )
        return int(rows[0]["oid"])

    async def fetch_table_row_count(
        self,
        schema_name: str,
        table_name: str,
    ) -> int | None:
        """Return exact row count via COUNT(*)."""
        row_count, _stats = await self.fetch_table_row_and_column_stats(
            schema_name,
            table_name,
            [],
        )
        return row_count

    async def fetch_table_row_and_column_stats(
        self,
        schema_name: str,
        table_name: str,
        column_names: list[str],
    ) -> tuple[int | None, tuple[int, list[dict[str, Any]]] | None]:
        """One heap scan for row count and per-column distinct/null stats."""
        sql = build_table_column_stats_sql(schema_name, table_name, column_names)
        try:
            results = await self._client.execute_query(sql, local_settings=PROFILING_QUERY_SETTINGS)
            if not results:
                logger.warning(
                    "🔧 [PostgreSQLDataSource] table stats query failed for %s.%s: %s",
                    schema_name,
                    table_name,
                    "no rows returned",
                )
                return None, None
            total_rows, stats_list = parse_table_column_stats_row(
                results[0],
                column_names,
            )
            return total_rows, (total_rows, stats_list)
        except Exception as e:
            logger.warning(
                "🔧 [PostgreSQLDataSource] table stats query failed for %s.%s: %s",
                schema_name,
                table_name,
                e,
            )
            return None, None

    async def fetch_column_statistics(
        self,
        schema_name: str,
        table_name: str,
    ) -> TableColumnStatistics | None:
        """A table's pg_stats rows and reltuples, read from the catalog only.

        Returns None if the catalog can't be read. A table never analyzed
        comes back with no columns.
        """
        try:
            rows = await self._client.execute_query(
                _COLUMN_STATISTICS_QUERY, (schema_name, table_name), local_settings=PROFILING_QUERY_SETTINGS,
            )
            return _column_statistics_from_rows(rows)
        except Exception as e:
            logger.warning(
                "🔧 [PostgreSQLDataSource] column statistics query failed for %s.%s: %s",
                schema_name,
                table_name,
                e,
            )
            return None

    async def profile_columns(
        self,
        schema_name: str,
        table_name: str,
        columns: list[ColumnInfo],
        statistics: TableColumnStatistics,
        *,
        row_count: int | None = None,
        exact_stats: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, ColumnProfile]:
        """Value profile of each column, by name.

        ``row_count`` is the table's row count, the counted one whenever
        ``exact_stats`` is given; None means unknown, and reltuples stands in.
        ``exact_stats`` maps a column name to its counts from
        :func:`parse_table_column_stats_row`; without them the pg_stats
        estimates decide which columns get top values. A table counted empty
        gets no values, since its pg_stats describe rows that are gone.
        Never raises: a column that can't be profiled is left out.
        """
        if row_count is None and statistics.reltuples and statistics.reltuples > 0:
            row_count = int(statistics.reltuples)
        profiled_at = int(time.time() * 1000)
        named = [c for c in columns if c.name]
        if row_count == 0:
            return {c.name: ColumnProfile(profiled_at=profiled_at) for c in named}

        ranges = await self._fetch_ranges(schema_name, table_name, named, row_count)
        profiles: dict[str, ColumnProfile] = {}
        observed: dict[str, list[str | None]] = {}
        to_count: list[ColumnInfo] = []
        for column in named:
            try:
                pg = statistics.columns.get(column.name)
                profile = ColumnProfile(profiled_at=profiled_at)
                profile.min_value, profile.max_value = ranges.get(column.name, (None, None))
                observed[column.name] = (
                    (pg.most_common_vals or []) + (pg.histogram_bounds or []) if pg is not None else []
                )
                distinct, ratio = estimate_distinct_and_ratio(
                    (exact_stats or {}).get(column.name), pg, row_count
                )
                if top_values_eligible(column, distinct, ratio):
                    candidates = top_values_from_pg_stats(pg) if pg is not None else None
                    if candidates is None:
                        to_count.append(column)
                    else:
                        _set_top_values(profile, candidates)
                profile.numeric_text = detect_numeric_text(column, observed[column.name])
                profiles[column.name] = profile
            except Exception as e:
                logger.warning(
                    "🔧 [PostgreSQLDataSource] profiling %s.%s.%s failed: %s",
                    schema_name,
                    table_name,
                    column.name,
                    e,
                )

        if row_count is None or row_count > TOP_VALUES_SCAN_MAX_ROWS:
            return profiles
        for column in to_count[:TOP_VALUES_MAX_SCANS_PER_TABLE]:
            profile = profiles.get(column.name)
            if profile is None:
                continue
            try:
                counted = await self._count_top_values(schema_name, table_name, column.name)
            except Exception as e:
                logger.warning(
                    "🔧 [PostgreSQLDataSource] counting top values of %s.%s.%s failed: %s",
                    schema_name,
                    table_name,
                    column.name,
                    e,
                )
                continue
            candidates = top_values_from_counts(counted)
            if candidates is not None:
                _set_top_values(profile, candidates)
            profile.numeric_text = detect_numeric_text(
                column, observed[column.name] + [row.get("value") for row in counted]
            )
        return profiles

    async def _fetch_ranges(
        self,
        schema_name: str,
        table_name: str,
        columns: list[ColumnInfo],
        row_count: int | None,
    ) -> dict[str, tuple[str | None, str | None]]:
        """Exact MIN and MAX of the numeric and date/time columns, in one scan
        that needs no sort. Empty when the table's size is unknown or too big:
        pg_stats extremes come from a sample and miss the real ones."""
        # An extreme is one row's value: a phone number stored as bigint or a
        # birth date leaks as surely as a top value would.
        ranged = [
            c for c in columns
            if _column_kind(c) in ("numeric", "temporal") and not looks_personal(c.name)
        ]
        if not ranged or row_count is None or row_count * len(ranged) > MIN_MAX_SCAN_MAX_CELLS:
            return {}
        parts = ", ".join(
            f"min({quote_ident(c.name)})::text AS min_{i}, max({quote_ident(c.name)})::text AS max_{i}"
            for i, c in enumerate(ranged)
        )
        query = f"SELECT {parts} FROM {quote_ident(schema_name)}.{quote_ident(table_name)}"
        try:
            rows = await self._client.execute_query(query, local_settings=PROFILING_QUERY_SETTINGS)
        except Exception as e:
            logger.warning(
                "🔧 [PostgreSQLDataSource] min/max query failed for %s.%s: %s",
                schema_name,
                table_name,
                e,
            )
            return {}
        if not rows:
            return {}
        return {c.name: (rows[0].get(f"min_{i}"), rows[0].get(f"max_{i}")) for i, c in enumerate(ranged)}

    async def _count_top_values(
        self,
        schema_name: str,
        table_name: str,
        column_name: str,
    ) -> list[dict[str, Any]]:
        """Most common non-null values with their counts, for a column pg_stats
        lists none for. Values seen once are left out, so a column of mostly
        unique values yields little. ``total`` is every non-null row."""
        col = quote_ident(column_name)
        query = (
            f"SELECT value, n, total FROM ("
            f"SELECT {col}::text AS value, count(*) AS n, sum(count(*)) OVER () AS total "
            f"FROM {quote_ident(schema_name)}.{quote_ident(table_name)} "
            f"WHERE {col} IS NOT NULL GROUP BY 1"
            f") counted WHERE n > 1 ORDER BY n DESC, value LIMIT {TOP_VALUES_LIMIT}"
        )
        return await self._client.execute_query(query, local_settings=PROFILING_QUERY_SETTINGS)

    async def fetch_table_rows(
        self,
        schema_name: str,
        table_name: str,
        limit: int | None = None,
        order_by: list[str] | None = None,
        max_bytes: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` rows from a table (full row scan capped).

        Args:
            schema_name: Schema name
            table_name: Table name
            limit: Max rows; defaults to ``DEFAULT_TABLE_ROW_FETCH_LIMIT``
            order_by: Columns to order by. Without an order, which rows a capped
                read returns can change between reads of unchanged data.
            max_bytes: Stop once the rows' text form reaches this size. Counted
                by Postgres, so rows past the budget never leave the server.

        Returns:
            List of row dicts.

        Raises:
            The driver's exception. Its only caller streams the rows to the
            user, where swallowing the failure would serve a successful
            download of an empty table.
        """
        sample = _sample_query(schema_name, table_name, limit, order_by)
        if max_bytes is None:
            return await self._client.execute_query(sample)

        row_order = _order_list("sampled", order_by) if order_by else ""
        query = _byte_capped_query(sample, max_bytes, row_order) + f" ORDER BY {_ROW_NUMBER}"
        rows = await self._client.execute_query(query)
        for row in rows:
            row.pop(_ROW_NUMBER, None)
            row.pop(_RUNNING_BYTES, None)
        return rows

    async def fetch_column_value_counts(
        self,
        schema_name: str,
        table_name: str,
        column_name: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """A column's ``limit`` most common values as ``{"value", "count"}``.

        NULL counts as a value. Values are compared as text, so columns of types
        without an equality operator (json) group too.

        Raises:
            The driver's exception: the result is streamed to the user, where an
            empty list would read as a column with no values.
        """
        value = f"{quote_ident(column_name)}::text"
        query = (
            f"SELECT {value} AS value, COUNT(*) AS count "
            f"FROM {quote_ident(schema_name)}.{quote_ident(table_name)} "
            f"GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST LIMIT {int(limit)}"
        )
        return await self._client.execute_query(query, local_settings=PROFILING_QUERY_SETTINGS)

    async def get_sample_hash(
        self,
        schema_name: str,
        table_name: str,
        limit: int | None = None,
        order_by: list[str] | None = None,
        max_bytes: int | None = None,
    ) -> PostgreSQLResponse:
        """MD5 of a table's row sample, capped like ``fetch_table_rows``, computed server-side.

        A change signal for where the write counters stand still, such as a hot
        standby: replayed writes do not advance them. Without ``order_by`` the
        rows are ordered by their text, since a scan-order sample (and hash)
        can change while the table does not.
        """
        sample = _sample_query(schema_name, table_name, limit, order_by, by_row_text=not order_by)
        row_order = _order_list("sampled", order_by) if order_by else "sampled::text"
        if max_bytes is None:
            query = f"""
                SELECT md5(coalesce(string_agg(sampled::text, E'\\n' ORDER BY {row_order}), '')) AS sample_hash
                FROM ({sample}) sampled
            """
        else:
            query = f"""
                SELECT md5(coalesce(string_agg(sized::text, E'\\n' ORDER BY {_ROW_NUMBER}), '')) AS sample_hash
                FROM ({_byte_capped_query(sample, max_bytes, row_order)}) sized
            """
        try:
            rows = await self._client.execute_query(query)
            return PostgreSQLResponse(success=True, data={"sample_hash": rows[0]["sample_hash"]})
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_sample_hash failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to hash table sample")

    async def is_in_recovery(self) -> PostgreSQLResponse:
        """Whether the server is a standby replaying another server's writes."""
        try:
            rows = await self._client.execute_query("SELECT pg_is_in_recovery() AS in_recovery")
            return PostgreSQLResponse(success=True, data={"in_recovery": bool(rows[0]["in_recovery"])})
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] is_in_recovery failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to read recovery state")

    async def get_columns(self, schemas: list[str] | None = None) -> PostgreSQLResponse:
        """Columns of every table in ``schemas`` (all user schemas if None), in one query.

        Same per-column fields as ``get_table_info``, plus ``schema_name``,
        ``table_name`` and ``attnum`` (which a rename leaves alone, so it says
        which column a new name belongs to). Lets change detection read a whole
        catalog without a round trip per table.
        """
        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(success=True, data=[])

        query = """
            WITH unique_columns AS (
                SELECT DISTINCT n.nspname, c.relname, a.attname
        """ + _CONSTRAINTS_OF_TABLE + """
                JOIN pg_attribute a
                  ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey)
                WHERE con.contype = 'u'
            )
            SELECT
                col.table_schema AS schema_name,
                col.table_name,
                col.column_name AS name,
                col.ordinal_position::int AS attnum,
                col.data_type,
                col.udt_name,
                col.character_maximum_length,
                col.numeric_precision,
                col.numeric_scale,
                col.datetime_precision,
                col.is_nullable = 'YES' AS nullable,
                col.column_default AS "default",
                u.attname IS NOT NULL AS is_unique
            FROM information_schema.columns col
            LEFT JOIN unique_columns u
              ON u.nspname = col.table_schema
             AND u.relname = col.table_name
             AND u.attname = col.column_name
            WHERE col.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        """
        if schemas:
            query += " AND col.table_schema = ANY($1::text[])"
        query += " ORDER BY col.table_schema, col.table_name, col.ordinal_position;"

        try:
            rows = await self._client.execute_query(query, (list(schemas),) if schemas else None)
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_columns failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get columns")

    async def get_constraint_hashes(self, schemas: list[str] | None = None) -> PostgreSQLResponse:
        """A digest of each table's keys and constraints, in one query.

        Covers primary, unique, foreign-key and check constraints, and unique
        indexes that back no constraint. A foreign key's definition names the
        table it references, so renaming that table changes the digest of
        every table pointing at it. A table with none of these has no row.

        ``constraint_shape`` digests the same keys with no name in them:
        columns by attnum, a referenced table by oid, expressions as stored.
        A rename of the table, of a column or of a constraint, or of a table
        it references, leaves it as it was.
        """
        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(success=True, data=[])

        query = """
            WITH definitions AS (
                SELECT con.conrelid AS relid,
                       con.contype::text || ':' || con.conname || ':' || pg_get_constraintdef(con.oid) AS definition,
                       concat_ws(':', con.contype::text, con.conkey::text, con.confrelid::text,
                                 con.confkey::text, con.confupdtype::text, con.confdeltype::text,
                                 con.confmatchtype::text, con.conbin::text) AS shape
                FROM pg_constraint con
                WHERE con.contype IN ('p', 'u', 'f', 'c') AND con.conrelid <> 0{top_level}
                UNION ALL
                SELECT i.indrelid, 'i:' || ic.relname || ':' || pg_get_indexdef(i.indexrelid),
                       concat_ws(':', 'i', i.indkey::text, i.indexprs::text, i.indpred::text)
                FROM pg_index i
                JOIN pg_class ic ON ic.oid = i.indexrelid
                WHERE i.indisunique
                  -- A foreign key's conindid is the unique index it references, not one it is backed by.
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_constraint con
                      WHERE con.conindid = i.indexrelid AND con.contype IN ('p', 'u', 'x')
                  )
            )
            SELECT n.nspname AS schema_name,
                   c.relname AS table_name,
                   md5(string_agg(d.definition, '|' ORDER BY d.definition)) AS constraint_hash,
                   md5(string_agg(d.shape, '|' ORDER BY d.shape)) AS constraint_shape
            FROM definitions d
            JOIN pg_class c ON c.oid = d.relid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        """
        if schemas:
            query += " AND n.nspname = ANY($1::text[])"
        query += " GROUP BY n.nspname, c.relname ORDER BY n.nspname, c.relname;"

        try:
            query = query.replace("{top_level}", await self._top_level_constraints())
            rows = await self._client.execute_query(query, (list(schemas),) if schemas else None)
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_constraint_hashes failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get constraints")

    async def get_referencing_tables(self, oids: list[int]) -> PostgreSQLResponse:
        """The tables with a foreign key to one of these (by ``pg_class.oid``)."""
        if not oids:
            return PostgreSQLResponse(success=True, data=[])
        try:
            query = (
                "SELECT DISTINCT n.nspname AS schema_name, c.relname AS table_name"
                + _CONSTRAINTS_OF_TABLE
                + " WHERE con.contype = 'f' AND con.confrelid = ANY($1::oid[])"
                + await self._top_level_constraints()
                + " ORDER BY 1, 2;"
            )
            rows = await self._client.execute_query(query, ([int(oid) for oid in oids],))
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_referencing_tables failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get referencing tables")

    async def get_primary_keys_by_table(self, schemas: list[str] | None = None) -> PostgreSQLResponse:
        """Primary-key columns of every table in ``schemas``, in key order, in one query."""
        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(success=True, data=[])

        query = """
            SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name
        """ + _CONSTRAINTS_OF_TABLE + """
            CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a
              ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.contype = 'p'
        """
        if schemas:
            query += " AND n.nspname = ANY($1::text[])"
        query += " ORDER BY n.nspname, c.relname, k.ord;"

        try:
            rows = await self._client.execute_query(query, (list(schemas),) if schemas else None)
            return PostgreSQLResponse(success=True, data=rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_primary_keys_by_table failed: {e}")
            return PostgreSQLResponse(success=False, error=str(e), message="Failed to get primary keys")

    async def get_table_stats(self, schemas: list[str] | None = None) -> PostgreSQLResponse:
        """Get table statistics for change detection.
        
        Fetches row count estimates and cumulative DML counters from pg_stat_user_tables.
        Used for incremental sync to detect which tables have changed.
        
        Args:
            schemas: Optional list of schemas to filter. If None, returns all user schemas.
            
        Returns:
            PostgreSQLResponse with table stats including:
            - schema_name: Schema name
            - table_name: Table name
            - n_live_tup: Estimated number of live rows
            - n_tup_ins: Cumulative number of rows inserted
            - n_tup_upd: Cumulative number of rows updated
            - n_tup_del: Cumulative number of rows deleted
        """
        logger.debug("🔧 [PostgreSQLDataSource] get_table_stats called")

        if schemas is not None and len(schemas) == 0:
            return PostgreSQLResponse(
                success=True,
                data=[],
                message="Successfully retrieved stats for 0 tables"
            )

        # Driven by pg_class so it holds exactly the tables list_tables sees in
        # information_schema.tables: ordinary and partitioned tables the user
        # holds some privilege on. A partitioned table's own counters never
        # move (its rows live in its partitions), so it gets their totals.
        query = """
            WITH RECURSIVE partition_tree AS (
                SELECT c.oid AS root, c.oid AS relid FROM pg_class c WHERE c.relkind = 'p'
                UNION ALL
                SELECT t.root, i.inhrelid
                FROM partition_tree t JOIN pg_inherits i ON i.inhparent = t.relid
            ),
            partition_totals AS (
                SELECT t.root,
                       sum(st.n_live_tup) AS n_live_tup,
                       sum(st.n_tup_ins) AS n_tup_ins,
                       sum(st.n_tup_upd) AS n_tup_upd,
                       sum(st.n_tup_del) AS n_tup_del
                FROM partition_tree t JOIN pg_stat_all_tables st ON st.relid = t.relid
                GROUP BY t.root
            ),
            partition_storage AS (
                SELECT t.root,
                       md5(string_agg(pc.relfilenode::text, ',' ORDER BY pc.oid)) AS storage_id
                FROM partition_tree t JOIN pg_class pc ON pc.oid = t.relid
                GROUP BY t.root
            )
            SELECT
                n.nspname AS schema_name,
                c.relname AS table_name,
                c.relkind::text AS kind,
                c.oid::bigint AS oid,
                coalesce(ps.storage_id, c.relfilenode::text) AS storage_id,
                coalesce(pt.n_live_tup, st.n_live_tup, 0)::bigint AS n_live_tup,
                coalesce(pt.n_tup_ins, st.n_tup_ins, 0)::bigint AS n_tup_ins,
                coalesce(pt.n_tup_upd, st.n_tup_upd, 0)::bigint AS n_tup_upd,
                coalesce(pt.n_tup_del, st.n_tup_del, 0)::bigint AS n_tup_del,
                (coalesce(st.analyze_count, 0) + coalesce(st.autoanalyze_count, 0))::bigint AS analyze_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_all_tables st ON st.relid = c.oid
            LEFT JOIN partition_totals pt ON pt.root = c.oid
            LEFT JOIN partition_storage ps ON ps.root = c.oid
            WHERE c.relkind IN ('r', 'p')
              AND NOT c.relispartition
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname !~ '^pg_(toast_)?temp_'
              AND (
                  pg_has_role(c.relowner, 'USAGE')
                  OR has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
                  OR has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
              )
        """

        if schemas:
            query += " AND n.nspname = ANY($1::text[])"
        query += " ORDER BY n.nspname, c.relname;"

        try:
            params = (list(schemas),) if schemas else None
            results = await self._client.execute_query(query, params)
            stats = [TableStats.model_validate(row) for row in results]
            logger.debug(f"🔧 [PostgreSQLDataSource] Found stats for {len(stats)} tables")
            
            return PostgreSQLResponse(
                success=True,
                data=[s.model_dump() for s in stats],
                message=f"Successfully retrieved stats for {len(stats)} tables"
            )
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLDataSource] get_table_stats failed: {e}")
            return PostgreSQLResponse(
                success=False,
                error=str(e),
                message="Failed to get table stats"
            )

