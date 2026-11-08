"""Counts and value profiles of a Snowflake table's columns, in one scan.

Every Snowflake statement resumes a warehouse and the table is columnar, so
one statement reads everything: row and per-column counts, MIN/MAX of
the columns whose extremes may be stored, whether each text column holds only
numbers, and the most common values of the columns that may be a vocabulary.
Distinct counts are approximate past a size budget. The shared rules
in ``app.connectors.core.sql.profiling`` decide what may be stored; which
tables are scanned at all is the connector's call.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from app.connectors.core.sql.profiling import (
    TOP_VALUES_LIMIT,
    ColumnKind,
    ColumnProfile,
    ColumnStats,
    distinct_and_ratio_from_counts,
    looks_personal,
    parse_table_column_stats_row,
    range_eligible,
    set_top_values,
    top_values_from_counts,
    vocabulary_eligible,
)
from app.sources.external.snowflake.identifiers import (
    qualified_identifier,
    quote_identifier,
)
from app.sources.external.snowflake.statements import (
    SnowflakeFetchError,
    SnowflakeStatementRunner,
)

logger = logging.getLogger(__name__)

# Exact COUNT(DISTINCT) only while rows x counted columns stays under this, the
# same budget PostgreSQL uses; past it, APPROX_COUNT_DISTINCT (HyperLogLog).
EXACT_COLUMN_STATS_MAX_CELLS = 1_000_000
# One more than kept: whether APPROX_TOP_K counts NULL as a value is
# undocumented, and NULL is dropped. Its counts are exact up to the counters,
# which covers every column small enough to be a vocabulary.
TOP_K = TOP_VALUES_LIMIT + 1
TOP_K_COUNTERS = 1000
# Columns per kind of per-column test in one statement; the statement grows with each.
MAX_COLUMNS_PER_TEST = 50
PROFILE_STATEMENT_TIMEOUT_S = 60

# Snowflake's regular expressions match the whole string. Character classes
# instead of \d keep backslashes out of the SQL literal.
_NUMBER_PATTERN = "[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?"
# ZIP 02134, code 007: an identifier that casting to a number would break.
_LEADING_ZERO_PATTERN = "0[0-9].*"

_NUMERIC = frozenset({
    "NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT",
    "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL", "DECFLOAT",
})
_TEMPORAL = frozenset({"DATE", "TIME", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"})
_TEXT = frozenset({"TEXT", "VARCHAR", "CHAR", "CHARACTER", "STRING", "NCHAR", "NVARCHAR", "NVARCHAR2"})
# No equality on these, or none worth counting.
_UNCOUNTABLE = frozenset({"VARIANT", "OBJECT", "ARRAY", "GEOGRAPHY", "GEOMETRY", "VECTOR", "FILE"})
# Values that are no vocabulary (binary, structured, spatial), and DECFLOAT,
# which APPROX_TOP_K refuses.
_NO_TOP_VALUES = _UNCOUNTABLE | {"BINARY", "VARBINARY", "DECFLOAT"}


def base_type(data_type: str | None) -> str:
    """``NUMBER(38,0)`` → ``NUMBER``; upper-cased."""
    return (data_type or "").split("(", 1)[0].strip().upper()


def column_kind(data_type: str | None) -> ColumnKind | None:
    name = base_type(data_type)
    if name in _NUMERIC:
        return "numeric"
    if name in _TEMPORAL:
        return "temporal"
    if name in _TEXT:
        return "text"
    return None


def is_countable(data_type: str | None) -> bool:
    return base_type(data_type) not in _UNCOUNTABLE


def top_values_excluded(data_type: str | None) -> bool:
    return base_type(data_type) in _NO_TOP_VALUES


class ProfiledColumn(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def data_type(self) -> str | None: ...
    @property
    def is_primary_key(self) -> bool: ...
    @property
    def is_unique(self) -> bool: ...
    @property
    def is_part_of_composite_primary_key(self) -> bool: ...


@dataclass
class ScanPlan:
    """The columns each test in the scan covers, by position in its alias."""
    counted: list[str]
    ranged: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


def plan_scan(columns: Sequence[ProfiledColumn], *, with_values: bool) -> ScanPlan:
    """Which columns get counted, ranged, tested for numeric text, and top-k'd.

    Without ``with_values`` (table indexing off) only counts: extremes and top
    values are row data, and the numeric-text test reads every value.
    """
    named = [c for c in columns if c.name]
    plan = ScanPlan(counted=[c.name for c in named if is_countable(c.data_type)])
    if not with_values:
        return plan
    plan.ranged = [c.name for c in named if range_eligible(column_kind(c.data_type), c.name)]
    plan.text = [c.name for c in named if column_kind(c.data_type) == "text"][:MAX_COLUMNS_PER_TEST]
    plan.candidates = [
        c.name for c in named
        if is_countable(c.data_type)
        and not top_values_excluded(c.data_type)
        and not c.is_unique
        and not (c.is_primary_key and not c.is_part_of_composite_primary_key)
        and not looks_personal(c.name)
    ][:MAX_COLUMNS_PER_TEST]
    return plan


def scan_statement(table_reference: str, plan: ScanPlan, *, exact: bool) -> str:
    """The profiling scan. Aliases are positional within each test (``d0``, ``mn0``,
    ``nt0``, ``k0``...): a name-based alias could pass the identifier length limit."""
    distinct = "COUNT(DISTINCT {})" if exact else "APPROX_COUNT_DISTINCT({})"
    parts = ["COUNT(*) AS total_rows"]
    for i, name in enumerate(plan.counted):
        column = quote_identifier(name)
        parts.append(f"{distinct.format(column)} AS d{i}")
        parts.append(f"COUNT(*) - COUNT({column}) AS n{i}")
    for i, name in enumerate(plan.ranged):
        column = quote_identifier(name)
        parts.append(f"MIN({column}) AS mn{i}, MAX({column}) AS mx{i}")
    for i, name in enumerate(plan.text):
        value = f"TRIM({quote_identifier(name)})"
        parts.append(f"COUNT({value}) AS tc{i}")
        parts.append(f"COUNT_IF(NOT REGEXP_LIKE({value}, '{_NUMBER_PATTERN}')) AS nt{i}")
        parts.append(f"COUNT_IF(REGEXP_LIKE({value}, '{_LEADING_ZERO_PATTERN}')) AS lz{i}")
    for i, name in enumerate(plan.candidates):
        column = quote_identifier(name)
        parts.append(f"APPROX_TOP_K({column}, {TOP_K}, {TOP_K_COUNTERS}) AS k{i}")
        parts.append(f"COUNT({column}) AS t{i}")
    return f"SELECT {', '.join(parts)} FROM {table_reference}"


def _value(row: dict[str, Any], key: str) -> Any:  # noqa: ANN401
    """A result value by alias; Snowflake upper-cases unquoted aliases."""
    return row.get(key.upper(), row.get(key))


def _int(value: Any) -> int | None:  # noqa: ANN401
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def top_value_pairs(raw: Any) -> list[tuple[str, int]]:  # noqa: ANN401
    """``APPROX_TOP_K``'s ``[[value, count], ...]`` as ``(text, count)`` pairs; NULL is no value."""
    try:
        pairs = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    parsed = []
    for pair in pairs if isinstance(pairs, list) else []:
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] is None:
            continue
        count = _int(pair[1])
        if count is None:
            continue
        value = pair[0]
        parsed.append((value if isinstance(value, str) else json.dumps(value), count))
    return parsed


