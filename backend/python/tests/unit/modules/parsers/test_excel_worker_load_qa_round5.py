"""QA round 5 of the worker-load design (MEM-6b): the merged-range cap must count what openpyxl expands.

``_precheck`` sums ``mergeCell`` areas with ``range_boundaries`` after stripping ``$`` and spaces.
openpyxl's ``CellRange`` reads a ref containing ``!`` with ``range_to_tuple`` instead, and expands it,
while ``range_boundaries`` rejects it, so the precheck counts 0 for ``S!A1:Z200000``. A sheet part
encoded as UTF-16 (which openpyxl's XML parser reads) does not contain the ASCII bytes ``mergeCell``
that select a part for the merge scan. Either way 50 overlapping 5.2 M-position ranges (a 20 KB
sheet) are expanded in full: the CPU time bomb the round-5 cap was written to remove.
The expansion is replaced by a counter, so the tests are fast.
"""

from __future__ import annotations

import io
import time
import zipfile
from typing import TYPE_CHECKING

import pytest
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.parsers.excel import excel_parser as module
from app.modules.parsers.excel.workbook_model import (
    WorkbookRefused,
    load_workbook_model,
)
from tests.support.excel_workbooks import workbook_zip

if TYPE_CHECKING:
    from openpyxl.worksheet.merge import MergedCellRange

RANGES = 50
POSITIONS_PER_RANGE = 26 * 200_000
EXPANSION_BOUND = 3 * POSITIONS_PER_RANGE


def _merges(prefix: str, dollar: bool = False) -> str:
    def ref(i: int) -> str:
        a, b = (f"$A${1 + i}", f"$Z${200_000 + i}") if dollar else (f"A{1 + i}", f"Z{200_000 + i}")
        return f"{prefix}{a}:{b}"

    cells = "".join(f'<mergeCell ref="{ref(i)}"/>' for i in range(RANGES))
    return (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="{RANGES}">{cells}</mergeCells>'
    )


def _expanded_positions(data: bytes, monkeypatch: pytest.MonkeyPatch) -> int:
    expanded = [0]

    def count_instead_of_expand(_ws: Worksheet, mcr: MergedCellRange) -> None:
        expanded[0] += mcr.size["rows"] * mcr.size["columns"]

    monkeypatch.setattr(Worksheet, "_clean_merge_range", count_instead_of_expand)
    try:
        load_workbook_model(
            data,
            module.EXCEL_LOAD_MAX_BYTES,
            module.EXCEL_MAX_SHEET_XML_BYTES,
            module.EXCEL_MAX_TEXT_CHARS,
        )
    except WorkbookRefused as refused:
        assert refused.too_large
    return expanded[0]


@pytest.mark.parametrize("prefix", ["S!", "'S'!", "Sheet1!"])
def test_sheet_qualified_merge_refs_are_counted_like_plain_ones(
    prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanded = _expanded_positions(workbook_zip(_merges(prefix)), monkeypatch)
    assert expanded <= EXPANSION_BOUND, f"{expanded:,} positions expanded for {RANGES} overlapping ranges"


def test_sheet_qualified_absolute_merge_refs_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    expanded = _expanded_positions(workbook_zip(_merges("S!", dollar=True)), monkeypatch)
    assert expanded <= EXPANSION_BOUND, f"{expanded:,} positions expanded"


def _as_utf16(data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                text = body.decode().replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="UTF-16"?>')
                body = text.encode("utf-16")
            dst.writestr(info.filename, body)
    return out.getvalue()


def test_a_utf16_sheet_part_is_scanned_for_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _as_utf16(workbook_zip(_merges("")))
    expanded = _expanded_positions(data, monkeypatch)
    assert expanded <= EXPANSION_BOUND, f"{expanded:,} positions expanded from a UTF-16 sheet part"


def test_ordinary_merges_with_a_sheet_name_still_load() -> None:
    cells = "".join(f'<mergeCell ref="A{r}:C{r + 1}"/>' for r in range(2, 40, 2))
    body = f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData><mergeCells count="19">{cells}</mergeCells>'
    model = load_workbook_model(
        workbook_zip(body), module.EXCEL_LOAD_MAX_BYTES, module.EXCEL_MAX_SHEET_XML_BYTES, module.EXCEL_MAX_TEXT_CHARS
    )
    assert model.sheetnames


# ---------------------------------------------------------------------------
# Other openpyxl expansions with the same shape: work that grows with a product or a sum of declared
# areas while memory stays flat, so the worker's memory limit never trips and only the job timeout ends it.
# ---------------------------------------------------------------------------

LOAD_SECONDS = 3
SLACK_SECONDS = 30


def _refused_within_the_bound(body: str) -> None:
    # Per the owner's scope for round 6, no per-path cap: the load's CPU/time bound ends it.
    started = time.monotonic()
    with pytest.raises(WorkbookRefused) as refused:
        load_workbook_model(
            workbook_zip(body),
            module.EXCEL_LOAD_MAX_BYTES,
            module.EXCEL_MAX_SHEET_XML_BYTES,
            module.EXCEL_MAX_TEXT_CHARS,
            LOAD_SECONDS,
        )
    assert refused.value.too_large
    assert time.monotonic() - started < LOAD_SECONDS + SLACK_SECONDS


def test_hyperlinks_on_merged_cells_are_refused_within_the_time_bound() -> None:
    # openpyxl resolves a hyperlink on a merged cell by scanning every merged range
    # (WorksheetReader.normalize_merged_cell_link): H hyperlinks x M merges, quadratic, flat memory.
    # 20 000 x 20 000 (a 60 KB sheet part) would hold a worker for about 27 minutes.
    merges, links = 20_000, 20_000
    _refused_within_the_bound(
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="{merges}">' + "".join(f'<mergeCell ref="A{r}:B{r}"/>' for r in range(3, 3 + merges)) + "</mergeCells>"
        "<hyperlinks>" + "".join(f'<hyperlink ref="B{3 + (i % merges)}" location="S!A1"/>' for i in range(links)) + "</hyperlinks>"
    )


def test_overlapping_hyperlink_ranges_are_refused_within_the_time_bound() -> None:
    # openpyxl visits (and copies the hyperlink onto) every cell of every hyperlink range, overlaps
    # included; memory stays flat. 2000 ranges of 26 000 cells (a 120 KB sheet part) are 52 M visits.
    _refused_within_the_bound(
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        "<hyperlinks>" + "".join('<hyperlink ref="A1:Z1000" location="S!A1"/>' for _ in range(2000)) + "</hyperlinks>"
    )


def test_an_operator_who_lowers_the_heavy_parse_working_set_is_still_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    # GOVERNOR_HEAVY_PARSE_WORKING_SET_GB is a Helm / compose setting (values.yaml
    # governorHeavyParseWorkingSetGb). Lowering it admitted more heavy parses before the Excel
    # floor (max(setting, Excel peak)): with the Excel defaults (1.5 GiB) a setting of 0.5 or 1.0
    # is now ignored, so PDF/Docling-only deployments cannot raise their concurrency any more.
    from app.services.resource_governor import policy as policy_mod
    from tests.unit.services.resource_governor.test_policy import _snapshot

    monkeypatch.delenv("EXCEL_MAX_TEXT_MB", raising=False)
    monkeypatch.delenv("EXCEL_LOAD_MAX_MB", raising=False)
    snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=32 * 1024**3)
    monkeypatch.setattr(policy_mod, "HEAVY_PARSE_WORKING_SET_GB", 0.5)
    assert policy_mod.heavy_memory_cap(snap) == int(31 / 0.5)  # 32 GiB limit, 1 GiB resident
