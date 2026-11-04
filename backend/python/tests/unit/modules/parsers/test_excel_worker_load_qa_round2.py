"""QA round 2 of the worker-load design (MEM-6b): text the parent builds that the text bound does not count.

The worker bounds the characters of the string cells (a shared string once per use). Two inputs
make the parent hold far more text than that: a header repeated into the text of every row, and a
number-format literal rendered into every date cell. Each test sets EXCEL_MAX_TEXT_CHARS small and
accepts either a refusal at load or a result that stays inside that many characters.
"""

from __future__ import annotations

import asyncio
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import MAIN_NS, load, workbook_zip

LIMIT = 5_000_000  # characters


def _header_workbook(header_chars: int, rows: int) -> bytes:
    sst = (
        f'<?xml version="1.0"?><sst xmlns="{MAIN_NS}"><si><t>{"h" * header_chars}</t></si></sst>'
    )
    body = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>' + "".join(
        f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(2, rows + 2)
    )
    return workbook_zip(f"<sheetData>{body}</sheetData>", extra={"xl/sharedStrings.xml": sst})


def _date_workbook(literal_chars: int, rows: int) -> bytes:
    styles = (
        f'<?xml version="1.0"?><styleSheet xmlns="{MAIN_NS}"><numFmts count="1">'
        f'<numFmt numFmtId="164" formatCode="yyyy-mm-dd&quot;{"L" * literal_chars}&quot;"/></numFmts>'
        '<fonts count="1"><font/></fonts><fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf/>'
        '<xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>'
    )
    body = '<row r="1"><c r="A1" t="inlineStr"><is><t>h</t></is></c></row>' + "".join(
        f'<row r="{i}"><c r="A{i}" s="1"><v>45000</v></c></row>' for i in range(2, rows + 2)
    )
    return workbook_zip(f"<sheetData>{body}</sheetData>", extra={"xl/styles.xml": styles})


def test_a_header_is_not_repeated_into_every_row_past_the_text_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 50 000 character header over 400 one-cell rows: 50 KB of cell text, 20 M characters of
    # row text ("<header>: <value>" per row) in the blocks, four times the bound set below.
    monkeypatch.setattr(module, "EXCEL_MAX_TEXT_CHARS", LIMIT)
    data = _header_workbook(50_000, 400)
    try:
        parser = load(data)
    except ParseError as refused:
        assert refused.message == user_errors.EXCEL_TOO_LARGE
        return
    container = parser._build_basic_block_container()
    held = sum(len(b.data["row_natural_language_text"]) for b in container.blocks)
    assert held <= LIMIT, f"{held} characters of row text from a workbook bounded at {LIMIT}"


@pytest.mark.asyncio
async def test_a_number_format_literal_is_not_rendered_into_every_date_cell_past_the_text_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 20 000 character literal in the number format of 1000 date cells: the indexing path renders
    # each date through the format, 20 M characters held while the tables are built.
    monkeypatch.setattr(module, "EXCEL_MAX_TEXT_CHARS", LIMIT)
    try:
        parser = load(_date_workbook(20_000, 1000))
    except ParseError as refused:
        assert refused.message == user_errors.EXCEL_TOO_LARGE
        return
    parser.detect_excel_headers_with_llm = AsyncMock(
        return_value=SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high")
    )
    parser.get_table_summary = AsyncMock(return_value="s")
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "0")
    tracemalloc.start()
    try:
        await asyncio.wait_for(parser.get_blocks_from_workbook(MagicMock()), 120)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 2 * LIMIT, f"{peak} bytes allocated building tables for a workbook bounded at {LIMIT} characters"


def test_a_text_bound_in_characters_is_not_four_times_as_loose_for_wide_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # EXCEL_MAX_TEXT_MB counts characters, but a str of astral characters (emoji, rarer CJK) takes four
    # bytes each, and the blocks hold a copy per use. 4.9 M such characters, a hair under the bound,
    # cost the parent about four times what 4.9 M ASCII characters do.
    monkeypatch.setattr(module, "EXCEL_MAX_TEXT_CHARS", LIMIT)
    width, uses = 50_000, 98
    sst = f'<?xml version="1.0"?><sst xmlns="{MAIN_NS}"><si><t>{"\U0001f600" * width}</t></si></sst>'
    body = '<row r="1"><c r="A1" t="inlineStr"><is><t>k</t></is></c></row>' + "".join(
        f'<row r="{i}"><c r="A{i}" t="s"><v>0</v></c></row>' for i in range(2, uses + 2)
    )
    data = workbook_zip(f"<sheetData>{body}</sheetData>", extra={"xl/sharedStrings.xml": sst})
    try:
        parser = load(data)
    except ParseError as refused:
        assert refused.message == user_errors.EXCEL_TOO_LARGE
        return
    tracemalloc.start()
    try:
        parser._build_basic_block_container()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 3 * LIMIT, f"{peak} bytes for a workbook bounded at {LIMIT} characters"


def test_a_load_that_peaked_near_the_allowance_between_two_samples_is_still_called_too_large() -> None:
    # openpyxl turns some MemoryErrors into other exceptions after the memory is given back. The growth
    # watcher samples every 20 ms, so a peak that is allocated and freed between two samples (a C
    # call that builds a buffer and drops it) is not seen, and the load's error is reported as the
    # file's own instead of EXCEL_TOO_LARGE. Held for a few milliseconds the same peak is seen.
    from tests.support.excel_worker_jobs import spike_then_fail

    mb = 1024 * 1024
    outcomes = [parse_pool.run_blocking(spike_then_fail, b"x", 400 * mb, 400 * mb, 0.0) for _ in range(5)]
    assert outcomes == ["refused:too_large"] * 5, outcomes
