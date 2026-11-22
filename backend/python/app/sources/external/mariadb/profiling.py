"""Counts and value profiles of a MariaDB table's columns.

MariaDB has no approximate distinct count and no column statistics like
PostgreSQL's ``pg_stats``. Distinct and null counts are exact while rows x
counted columns stays within a budget, in one scan that also takes the
ranges and the numeric-text test; beyond it, the cardinality of an index the
column leads stands in for its distinct count. Top values are counted per
column, for the columns that may be a vocabulary. Every statement runs on a
bounded, read-only connection of its own: the async pool returns a
statement stopped by ``max_statement_time`` as no rows. The shared rules in
``app.connectors.core.sql.profiling`` decide what may be stored.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.connectors.core.sql.profiling import (
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
from app.sources.external.mariadb.values import format_time

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence

    from app.sources.client.mariadb.mariadb import MariaDBClient

# Exact counts while rows x counted columns stays within this, as PostgreSQL.
EXACT_COLUMN_STATS_MAX_CELLS = 1_000_000
# MIN and MAX of every ranged column in one unsorted scan.
MIN_MAX_SCAN_MAX_CELLS = 50_000_000
TOP_VALUES_SCAN_MAX_ROWS = 1_000_000
TOP_VALUES_MAX_SCANS_PER_TABLE = 10
TOP_VALUES_LIMIT = 21
MAX_COLUMNS_PER_TEST = 50
PROFILE_STATEMENT_TIMEOUT_S = 30
# Per sync, as Snowflake's.
PROFILING_TIME_BUDGET_S = 30 * 60
MAX_CONSECUTIVE_UNAVAILABLE = 3

# MariaDB's REGEXP searches; anchored, it matches the whole value. No
# backslashes or quotes, so it can sit in the statement as a literal.
_NUMBER_PATTERN = "^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$"
_LEADING_ZERO_PATTERN = "^0[0-9]"

_NUMERIC = frozenset({
    "tinyint", "smallint", "mediumint", "int", "integer", "bigint",
    "decimal", "numeric", "dec", "fixed", "float", "double", "real",
})
_TEMPORAL = frozenset({"date", "datetime", "timestamp", "time", "year"})
# JSON is LONGTEXT in MariaDB; ENUM and SET are labels: text, never ranged.
_TEXT = frozenset({
    "char", "varchar", "tinytext", "text", "mediumtext", "longtext", "json", "enum", "set",
    "inet4", "inet6", "uuid",
})
_UNRANGED_TEXT = frozenset({"enum", "set"})
# Addresses and identifiers: counted, never a vocabulary (the shared rules exclude network addresses).
_NO_TOP_VALUES = frozenset({"inet4", "inet6", "uuid"})
# Bytes, documents of bytes and shapes: no vocabulary, and costly to count.
_UNCOUNTABLE = frozenset({
    "binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob", "bit",
    "geometry", "point", "linestring", "polygon", "multipoint", "multilinestring",
    "multipolygon", "geometrycollection",
})


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


def base_type(data_type: str | None) -> str:
    """``int(11) unsigned`` -> ``int``; lower-cased."""
    return (data_type or "").split("(", 1)[0].split(" ", 1)[0].strip().lower()


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


def is_ranged(column: ProfiledColumn) -> bool:
    return base_type(column.data_type) not in _UNRANGED_TEXT and range_eligible(
        column_kind(column.data_type), column.name,
    )


def quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


@dataclass
class ScanPlan:
    """The columns each test in the scan covers, by position in its alias."""
    counted: list[str]
    ranged: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


def plan_scan(columns: Sequence[ProfiledColumn], *, with_values: bool, counts: bool) -> ScanPlan:
    """Which columns get counted, ranged and tested for numeric text.

    Without ``with_values`` (table indexing off) only counts: extremes are
    row data, and the numeric-text test reads every value. Without
    ``counts`` (over the cell budget) only ranges.
    """
    named = [c for c in columns if c.name]
    plan = ScanPlan(counted=[c.name for c in named if is_countable(c.data_type)] if counts else [])
    if not with_values:
        return plan
    plan.ranged = [c.name for c in named if is_ranged(c)]
    if counts:
        plan.text = [c.name for c in named if column_kind(c.data_type) == "text"][:MAX_COLUMNS_PER_TEST]
    return plan


def scan_statement(table_reference: str, plan: ScanPlan) -> str:
    """The profiling scan; positional aliases (``d0``, ``mn0``…) as the shared parser reads them."""
    parts = ["COUNT(*) AS total_rows"]
    for i, name in enumerate(plan.counted):
        column = quote(name)
        parts.append(f"COUNT(DISTINCT {column}) AS d{i}")
        parts.append(f"COUNT(*) - COUNT({column}) AS n{i}")
    for i, name in enumerate(plan.ranged):
        column = quote(name)
        parts.append(f"CAST(MIN({column}) AS CHAR) AS mn{i}, CAST(MAX({column}) AS CHAR) AS mx{i}")
    for i, name in enumerate(plan.text):
        value = f"TRIM({quote(name)})"
        parts.append(f"COUNT({value}) AS tc{i}")
        parts.append(f"SUM({value} NOT REGEXP '{_NUMBER_PATTERN}') AS nt{i}")
        parts.append(f"SUM({value} REGEXP '{_LEADING_ZERO_PATTERN}') AS lz{i}")
    return f"SELECT {', '.join(parts)} FROM {table_reference}"


def top_values_statement(table_reference: str, column_name: str) -> str:
    """Most common non-null values with their counts; values seen once are left out."""
    column = quote(column_name)
    return (
        f"SELECT value, n, total FROM ("
        f"SELECT {column} AS value, COUNT(*) AS n, SUM(COUNT(*)) OVER () AS total "
        f"FROM {table_reference} WHERE {column} IS NOT NULL GROUP BY {column}"
        f") counted WHERE n > 1 ORDER BY n DESC, value LIMIT {TOP_VALUES_LIMIT}"
    )


def _int(value: Any) -> int | None:  # noqa: ANN401
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:  # noqa: ANN401
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, datetime.timedelta):
        return format_time(value)
    return str(value)


def numeric_text(non_null: int | None, not_numbers: int | None, leading_zeros: int | None) -> bool | None:
    """Whether a text column holds only numbers, none with a leading zero; None when it holds nothing."""
    if not non_null or not_numbers is None or leading_zeros is None:
        return None
    return not_numbers == 0 and leading_zeros == 0


@dataclass
class TableProfile:
    """What profiling learned about a table; stats and profiles by column name."""
    row_count: int | None = None
    stats: dict[str, ColumnStats] = field(default_factory=dict)
    profiles: dict[str, ColumnProfile] = field(default_factory=dict)


class ProfileError(RuntimeError):
    """A profiling statement answered with nothing to read."""


class MariaDBProfiler:
    def __init__(self, client: MariaDBClient, *, slots: asyncio.Semaphore | None = None) -> None:
        self.client = client
        # Outside the sync, statements share the connector's limit.
        self.slots = slots

    async def _rows(self, statement: str) -> list[dict[str, Any]]:
        columns, rows, _ = await self.client.execute_read_only_query(
            statement, PROFILE_STATEMENT_TIMEOUT_S, slots=self.slots,
        )
        return [dict(zip(columns, row)) for row in rows]

    async def profile_table(
        self,
        database: str,
        table: str,
        columns: Sequence[ProfiledColumn],
        *,
        row_estimate: int | None,
        cardinality: dict[str, int],
        with_values: bool,
    ) -> TableProfile:
        """Profile the table. ``row_estimate`` is ``TABLE_ROWS``; it picks what may be scanned.

        Raises when a scan fails or answers with no row: a table read as empty
        would store empty profiles for it.
        """
        reference = f"{quote(database)}.{quote(table)}"
        named = [c for c in columns if c.name]
        countable = [c for c in named if is_countable(c.data_type)]
        # TABLE_ROWS can read 0 for a full InnoDB table: 0 is no estimate, not an empty table.
        if not row_estimate:
            row_estimate = None
        counts = row_estimate is not None and row_estimate * max(len(countable), 1) <= EXACT_COLUMN_STATS_MAX_CELLS
        plan = plan_scan(named, with_values=with_values, counts=counts)
        if not counts and (row_estimate is None or row_estimate * max(len(plan.ranged), 1) > MIN_MAX_SCAN_MAX_CELLS):
            plan.ranged = []
        # Taken before the scan: a change that lands while it runs is newer.
        profiled_at = int(time.time() * 1000)
        result = TableProfile(row_count=row_estimate)
        result.profiles = {c.name: ColumnProfile(profiled_at=profiled_at) for c in named}

        if plan.counted or plan.ranged or plan.text:
            rows = await self._rows(scan_statement(reference, plan))
            if not rows:
                raise ProfileError(f"The profiling scan of {table} returned no row")
            row = rows[0]
            if counts:
                result.row_count, stats = parse_table_column_stats_row(row, plan.counted)
                result.stats = {s["name"]: s for s in stats}
            for i, name in enumerate(plan.ranged):
                result.profiles[name].min_value = _text(row.get(f"mn{i}"))
                result.profiles[name].max_value = _text(row.get(f"mx{i}"))
            for i, name in enumerate(plan.text):
                result.profiles[name].numeric_text = numeric_text(
                    _int(row.get(f"tc{i}")), _int(row.get(f"nt{i}")), _int(row.get(f"lz{i}")),
                )
        if not counts:
            # Index cardinality: an estimate of distinct values, and nothing on nulls.
            for position, column in enumerate(named):
                if column.name in cardinality:
                    result.stats[column.name] = {
                        "name": column.name, "ordinal_position": position,
                        "distinct_count": cardinality[column.name], "null_count": None,
                        "cardinality_ratio": None,
                    }
        if not with_values or not counts or not result.row_count or result.row_count > TOP_VALUES_SCAN_MAX_ROWS:
            return result

        scanned = 0
        for column in countable:
            if scanned >= TOP_VALUES_MAX_SCANS_PER_TABLE:
                break
            if not self._eligible(column, result.stats.get(column.name), result.row_count):
                continue
            scanned += 1
            found = top_values_from_counts([
                {**r, "value": _text(r.get("value"))} for r in await self._rows(
                    top_values_statement(reference, column.name),
                )
            ])
            if found is not None:
                set_top_values(result.profiles[column.name], found)
        return result

    @staticmethod
    def _eligible(column: ProfiledColumn, stats: ColumnStats | None, row_count: int | None) -> bool:
        if stats is None or looks_personal(column.name):
            return False
        distinct, ratio = distinct_and_ratio_from_counts(stats["distinct_count"], stats["null_count"], row_count)
        return vocabulary_eligible(
            name=column.name,
            is_unique=column.is_unique,
            is_primary_key=column.is_primary_key,
            is_part_of_composite_primary_key=column.is_part_of_composite_primary_key,
            excluded_type=not is_countable(column.data_type) or base_type(column.data_type) in _NO_TOP_VALUES,
            distinct_count=distinct,
            non_null_ratio=ratio,
        )


# Timeouts and lost or refused connections: what every next table would hit too.
# Lost or refused connections. A statement stopped by max_statement_time
# (1969) says only that its table is big: the next table may well profile.
_UNAVAILABLE_ERRNOS = frozenset({2002, 2003, 2006, 2013})


class ProfilingBudget:
    """Per sync: stops profiling after 30 minutes, or 3 failures in a row that say the server is unavailable."""

    def __init__(self, *, clock=time.monotonic) -> None:  # noqa: ANN001
        self._clock = clock
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
        unavailable = getattr(error, "errno", None) in _UNAVAILABLE_ERRNOS or isinstance(
            error, (TimeoutError, ConnectionError),
        )
        self._unavailable_in_a_row = self._unavailable_in_a_row + 1 if unavailable else 0