def top_value_rows(pairs: list[tuple[str, int]], total: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Pairs as ``{value, n, total}`` rows for ``top_values_from_counts``.

    A value counted once is left out, as PostgreSQL's GROUP BY leaves out
    ``n = 1``: a column of mostly unique values yields little.
    """
    total_count = _int(total)
    if not total_count:
        return []
    return [{"value": value, "n": count, "total": total_count} for value, count in pairs if count > 1]


def numeric_text(non_null: int | None, not_numbers: int | None, leading_zeros: int | None) -> bool | None:
    """Whether a text column holds only numbers, none with a leading zero; None when it holds nothing."""
    if not non_null or not_numbers is None or leading_zeros is None:
        return None
    return not_numbers == 0 and leading_zeros == 0


def _as_text(value: Any) -> str | None:  # noqa: ANN401
    return None if value is None else str(value)


@dataclass
class TableProfile:
    """What profiling learned about a table. Stats and profiles are by column name."""
    row_count: int | None = None
    stats: dict[str, ColumnStats] = field(default_factory=dict)
    profiles: dict[str, ColumnProfile] = field(default_factory=dict)


class SnowflakeProfiler:
    def __init__(self, runner: SnowflakeStatementRunner) -> None:
        self.runner = runner

    async def profile_table(
        self,
        database: str,
        schema: str,
        table: str,
        columns: Sequence[ProfiledColumn],
        *,
        row_count: int,
        with_values: bool,
    ) -> TableProfile:
        """Scan the table once. ``row_count`` is the catalog's (known, above zero);
        it picks exact or approximate distinct counts.

        Raises SnowflakeFetchError when the scan is refused or its result is unreadable.
        """
        plan = plan_scan(columns, with_values=with_values)
        exact = row_count * max(len(plan.counted), 1) <= EXACT_COLUMN_STATS_MAX_CELLS
        # Taken before the scan: a change that lands while it runs is newer.
        profiled_at = int(time.time() * 1000)
        rows = (await self.runner.run(
            scan_statement(qualified_identifier(database, schema, table), plan, exact=exact),
            database=database, timeout_s=PROFILE_STATEMENT_TIMEOUT_S,
        )).as_dicts()
        if not rows or _value(rows[0], "total_rows") is None:
            raise SnowflakeFetchError(f"The profiling scan of {table} returned no counts")
        row = rows[0]

        result = TableProfile()
        result.row_count, stats_list = parse_table_column_stats_row(row, plan.counted)
        result.stats = {s["name"]: s for s in stats_list}
        # Without values the profile holds only when it was taken.
        result.profiles = {c.name: ColumnProfile(profiled_at=profiled_at) for c in columns if c.name}
        if not with_values:
            return result

        for i, name in enumerate(plan.ranged):
            result.profiles[name].min_value = _as_text(_value(row, f"mn{i}"))
            result.profiles[name].max_value = _as_text(_value(row, f"mx{i}"))
        for i, name in enumerate(plan.text):
            result.profiles[name].numeric_text = numeric_text(
                _int(_value(row, f"tc{i}")), _int(_value(row, f"nt{i}")), _int(_value(row, f"lz{i}")),
            )
        by_name = {c.name: c for c in columns if c.name}
        for i, name in enumerate(plan.candidates):
            if not self._eligible(by_name[name], result.stats.get(name), result.row_count):
                continue
            pairs = top_value_pairs(_value(row, f"k{i}"))
            found = top_values_from_counts(top_value_rows(pairs, _value(row, f"t{i}")))
            if found is not None:
                set_top_values(result.profiles[name], found)
        return result

    @staticmethod
    def _eligible(column: ProfiledColumn, stats: ColumnStats | None, row_count: int | None) -> bool:
        if stats is None:
            return False
        distinct, ratio = distinct_and_ratio_from_counts(stats["distinct_count"], stats["null_count"], row_count)
        return vocabulary_eligible(
            name=column.name,
            is_unique=column.is_unique,
            is_primary_key=column.is_primary_key,
            is_part_of_composite_primary_key=column.is_part_of_composite_primary_key,
            excluded_type=top_values_excluded(column.data_type),
            distinct_count=distinct,
            non_null_ratio=ratio,
        )


# A table this large is not scanned; its stored profile is kept.
PROFILE_MAX_BYTES = 20 * 1024**3
# Per sync.
PROFILING_TIME_BUDGET_S = 30 * 60
MAX_CONSECUTIVE_UNAVAILABLE = 3
# Connection, insufficient resources, operator intervention: a suspended
# warehouse or a tripped resource monitor, which every next table would hit too.
_UNAVAILABLE_SQLSTATE_CLASSES = frozenset({"08", "53", "57"})
# A statement that timed out (57014) says the table was large, not that the
# warehouse is gone.
_TIMED_OUT_SQLSTATE = "57014"
# A profile taken this long after the table last changed describes it; the
# margin covers the scan's own run time and clock skew with Snowflake.
PROFILE_FRESHNESS_MARGIN_MS = 5 * 60 * 1000


def profile_is_current(profiled_at: int | None, last_altered_ms: int | None) -> bool:
    """Whether a profile taken at ``profiled_at`` still describes a table last changed at ``last_altered_ms``."""
    if profiled_at is None or last_altered_ms is None:
        return False
    return profiled_at - PROFILE_FRESHNESS_MARGIN_MS >= last_altered_ms


class ProfilingDecision(Enum):
    SCAN = "scan"
    # Not scanned: the stored stats and profiles stay as they are.
    KEEP_STORED = "keep_stored"
    # Known empty: zero counts and empty profiles, without a scan.
    EMPTY = "empty"


def decide_profiling(
    *,
    row_count: int | None,
    table_type: str | None,
    size_bytes: int | None,
    unchanged: bool,
    budget_exhausted: bool,
) -> ProfilingDecision:
    """Whether to scan a table."""
    if unchanged or budget_exhausted:
        return ProfilingDecision.KEEP_STORED
    # External tables report no row count; scanning one reads its files.
    if row_count is None or (table_type or "").upper() == "EXTERNAL TABLE":
        return ProfilingDecision.KEEP_STORED
    if row_count == 0:
        return ProfilingDecision.EMPTY
    if size_bytes is not None and size_bytes > PROFILE_MAX_BYTES:
        return ProfilingDecision.KEEP_STORED
    return ProfilingDecision.SCAN


def empty_profile(columns: Sequence[ProfiledColumn]) -> TableProfile:
    """What a scan of an empty table would find, with or without values: none."""
    result = TableProfile(row_count=0)
    for position, name in enumerate(c.name for c in columns if c.name and is_countable(c.data_type)):
        result.stats[name] = {
            "name": name, "ordinal_position": position,
            "distinct_count": 0, "null_count": 0, "cardinality_ratio": None,
        }
    profiled_at = int(time.time() * 1000)
    result.profiles = {c.name: ColumnProfile(profiled_at=profiled_at) for c in columns if c.name}
    return result


class ProfilingBudget:
    """One sync's allowance for profiling scans."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # Counted from the first scan, not the start of the sync.
        self._started: float | None = None
        self._unavailable_in_a_row = 0

    @property
    def exhausted(self) -> bool:
        if self._unavailable_in_a_row >= MAX_CONSECUTIVE_UNAVAILABLE:
            return True
        return self._started is not None and self._clock() - self._started > PROFILING_TIME_BUDGET_S

    def start(self) -> None:
        if self._started is None:
            self._started = self._clock()

    def record_success(self) -> None:
        self._unavailable_in_a_row = 0

    def record_failure(self, error: BaseException) -> None:
        sqlstate = getattr(error, "sqlstate", None) or ""
        if sqlstate[:2] in _UNAVAILABLE_SQLSTATE_CLASSES and sqlstate != _TIMED_OUT_SQLSTATE:
            self._unavailable_in_a_row += 1
        else:
            self._unavailable_in_a_row = 0
