"""Per-record indexing enrichment counters.

Scoped with a ContextVar so concurrent tables/batches on the same record share
one counter object, while different event-loop tasks (other records) stay isolated.
"""

from __future__ import annotations

import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from collections.abc import Iterator
    from logging import Logger

#: Why a table or row fell back to plain ``column: value`` text.
#: ``count``: the model answered but left rows out. ``parse``: no usable answer. ``error``: the
#: call failed. ``budget``: the table's retry budget was spent before the rows could be retried.
DEGRADE_REASONS = ("count", "parse", "error", "budget")

_counters: ContextVar[Optional["IndexingEnrichmentCounters"]] = ContextVar(
    "indexing_enrichment_counters", default=None
)


@dataclass
class IndexingEnrichmentCounters:
    llm_calls: int = 0
    rate_limit_retries: int = 0
    tables_attempted: int = 0
    tables_succeeded: int = 0
    degraded_tables: int = 0
    degraded_rows: int = 0
    rows_over_row_budget: int = 0
    degraded_tables_by_reason: Counter = field(default_factory=Counter)
    degraded_rows_by_reason: Counter = field(default_factory=Counter)
    started_at: float = field(default_factory=time.perf_counter)

    def note_llm_call(self) -> None:
        self.llm_calls += 1

    def note_rate_limit_retry(self) -> None:
        self.rate_limit_retries += 1

    def note_table(
        self, *, succeeded: bool, degraded: bool = False, reason: Optional[str] = None
    ) -> None:
        self.tables_attempted += 1
        if succeeded:
            self.tables_succeeded += 1
        if degraded or not succeeded:
            self.degraded_tables += 1
            self.degraded_tables_by_reason[reason or "error"] += 1

    def note_rows_over_budget(self, rows: int) -> None:
        self.rows_over_row_budget += max(0, rows)

    def note_degraded_rows(self, reason: str, rows: int) -> None:
        if rows > 0:
            self.degraded_rows += rows
            self.degraded_rows_by_reason[reason] += rows

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000


def note_llm_call() -> None:
    counters = _counters.get()
    if counters is not None:
        counters.note_llm_call()


def note_rate_limit_retry() -> None:
    counters = _counters.get()
    if counters is not None:
        counters.note_rate_limit_retry()


def note_table_result(
    *, succeeded: bool, degraded: bool = False, reason: Optional[str] = None
) -> None:
    """Count one table. A degraded table is counted under *reason* (``error`` when not given)."""
    counters = _counters.get()
    if counters is not None:
        counters.note_table(succeeded=succeeded, degraded=degraded, reason=reason)


def note_rows_over_budget(rows: int) -> None:
    """Count *rows* rows given plain text because the record is over ``MAX_TABLE_ROWS_FOR_LLM``.

    That cutoff is configuration, so these rows are not degraded: they have their own count.
    """
    counters = _counters.get()
    if counters is not None:
        counters.note_rows_over_budget(rows)


def note_degraded_rows(reason: str, rows: int) -> None:
    """Count *rows* rows that got plain ``column: value`` text instead of a model description."""
    counters = _counters.get()
    if counters is not None:
        counters.note_degraded_rows(reason, rows)


def _by_reason(counts: Counter) -> str:
    return ",".join(f"{r}={counts[r]}" for r in sorted(counts)) or "-"


@contextmanager
def track_indexing_enrichment(
    logger: Logger, *, label: str = "record"
) -> Iterator[IndexingEnrichmentCounters]:
    """Accumulate enrichment stats for one record and log a one-line summary."""
    counters = IndexingEnrichmentCounters()
    token: Token = _counters.set(counters)
    try:
        yield counters
    finally:
        _counters.reset(token)
        if counters.tables_attempted or counters.llm_calls:
            logger.info(
                "Indexing enrichment (%s): llm_calls=%d rate_limit_retries=%d "
                "tables=%d/%d succeeded degraded=%d degraded_rows=%d rows_over_row_budget=%d "
                "degraded_tables_by_reason=%s degraded_rows_by_reason=%s elapsed_ms=%.0f",
                label,
                counters.llm_calls,
                counters.rate_limit_retries,
                counters.tables_succeeded,
                counters.tables_attempted,
                counters.degraded_tables,
                counters.degraded_rows,
                counters.rows_over_row_budget,
                _by_reason(counters.degraded_tables_by_reason),
                _by_reason(counters.degraded_rows_by_reason),
                counters.elapsed_ms,
            )
