"""Table summaries, headers and row descriptions of a record are paid for once."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.prompt_template import (
    CSVHeaderDetection,
    ExcelHeaderDetection,
    RowDescriptions,
    TableEnrichment,
    TableHeaders,
)
from app.services.paid_cache.access import set_ambient_cache
from app.services.paid_cache.cache import PaidResultCache
from app.utils import table_enrichment
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, REC = "org-1", "rec-1"
LOG = logging.getLogger("test-table-cache")
GRID = [["name", "qty"], ["bolt", "10"], ["nut", "20"], ["washer", "30"]]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr("app.services.paid_cache.access.paid_cache_for", lambda *_a, **_k: cache)
    set_ambient_cache(cache)
    yield memory
    set_ambient_cache(None)


class _Calls:
    def __init__(self) -> None:
        self.n = 0


def _patch_first_call(monkeypatch: pytest.MonkeyPatch, calls: _Calls, *, count: int = 3, **extra: Any) -> None:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    async def fake(llm: object, messages: list, model: type, field: str, expected: int, *_a: object) -> TableEnrichment:
        calls.n += 1
        return TableEnrichment(summary="Parts", headers=["name", "qty"], header_row_count=0,
                               descriptions=["d"] * count, **extra)

    monkeypatch.setattr(table_enrichment, "invoke_with_count_validation_and_reflection", fake)


async def test_a_tables_first_call_is_reused_by_a_retry(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _Calls()
    _patch_first_call(monkeypatch, calls)
    llm = CountingModel("indexing")
    with record_in_flight(ORG, REC):
        first = await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)
        second = await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)

    assert calls.n == 1
    assert second == first and first.summary == "Parts"
    assert [p.split("/")[0] for p in store.results(ORG, REC)] == ["table_summary"]


async def test_another_tables_rows_or_another_record_pay_for_their_own(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _Calls()
    _patch_first_call(monkeypatch, calls)
    llm = CountingModel("indexing")
    with record_in_flight(ORG, REC):
        await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)
        await table_enrichment.enrich_table_grid(llm, [*GRID[:3], ["gasket", "40"]], logger=LOG, known_header_row_count=1)
    with record_in_flight(ORG, "rec-2"):
        await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)

    assert calls.n == 3


async def test_a_degraded_answer_is_not_stored(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _Calls()
    _patch_first_call(monkeypatch, calls, count=1)      # fewer descriptions than rows: the count check fails
    llm = CountingModel("indexing")
    with record_in_flight(ORG, REC):
        await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)
        await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)

    assert calls.n == 2
    assert store.results(ORG, REC) == []


async def test_a_failed_call_is_not_stored_and_is_paid_again(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = _Calls()

    async def failing(*_a: object, **_k: object) -> TableEnrichment:
        attempts.n += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(table_enrichment, "invoke_with_count_validation_and_reflection", failing)
    llm = CountingModel("indexing")
    with record_in_flight(ORG, REC):
        for _ in range(2):
            result = await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)
            assert result.degraded

    assert attempts.n == 2 and store.results(ORG, REC) == []


async def test_row_batches_are_cached_one_by_one(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _Calls()

    async def rows(llm: object, messages: list, model: type) -> RowDescriptions:
        calls.n += 1
        return RowDescriptions(descriptions=["row"] * (len(messages[-1].content.split("\n")) and 2))

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", rows)
    llm = CountingModel("indexing")
    table = {"grid": [["a", "b"], ["1", "2"], ["3", "4"]]}
    with record_in_flight(ORG, REC):
        first = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])
        second = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])

    assert calls.n == 1 and first[0] == second[0]
    assert [p.split("/")[0] for p in store.results(ORG, REC)] == ["table_rows"]


async def test_without_a_record_in_flight_nothing_is_cached(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _Calls()
    _patch_first_call(monkeypatch, calls)
    llm = CountingModel("indexing")
    await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)
    await table_enrichment.enrich_table_grid(llm, GRID, logger=LOG, known_header_row_count=1)

    assert calls.n == 2 and store.files == {}


# -- the CSV and Excel parsers' own calls --------------------------------------------------------------------


def _csv() -> CSVParser:
    return CSVParser(MagicMock())


def _excel(llm: CountingModel) -> ExcelParser:
    parser = ExcelParser(LOG, MagicMock())
    parser.llm = llm  # type: ignore[assignment]
    return parser


async def test_the_csv_summary_is_paid_once_per_record(store: MemoryResultStore) -> None:
    llm = CountingModel("indexing", reply="Parts list")
    rows = [{"name": "bolt", "qty": 10}, {"name": "nut", "qty": 20}]
    parser = _csv()
    with record_in_flight(ORG, REC):
        assert await parser.get_table_summary(llm, rows) == "Parts list"
        assert await parser.get_table_summary(llm, rows) == "Parts list"
    with record_in_flight(ORG, "rec-2"):
        await parser.get_table_summary(llm, rows)

    assert llm.total == 2
    assert [p.split("/")[0] for p in store.results(ORG, REC)] == ["table_summary"]


@pytest.mark.parametrize("truncation", [{"finish_reason": "length"}, {"stop_reason": "max_tokens"}])
async def test_a_truncated_or_empty_summary_is_not_stored(store: MemoryResultStore, truncation: dict[str, str]) -> None:
    rows = [{"name": "bolt", "qty": 10}]
    parser = _csv()
    with record_in_flight(ORG, REC):
        await parser.get_table_summary(CountingModel("indexing", reply="Cut o", metadata=truncation), rows)
        await parser.get_table_summary(CountingModel("indexing", reply="   "), rows)

    assert store.results(ORG, REC) == []


async def test_the_excel_summary_is_paid_once_per_record(store: MemoryResultStore) -> None:
    llm = CountingModel("indexing", reply="A parts sheet")
    table = {"headers": ["name", "qty"], "data": [[{"header": "name", "value": "bolt"}, {"header": "qty", "value": 10}]]}
    parser = _excel(llm)
    with record_in_flight(ORG, REC):
        await parser.get_table_summary(table)
        assert await parser.get_table_summary(table) == "A parts sheet"

    assert llm.total == 1


@pytest.mark.parametrize("truncation", [{"finish_reason": "length"}, {"stop_reason": "max_tokens"}])
async def test_a_truncated_excel_summary_is_not_stored(store: MemoryResultStore, truncation: dict[str, str]) -> None:
    """The Excel parser has its own truncation check (QA-S7a-7); a cut summary is paid again, never served."""
    table = {"headers": ["name", "qty"], "data": [[{"header": "name", "value": "bolt"}, {"header": "qty", "value": 10}]]}
    parser = _excel(CountingModel("indexing", reply="Cut o", metadata=truncation))
    with record_in_flight(ORG, REC):
        assert await parser.get_table_summary(table) == "Cut o"        # the caller still gets it

    assert store.results(ORG, REC) == []


async def test_a_stored_value_that_no_longer_decodes_is_a_miss_that_is_replaced(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-S7a-3 (probe q6): a schema change without a kind-version bump must not raise at the call site."""
    import json

    from app.services.paid_cache.keys import KIND_TABLE_ROWS

    calls = _Calls()

    async def rows(llm: object, messages: list, model: type) -> RowDescriptions:
        calls.n += 1
        return RowDescriptions(descriptions=["row one", "row two"])

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", rows)
    llm = CountingModel("indexing")
    table = {"grid": [["a", "b"], ["1", "2"], ["3", "4"]]}
    with record_in_flight(ORG, REC):
        await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])
        (path,) = store.results(ORG, REC)
        store.files[(ORG, REC, path)] = (
            json.dumps({"v": 1, "kind": KIND_TABLE_ROWS, "value": {"descriptions": "x"}}).encode(), store.now_ms,
        )
        second = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])
        third = await table_enrichment.get_rows_text(llm, table, "A table", ["a", "b"])

    assert calls.n == 2                      # the bad object was a miss, paid once, and replaced
    assert second == third
    assert json.loads(store.files[(ORG, REC, path)][0])["value"]["descriptions"] == ["row one", "row two"]


