"""QA round 4 of the worker-load design (MEM-6b): merged ranges that overlap are expanded once per range.

openpyxl turns every ``mergeCell`` into one ``MergedCell`` per position (``Worksheet._clean_merge_range``).
Overlapping ranges add no memory after the first, so the worker's memory limit never trips, while the
time grows with the sum of the areas: 50 ranges of 5.2 M positions (a 20 KB sheet part) held a worker at
100% CPU for over 15 minutes (a single such range is refused after about 130 s). The load must refuse
such a workbook from the declared areas, before it expands them, so the work is bounded.
The expansion is replaced here by a counter, so the test is fast with or without the fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openpyxl.worksheet.worksheet import Worksheet

from app.modules.parsers.excel import excel_parser as module
from app.modules.parsers.excel.workbook_model import (
    WorkbookRefused,
    load_workbook_model,
)
from tests.support.excel_workbooks import workbook_zip

if TYPE_CHECKING:
    import pytest
    from openpyxl.worksheet.merge import MergedCellRange

RANGES = 50
POSITIONS_PER_RANGE = 26 * 200_000
# what a bounded load may expand before it refuses: a couple of ranges, never the sum
EXPANSION_BOUND = 3 * POSITIONS_PER_RANGE


def _overlapping_merges() -> bytes:
    cells = "".join(
        f'<mergeCell ref="A{1 + i}:Z{200_000 + i}"/>' for i in range(RANGES)
    )
    body = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="{RANGES}">{cells}</mergeCells>'
    )
    return workbook_zip(body)


def test_many_overlapping_huge_merges_are_refused_before_they_are_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expanded = [0]

    def count_instead_of_expand(_ws: Worksheet, mcr: MergedCellRange) -> None:
        expanded[0] += mcr.size["rows"] * mcr.size["columns"]

    monkeypatch.setattr(Worksheet, "_clean_merge_range", count_instead_of_expand)
    try:
        load_workbook_model(
            _overlapping_merges(),
            module.EXCEL_LOAD_MAX_BYTES,
            module.EXCEL_MAX_SHEET_XML_BYTES,
            module.EXCEL_MAX_TEXT_CHARS,
        )
    except WorkbookRefused as refused:
        assert refused.too_large
    assert expanded[0] <= EXPANSION_BOUND, (
        f"{expanded[0]:,} positions expanded for {RANGES} overlapping ranges"
    )


def test_ordinary_merged_ranges_still_load() -> None:
    cells = "".join(f'<mergeCell ref="A{r}:C{r + 1}"/>' for r in range(2, 400, 2))
    body = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="199">{cells}</mergeCells>'
    )
    model = load_workbook_model(
        workbook_zip(body),
        module.EXCEL_LOAD_MAX_BYTES,
        module.EXCEL_MAX_SHEET_XML_BYTES,
        module.EXCEL_MAX_TEXT_CHARS,
    )
    assert model.sheetnames
