"""QA round 3 of the worker-load design (MEM-6b): structures the output budget does not charge.

The budget charges text, cell dicts, row dicts, blocks and groups. A sheet of many small tables also
makes the parser build, per table, a table dict, its row lists, a location dict, a processed-table
dict and the visited-range tuple, none of which is charged. The test compares what the build really
allocates (tracemalloc peak) with what it charged at its high-water mark; a calibrated budget never
charges less than is allocated.
"""

from __future__ import annotations

import asyncio
import logging
import tracemalloc
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.modules.parsers.excel import excel_parser as module
from tests.support.excel_workbooks import load, workbook_zip

TABLES = 1500


def _many_small_tables() -> bytes:
    # header + two data rows + a blank row, TABLES times, in one sheet
    rows: list[str] = []
    r = 1
    for _ in range(TABLES):
        rows.append(
            f'<row r="{r}">'
            + "".join(f'<c r="{c}{r}" t="inlineStr"><is><t>h{c}</t></is></c>' for c in "ABCD")
            + "</row>"
        )
        rows.extend(
            f'<row r="{r + k}">' + "".join(f'<c r="{c}{r + k}"><v>{k}</v></c>' for c in "ABCD") + "</row>"
            for k in (1, 2)
        )
        r += 4
    return workbook_zip("<sheetData>" + "".join(rows) + "</sheetData>")


def _allocated_and_charged(data: bytes, monkeypatch) -> tuple[int, int, int]:
    parser = load(data)
    quiet = logging.getLogger("qa.round3.quiet")
    quiet.setLevel(logging.CRITICAL + 1)  # captured log records would be counted as allocations
    quiet.propagate = False
    parser.logger = quiet
    # plain coroutines: a Mock records every call's arguments, which would keep every table alive
    async def detect(_rows: object, _llm: object) -> SimpleNamespace:
        return SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high")

    async def summary(_table: object) -> str:
        return "s"

    parser.detect_excel_headers_with_llm = detect
    parser.get_table_summary = summary
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "0")
    start = parser._output.left
    lowest = [start]
    real_charge = module._OutputBudget.charge

    def watch(self, nbytes: int) -> None:
        real_charge(self, nbytes)
        lowest[0] = min(lowest[0], self.left)

    monkeypatch.setattr(module._OutputBudget, "charge", watch)
    tracemalloc.start(1)
    try:
        before, _ = tracemalloc.get_traced_memory()
        container = asyncio.run(parser.get_blocks_from_workbook(MagicMock()))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak - before, start - lowest[0], len(container.block_groups)


def test_many_small_tables_are_charged_at_least_what_they_allocate(monkeypatch) -> None:
    allocated, charged, groups = _allocated_and_charged(_many_small_tables(), monkeypatch)
    assert groups == TABLES + 1
    assert allocated <= charged, f"allocated {allocated / 1e6:.1f} MB but charged {charged / 1e6:.1f} MB"


def test_one_large_table_is_charged_at_least_what_it_allocates(monkeypatch) -> None:
    # control: the same check on a single 1500 x 30 table passes (about 0.94 of the charge)
    rows = "".join(
        f'<row r="{r}">' + "".join(f'<c r="{chr(65 + c)}{r}"><v>{c}</v></c>' for c in range(26)) + "</row>"
        for r in range(1, 1501)
    )
    allocated, charged, _ = _allocated_and_charged(workbook_zip(f"<sheetData>{rows}</sheetData>"), monkeypatch)
    assert allocated <= charged, f"allocated {allocated / 1e6:.1f} MB but charged {charged / 1e6:.1f} MB"


def _big_cell_workbook(chars: int) -> bytes:
    from tests.support.excel_workbooks import MAIN_NS

    sst = f'<?xml version="1.0"?><sst xmlns="{MAIN_NS}"><si><t>{"x" * chars}</t></si></sst>'
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c><c r="B1" t="inlineStr"><is><t>b</t></is></c></row>'
        '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2"><v>2</v></c></row>'
        + "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}"><v>{r}</v></c></row>' for r in range(3, 6))
    )
    return workbook_zip(f"<sheetData>{rows}</sheetData>", extra={"xl/sharedStrings.xml": sst})


def test_a_huge_cell_is_not_copied_into_the_llm_prompts_past_what_was_charged(monkeypatch) -> None:
    # One 6 MB cell in the first rows: the header-detection prompt and the table-summary sample are
    # built from it by the real prompt code (only the network edge is stubbed). Their copies of the
    # text are parent memory too; they must not exceed what the budget charged.
    parser = load(_big_cell_workbook(6_000_000))
    quiet = logging.getLogger("qa.round3.quiet")
    quiet.setLevel(logging.CRITICAL + 1)
    quiet.propagate = False
    parser.logger = quiet

    async def structured(_llm: object, _messages: object, _schema: object, *a: object, **k: object) -> SimpleNamespace:
        return SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")

    async def call_llm(_messages: object, *a: object, **k: object) -> SimpleNamespace:
        return SimpleNamespace(content="summary")

    monkeypatch.setattr(module, "invoke_with_structured_output_and_reflection", structured)
    monkeypatch.setattr(parser, "_call_llm", call_llm)
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "0")
    start = parser._output.left
    lowest = [start]
    real_charge = module._OutputBudget.charge

    def watch(self, nbytes: int) -> None:
        real_charge(self, nbytes)
        lowest[0] = min(lowest[0], self.left)

    monkeypatch.setattr(module._OutputBudget, "charge", watch)
    tracemalloc.start(1)
    try:
        before, _ = tracemalloc.get_traced_memory()
        asyncio.run(parser.get_blocks_from_workbook(MagicMock()))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    charged = start - lowest[0]
    allocated = peak - before
    assert allocated <= charged, f"allocated {allocated / 1e6:.1f} MB but charged {charged / 1e6:.1f} MB"
