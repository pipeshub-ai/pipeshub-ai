"""Round 4 of the worker-load design (MEM-6b): prompts, per-table overhead, LLM text, sparse scan."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.modules.parsers.excel import workbook_model
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from app.utils.indexing_helpers import generate_simple_row_text
from tests.support.excel_workbooks import MAIN_NS, load, workbook_zip


def _quiet(parser) -> None:
    quiet = logging.getLogger("round4.quiet")
    quiet.setLevel(logging.CRITICAL + 1)
    quiet.propagate = False
    parser.logger = quiet


def _big_cell_workbook(chars: int) -> bytes:
    sst = f'<?xml version="1.0"?><sst xmlns="{MAIN_NS}"><si><t>{"x" * chars}</t></si></sst>'
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c><c r="B1" t="inlineStr"><is><t>b</t></is></c></row>'
        '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2"><v>2</v></c></row>'
        + "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}"><v>{r}</v></c></row>' for r in range(3, 6))
    )
    return workbook_zip(f"<sheetData>{rows}</sheetData>", extra={"xl/sharedStrings.xml": sst})


def test_prompts_carry_a_bounded_slice_of_a_huge_cell(monkeypatch) -> None:
    parser = load(_big_cell_workbook(2_000_000))
    _quiet(parser)
    seen: list[int] = []

    async def structured(_llm, messages, _schema, *a, **k):
        seen.append(sum(len(str(m.content)) for m in messages))
        return SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")

    async def call_llm(messages, *a, **k):
        seen.append(sum(len(str(m.content)) for m in messages))
        return SimpleNamespace(content="summary")

    async def rows(_llm, messages, **k):
        seen.append(sum(len(str(m.content)) for m in messages))
        return SimpleNamespace(descriptions=["d"] * k["expected_count"])

    monkeypatch.setattr(module, "invoke_with_structured_output_and_reflection", structured)
    monkeypatch.setattr(module, "invoke_with_row_descriptions_and_reflection", rows)
    monkeypatch.setattr(parser, "_call_llm", call_llm)
    asyncio.run(parser.get_blocks_from_workbook(MagicMock()))
    assert len(seen) >= 3  # header detection, summary and row text were all built
    assert max(seen) < 200_000, seen


def test_the_fallback_row_text_keeps_the_whole_cell(monkeypatch) -> None:
    # the prompt is cut, the output is not: when the LLM gives nothing the row text is the full row
    parser = load(_big_cell_workbook(100_000))
    _quiet(parser)
    parser.llm = MagicMock()

    async def none(*a, **k):
        return None

    monkeypatch.setattr(module, "invoke_with_row_descriptions_and_reflection", none)
    row = [{"header": "a", "value": "y" * 100_000}, {"header": "b", "value": 1}]
    texts = asyncio.run(parser.get_rows_text([row], "s"))
    assert texts == [generate_simple_row_text({"a": "y" * 100_000, "b": 1})]
    assert len(texts[0]) > 100_000


def test_simple_row_text_format_is_unchanged() -> None:
    assert generate_simple_row_text({}) == ""
    assert generate_simple_row_text({"a": 1}) == "a: 1"
    assert generate_simple_row_text({"a": None, 3: "x", None: 2.5}) == "a: null, 3: x, None: 2.5"


def test_llm_row_texts_are_charged_as_each_batch_arrives(monkeypatch) -> None:
    rows = "".join(
        f'<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}"><v>{r}</v></c></row>' for r in range(1, 1001)
    )
    parser = load(workbook_zip(f"<sheetData>{rows}</sheetData>"))
    _quiet(parser)
    returned = [0]

    async def detect(_rows, _llm):
        return SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high")

    async def summary(_table):
        return "s"

    async def get_rows_text(batch, _summary):
        await asyncio.sleep(0)
        returned[0] += 1
        return ["z" * 100_000] * len(batch)

    parser.detect_excel_headers_with_llm = detect
    parser.get_table_summary = summary
    parser.get_rows_text = get_rows_text
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "5000")
    parser._output.left = 10_000_000  # one batch of 50 x 100 KB x 2 is 10 MB
    with pytest.raises(ParseError) as raised:
        asyncio.run(parser.get_blocks_from_workbook(MagicMock()))
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE
    # 1000 rows are 20 batches, 10 at a time; the first arrival is refused and the rest are cancelled
    assert returned[0] < 15, returned[0]  # all 20 without the fix


def test_a_stray_far_cell_does_not_probe_the_bounding_box(monkeypatch) -> None:
    rows = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c><c r="B1" t="inlineStr"><is><t>b</t></is></c></row>'
        '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>2</v></c></row>'
        '<row r="60000"><c r="KN60000"><v>5</v></c></row>'
    )
    parser = load(workbook_zip(f"<sheetData>{rows}</sheetData>"))
    _quiet(parser)
    probes = [0]
    real = workbook_model.SheetModel.raw_value

    def counting(self, row, col):
        probes[0] += 1
        return real(self, row, col)

    monkeypatch.setattr(workbook_model.SheetModel, "raw_value", counting)

    async def detect(_rows, _llm):
        return SimpleNamespace(has_headers=True, num_header_rows=1, confidence="high")

    async def summary(_table):
        return "s"

    parser.detect_excel_headers_with_llm = detect
    parser.get_table_summary = summary
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "0")
    asyncio.run(parser.get_blocks_from_workbook(MagicMock()))
    # the box is 60 000 x 300 = 18 M positions; only populated rows are scanned
    assert probes[0] < 1_000_000, probes[0]
