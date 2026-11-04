"""Round 5 of the worker-load design (MEM-6b): merged ranges are bounded from their declared areas."""

from __future__ import annotations

import time

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.modules.parsers.excel.workbook_model import (
    CELL_DICT_BYTES,
    OUTPUT_BUDGET_FACTOR,
    WorkbookModel,
    WorkbookRefused,
    _range_area,
    load_workbook_model,
    max_merged_positions,
)
from tests.support.excel_workbooks import workbook_zip

TEXT_BYTES = 3_686_667  # a merged-position cap of exactly 10 000
CAP = 10_000


def _workbook(refs: list[str]) -> bytes:
    cells = "".join(f'<mergeCell ref="{ref}"/>' for ref in refs)
    body = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="{len(refs)}">{cells}</mergeCells>'
    )
    return workbook_zip(body)


def _load(refs: list[str]) -> WorkbookModel:
    return load_workbook_model(_workbook(refs), module.EXCEL_LOAD_MAX_BYTES, module.EXCEL_MAX_SHEET_XML_BYTES, TEXT_BYTES)


def test_the_cap_is_derived_from_the_cell_charge_and_the_output_budget() -> None:
    assert max_merged_positions(TEXT_BYTES) == CAP
    assert max_merged_positions(module.EXCEL_MAX_TEXT_CHARS) == OUTPUT_BUDGET_FACTOR * module.EXCEL_MAX_TEXT_CHARS // CELL_DICT_BYTES
    assert module._CELL_DICT_BYTES == CELL_DICT_BYTES


@pytest.mark.parametrize(
    ("ref", "area"),
    [
        ("A1:B2", 4), ("C3", 1), ("$A$1:$B$2", 4), ("a1:b2", 4), ("S!A1:B2", 4), ("'S'!A1:Z9", 26 * 9),
        # refs openpyxl's CellRange rejects raise there and expand nothing
        ("A1 : B2", 0), ("B2:A1", 0), ("A:B", 0), ("1:2", 0), ("S!A:B", 0), ("junk", 0), ("", 0),
    ],
)
def test_range_area_counts_what_openpyxl_expands(ref: str, area: int) -> None:
    assert _range_area(ref) == area


def test_many_small_merges_just_under_the_cap_load() -> None:
    refs = [f"A{r}:B{r}" for r in range(2, 2 + CAP // 2)]  # exactly CAP positions
    assert _load(refs).sheetnames


def test_many_small_merges_just_over_the_cap_are_refused_fast() -> None:
    refs = [f"A{r}:B{r}" for r in range(2, 2 + CAP // 2 + 1)]  # CAP + 2 positions
    started = time.monotonic()
    with pytest.raises(WorkbookRefused) as raised:
        _load(refs)
    assert raised.value.too_large
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("spelling", ["$A$1:$Z$1000", "a1:z1000", "A1:Z1000", "S!A1:Z1000", "'S'!$A$1:$Z$1000"])
def test_one_huge_merge_is_refused_in_every_spelling(spelling: str) -> None:
    with pytest.raises(WorkbookRefused) as raised:
        _load([spelling])
    assert raised.value.too_large


def test_many_small_tables_are_charged_with_headroom_for_the_resident_overhead(monkeypatch: pytest.MonkeyPatch) -> None:
    # tracemalloc counts live objects; the resident set grew 1.12x the charge at 60 000 tables, so the
    # charge must clear the allocation by at least that much (measured: 1.32x with the round-5 constants)
    from tests.unit.modules.parsers.test_excel_worker_load_qa_round3 import (
        _allocated_and_charged,
        _many_small_tables,
    )

    allocated, charged, _ = _allocated_and_charged(_many_small_tables(), monkeypatch)
    assert charged >= 1.2 * allocated, f"charged {charged / 1e6:.1f} MB for {allocated / 1e6:.1f} MB allocated"
