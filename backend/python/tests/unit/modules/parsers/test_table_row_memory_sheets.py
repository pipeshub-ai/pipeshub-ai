"""CSV and Excel tables keep the text of the rows the stored version already described, as PDF tables do.

The parsers run over their real row path (``get_rows_text`` → ``describe_row_dicts``) with a fake model that words every row in
its own style ("A: ..." or "B: ..."), so a row that keeps its text is one that was not sent again. All data is synthetic.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import BlocksContainer, BlockType
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
from app.utils.table_row_memory import (
    ROW_DIGEST_KEY,
    PreviousRows,
    dict_row_digest,
    end_previous_rows,
    offer_previous_rows,
    row_digest,
)
from tests.support.table_model import FakeTableModel, by_first_row, make_rows

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

LOG = logging.getLogger("test-table-row-memory-sheets")
KEYS = ["Name", "Offset", "Reset"]
ROWS = 30
EDITED = 12  # the data row whose cell an edit changes


@pytest.fixture(autouse=True)
def _pinned(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", "50")
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "1000")
    end_previous_rows()
    yield
    end_previous_rows()


def _grid(*, edited: bool = False, count: int = ROWS) -> list[list[str]]:
    rows = [list(r) for r in make_rows(count)]
    if edited:
        rows[EDITED][1] = "0xFFF"
    return rows


def _model(style: str, **kw: object) -> FakeTableModel:
    return FakeTableModel(describe=lambda rid: f"{style}: {rid}", **kw)  # type: ignore[arg-type]


async def _csv(model: FakeTableModel, *grids: list[list[str]]) -> BlocksContainer:
    parser = CSVParser(MagicMock())
    parser.detect_headers_with_llm = AsyncMock(
        return_value=CSVHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="first row")
    )
    parser.get_table_summary = AsyncMock(return_value="Register map")
    tables = [{"raw_rows": [KEYS, *grid], "start_row": 1, "end_row": len(grid) + 1} for grid in grids]
    return await parser.get_blocks_from_csv_with_multiple_tables(tables, model)


async def _excel(model: FakeTableModel, *grids: list[list[str]]) -> BlocksContainer:
    parser = ExcelParser(LOG, MagicMock())
    parser.workbook = MagicMock(sheetnames=["Sheet1"])
    tables = [
        {
            "headers": KEYS,
            "data": [
                [{"header": key, "value": value, "row": i + 2} for key, value in zip(KEYS, row, strict=True)]
                for i, row in enumerate(grid)
            ],
            "start_row": 1, "start_col": 1, "end_row": len(grid) + 1, "end_col": len(KEYS),
        }
        for grid in grids
    ]
    parser.get_tables_in_sheet = AsyncMock(return_value=tables)
    parser.get_table_summary = AsyncMock(return_value="Register map")
    return await parser.get_blocks_from_workbook(model)


PARSERS = pytest.mark.parametrize("parse", [_csv, _excel], ids=["csv", "excel"])


def _rows(container: BlocksContainer) -> list[dict]:
    return [b.data for b in container.blocks if b.type == BlockType.TABLE_ROW]


def _texts(container: BlocksContainer) -> list[str]:
    return [d["row_natural_language_text"] for d in _rows(container)]


def _offer(container: BlocksContainer | None) -> AsyncMock:
    load = AsyncMock(return_value=container)
    offer_previous_rows(PreviousRows(load, LOG, "rec-1"))
    return load


def test_a_row_dict_digest_reads_its_keys_as_the_header_and_its_values_as_text() -> None:
    when = datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.timezone.utc)
    assert dict_row_digest({"At": when, "Note": None, "N": 7}) == row_digest(["At", "Note", "N"], [when.isoformat(), "", "7"])
    assert dict_row_digest({"A": "1", "B": "2"}) != dict_row_digest({"B": "2", "A": "1"})
    # An Excel header can be a number (a "2024" column): read as its text, like a cell.
    assert dict_row_digest({2024: 5, "Region": "EU"}) == row_digest(["2024", "Region"], ["5", "EU"])


@PARSERS
@pytest.mark.asyncio
async def test_a_first_index_describes_every_row_and_records_its_digest(
    parse: Callable[..., Awaitable[BlocksContainer]],
) -> None:
    model = _model("A")
    first = await parse(model, _grid())
    assert model.row_calls == [ROWS]
    assert _texts(first) == [f"A: REG{i}" for i in range(ROWS)]
    assert all(d.get(ROW_DIGEST_KEY) for d in _rows(first))


@PARSERS
@pytest.mark.asyncio
async def test_after_a_one_cell_edit_only_the_edited_row_is_described_again(
    parse: Callable[..., Awaitable[BlocksContainer]],
) -> None:
    # Kills: CSV and Excel describing the whole table again whenever one cell changes.
    first = await parse(_model("A"), _grid())
    _offer(first)
    model = _model("B")
    second = await parse(model, _grid(edited=True))

    assert model.row_calls == [1]
    expected = [f"A: REG{i}" for i in range(ROWS)]
    expected[EDITED] = f"B: REG{EDITED}"
    assert _texts(second) == expected
    assert [d["row_number"] for d in _rows(second)] == [d["row_number"] for d in _rows(first)]
    assert all(d.get(ROW_DIGEST_KEY) for d in _rows(second))


@PARSERS
@pytest.mark.asyncio
async def test_an_unchanged_table_sends_no_row(parse: Callable[..., Awaitable[BlocksContainer]]) -> None:
    first = await parse(_model("A"), _grid())
    _offer(first)
    model = _model("B")
    second = await parse(model, _grid())
    assert model.row_calls == []
    assert _rows(second) == _rows(first)


@PARSERS
@pytest.mark.asyncio
async def test_a_row_that_fell_back_to_plain_text_has_no_digest_and_is_described_next_time(
    parse: Callable[..., Awaitable[BlocksContainer]],
) -> None:
    # Kills: a plain row (the model left it out) kept for ever, never described again.
    first = await parse(_model("A", behave=by_first_row("REG0", "skip:3")), _grid())
    plain = _rows(first)[3]
    assert not plain["row_natural_language_text"].startswith("A: ") and ROW_DIGEST_KEY not in plain
    _offer(first)
    model = _model("B")
    second = await parse(model, _grid())
    assert model.row_calls == [1]
    assert _texts(second)[3] == "B: REG3"


@PARSERS
@pytest.mark.asyncio
async def test_each_table_keeps_its_own_wording_of_an_equal_row(parse: Callable[..., Awaitable[BlocksContainer]]) -> None:
    # Kills: one record-wide match, where the first table's wording of an equal row replaces the second table's.
    other = [_grid()[0], *[[f"OTHER{i}", "0x1", "0"] for i in range(10)]]
    first = await parse(_model("A", identify=lambda row: str(row.get("Name") or "HDR")), _grid(count=5))
    stored_other = await parse(_model("C", identify=lambda row: str(row.get("Name") or "HDR")), other)
    _offer(BlocksContainer(
        blocks=[*first.blocks, *[b.model_copy(update={"parent_index": 99}) for b in stored_other.blocks]], block_groups=[],
    ))
    edited = [list(r) for r in other]
    edited[4][1] = "0xFFF"
    model = _model("B", identify=lambda row: str(row.get("Name") or "HDR"))
    second = await parse(model, edited)
    assert model.row_calls == [1]
    assert _texts(second)[0] == "C: REG0"


@PARSERS
@pytest.mark.asyncio
async def test_a_stored_record_that_cannot_be_read_describes_every_row(
    parse: Callable[..., Awaitable[BlocksContainer]],
) -> None:
    offer_previous_rows(PreviousRows(AsyncMock(side_effect=OSError("down")), MagicMock(), "rec-1"))
    model = _model("B")
    await parse(model, _grid(edited=True))
    assert model.row_calls == [ROWS]


@PARSERS
@pytest.mark.asyncio
async def test_rows_over_the_row_budget_read_nothing(
    parse: Callable[..., Awaitable[BlocksContainer]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = _offer(await parse(_model("A"), _grid()))
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "10")
    second = await parse(_model("B"), _grid(edited=True))
    load.assert_not_awaited()
    assert not any(ROW_DIGEST_KEY in d for d in _rows(second))
