"""Tests for per-record indexing enrichment counters."""

import logging

from app.utils import indexing_metrics
from app.utils.indexing_metrics import (
    note_llm_call,
    note_rate_limit_retry,
    note_table_result,
    track_indexing_enrichment,
)


def test_track_indexing_enrichment_accumulates_and_clears():
    logger = logging.getLogger("test_indexing_metrics")
    with track_indexing_enrichment(logger, label="unit") as counters:
        note_llm_call()
        note_llm_call()
        note_rate_limit_retry()
        note_table_result(succeeded=True)
        note_table_result(succeeded=False, degraded=True)
        assert counters.llm_calls == 2
        assert counters.rate_limit_retries == 1
        assert counters.tables_attempted == 2
        assert counters.tables_succeeded == 1
        assert counters.degraded_tables == 1
        assert indexing_metrics._counters.get() is counters

    assert indexing_metrics._counters.get() is None


def test_note_helpers_noop_without_active_context():
    note_llm_call()
    note_rate_limit_retry()
    note_table_result(succeeded=False, degraded=True)
    assert indexing_metrics._counters.get() is None
