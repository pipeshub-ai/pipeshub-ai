"""Waiting for PipesHub to finish indexing an ingested corpus.

Statuses are counted client-side from the full record listing: the
connector's `indexing_status` query filter is declared as a list without
`Query()` and is ignored. FAILED records get exactly one reindex.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable

from benchmarks.harness.errors import IndexTimeoutError
from benchmarks.harness.models import IndexReport, IngestManifest

logger = logging.getLogger(__name__)

SUCCESS_STATUS = "COMPLETED"
RETRY_STATUS = "FAILED"
TERMINAL_STATUSES = frozenset({
    "COMPLETED", "FAILED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY", "AUTO_INDEX_OFF", "ENABLE_MULTIMODAL_MODELS",
})


class IndexWaiter:
    def __init__(
        self,
        list_statuses: Callable[[str], dict[str, str]],
        reindex: Callable[[str], None],
        *,
        poll_interval_s: float,
        timeout_s: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._list_statuses = list_statuses
        self._reindex = reindex
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._clock = clock

    def wait(self, ingest: IngestManifest, gold_refs: set[str]) -> IndexReport:
        started = self._clock()
        deadline = started + self._timeout_s
        record_ids = [r.record_id for r in ingest.records]
        reindexed: set[str] = set()
        while True:
            statuses = self._list_statuses(ingest.kb_id)
            retry_now = [rid for rid in record_ids if statuses.get(rid) == RETRY_STATUS and rid not in reindexed]
            for rid in retry_now:
                self._reindex(rid)
                reindexed.add(rid)
            pending = [rid for rid in record_ids if statuses.get(rid) not in TERMINAL_STATUSES or rid in retry_now]
            if not pending:
                return self._report(ingest, statuses, gold_refs, reindexed, self._clock() - started)
            if self._clock() >= deadline:
                raise IndexTimeoutError(f"{len(pending)}/{len(record_ids)} records still indexing after {self._timeout_s:.0f}s")
            logger.info("indexing: %d/%d records pending", len(pending), len(record_ids))
            self._sleep(self._poll_interval_s)

    @staticmethod
    def _report(
        ingest: IngestManifest, statuses: dict[str, str], gold_refs: set[str], reindexed: set[str], elapsed_s: float,
    ) -> IndexReport:
        by_url = {r.canonical_url: statuses.get(r.record_id, "MISSING") for r in ingest.records}
        indexed_gold = [url for url in gold_refs if by_url.get(url) == SUCCESS_STATUS]
        return IndexReport(
            total=len(ingest.records),
            status_counts=dict(Counter(statuses.get(r.record_id, "MISSING") for r in ingest.records)),
            gold_total=len(gold_refs),
            gold_indexed=len(indexed_gold),
            reindexed=sorted(reindexed),
            unindexed_urls=sorted(set(gold_refs) - set(indexed_gold)),
            elapsed_s=round(elapsed_s, 1),
        )
