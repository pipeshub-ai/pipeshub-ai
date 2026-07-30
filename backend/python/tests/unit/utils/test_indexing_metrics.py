"""Tests for per-record indexing enrichment counters."""

import logging

from app.utils.indexing_metrics import (
    current_enrichment_counters,
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
        assert current_enrichment_counters() is counters

    assert current_enrichment_counters() is None


def test_note_helpers_noop_without_active_context():
    note_llm_call()
    note_rate_limit_retry()
    note_table_result(succeeded=False, degraded=True)
    assert current_enrichment_counters() is None


def test_llm_row_budget_respects_already():
    from app.utils.table_enrichment import llm_row_budget

    # Cumulative after each table must stay <= limit (same as the old mutable counter).
    assert llm_row_budget([50, 900], 1000) == [True, True]
    assert llm_row_budget([50, 951], 1000) == [True, False]
    assert llm_row_budget([50, 900], 1000, already=100) == [True, False]
    assert llm_row_budget([50, 50], 1000, already=950) == [True, False]
    assert llm_row_budget([50], 1000, already=960) == [False]