async def test_csv_header_detection_and_generation_are_cached(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.csv import csv_parser

    detections, generations = _Calls(), _Calls()

    async def structured(llm: object, messages: list, model: type) -> object:
        if model is CSVHeaderDetection:
            detections.n += 1
            return CSVHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="first row")
        generations.n += 1
        return TableHeaders(headers=["a", "b"])

    monkeypatch.setattr(csv_parser, "invoke_with_structured_output_and_reflection", structured)
    llm = CountingModel("indexing")
    parser = _csv()
    with record_in_flight(ORG, REC):
        for _ in range(2):
            await parser.detect_headers_with_llm([["name", "qty"], ["bolt", "10"]], llm)
            assert await parser.generate_headers_with_llm([["bolt", "10"], ["nut", "20"]], 2, llm) == ["a", "b"]

    assert (detections.n, generations.n) == (1, 1)
    assert sorted(p.split("/")[0] for p in store.results(ORG, REC)) == ["excel_headers", "excel_headers"]


async def test_a_header_list_of_the_wrong_length_is_not_stored(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.csv import csv_parser

    async def structured(llm: object, messages: list, model: type) -> object:
        return TableHeaders(headers=["only-one"])

    monkeypatch.setattr(csv_parser, "invoke_with_structured_output_and_reflection", structured)
    with record_in_flight(ORG, REC):
        await _csv().generate_headers_with_llm([["bolt", "10"]], 2, CountingModel("indexing"))

    assert store.results(ORG, REC) == []


async def test_excel_header_detection_is_cached(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers.excel import excel_parser

    calls = _Calls()

    async def structured(llm: object, messages: list, model: type) -> object:
        calls.n += 1
        return ExcelHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="top row")

    monkeypatch.setattr(excel_parser, "invoke_with_structured_output_and_reflection", structured)
    llm = CountingModel("indexing")
    parser = _excel(llm)
    with record_in_flight(ORG, REC):
        for _ in range(2):
            await parser.detect_excel_headers_with_llm([["name", "qty"], ["bolt", 10]], llm)

    assert calls.n == 1
    assert [p.split("/")[0] for p in store.results(ORG, REC)] == ["excel_headers"]
