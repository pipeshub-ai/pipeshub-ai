"""One LLM row budget per record, shared by every table parser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.table_enrichment import (
    TableEnrichmentResult,
    describe_rows_within_budget,
    enrich_tables,
)


def test_budget_is_a_running_total_in_document_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "100")
    assert describe_rows_within_budget([40, 50, 20, 5]) == [True, True, False, False]


def test_a_single_table_over_the_budget_gets_no_llm_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "1000")
    assert describe_rows_within_budget([10_000]) == [False]


@pytest.mark.asyncio
async def test_enrich_tables_applies_the_budget_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "3")
    grids = [[["a"], ["b"]], [["c"], ["d"]]]
    enrich_one = AsyncMock(return_value=TableEnrichmentResult())
    with patch("app.utils.table_enrichment.enrich_table_grid", enrich_one):
        await enrich_tables(MagicMock(), grids, logger=MagicMock())
    assert [call.kwargs["describe_rows"] for call in enrich_one.await_args_list] == [True, False]
