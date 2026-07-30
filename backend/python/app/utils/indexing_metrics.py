"""Per-record indexing enrichment counters.

Scoped with a ContextVar so concurrent tables/batches on the same record share
one counter object, while different event-loop tasks (other records) stay isolated.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from logging import Logger
from typing import Iterator, Optional

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
    started_at: float = field(default_factory=time.perf_counter)

    def note_llm_call(self) -> None:
        self.llm_calls += 1

    def note_rate_limit_retry(self) -> None:
        self.rate_limit_retries += 1

    def note_table(self, *, succeeded: bool, degraded: bool = False) -> None:
        self.tables_attempted += 1
        if succeeded:
            self.tables_succeeded += 1
        if degraded or not succeeded:
            self.degraded_tables += 1

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


def note_table_result(*, succeeded: bool, degraded: bool = False) -> None:
    counters = _counters.get()
    if counters is not None:
        counters.note_table(succeeded=succeeded, degraded=degraded)


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
                "tables=%d/%d succeeded degraded=%d elapsed_ms=%.0f",
                label,
                counters.llm_calls,
                counters.rate_limit_retries,
                counters.tables_succeeded,
                counters.tables_attempted,
                counters.degraded_tables,
                counters.elapsed_ms,
            )
