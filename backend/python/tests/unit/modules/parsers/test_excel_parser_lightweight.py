"""Unit tests for ExcelParser.create_blocks_lightweight."""

import io
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.models.blocks import BlockType, GroupType
from app.modules.parsers.excel.excel_parser import ExcelParser


def _xlsx_bytes(rows: list[list[object]], sheet_name: str = "Sheet1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def parser():
    return ExcelParser(logger=MagicMock(), config_service=MagicMock())


@pytest.mark.asyncio
async def test_create_blocks_lightweight_happy_path(parser):
    content = _xlsx_bytes([["name", "amount"], ["Alice", 10], ["Bob", 20]])
    parser.load_workbook_from_binary(content)
    container = await parser.create_blocks_lightweight()

    assert any(g.type == GroupType.SHEET for g in container.block_groups)
    assert any(g.type == GroupType.TABLE for g in container.block_groups)
    assert len([b for b in container.blocks if b.type == BlockType.TABLE_ROW]) == 2


@pytest.mark.asyncio
async def test_create_blocks_lightweight_respects_max_rows(parser):
    rows = [["col"]] + [[i] for i in range(50)]
    content = _xlsx_bytes(rows)
    parser.load_workbook_from_binary(content)
    container = await parser.create_blocks_lightweight(max_rows=5)
    assert len([b for b in container.blocks if b.type == BlockType.TABLE_ROW]) == 5


@pytest.mark.asyncio
async def test_create_blocks_lightweight_empty_workbook(parser):
    content = _xlsx_bytes([])  # header-less empty sheet
    parser.load_workbook_from_binary(content)
    container = await parser.create_blocks_lightweight()
    # Empty / header-only sheet yields no data rows.
    assert container.blocks == []


@pytest.mark.asyncio
async def test_create_blocks_lightweight_closes_workbook(parser):
    content = _xlsx_bytes([["h"], ["v"]])
    parser.load_workbook_from_binary(content)
    wb = parser.workbook
    close = MagicMock(wraps=wb.close)
    wb.close = close
    await parser.create_blocks_lightweight()
    close.assert_called_once()


# ---------------------------------------------------------------------------
# Cell styling: gradient, pattern, theme and missing fills / fonts
# ---------------------------------------------------------------------------


def _styled_xlsx_bytes() -> bytes:
    from openpyxl.styles import Color, Font, GradientFill, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.append(["Region", "Sales", "Plain"])
    ws.append(["North", 10, "x"])
    ws["A1"].fill = GradientFill(stop=("FF0000", "0000FF"))  # header cell
    ws["A2"].fill = GradientFill(stop=("00FF00", "FFFFFF"))  # data cell
    ws["B2"].fill = PatternFill(fill_type="solid", start_color="FFFFFF00")
    ws["B1"].fill = PatternFill(fill_type="solid", fgColor=Color(theme=4))
    ws["B1"].font = Font(color=Color(theme=1))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def styled_sheet(parser: ExcelParser) -> Worksheet:
    parser.load_workbook_from_binary(_styled_xlsx_bytes())
    return parser.workbook.active


def test_process_cell_gradient_fill_uses_first_stop(parser: ExcelParser, styled_sheet: Worksheet) -> None:
    header = parser._process_cell(styled_sheet.cell(1, 1), None, 1, 1)
    data = parser._process_cell(styled_sheet.cell(2, 1), "Region", 2, 1)
    assert header["value"] == "Region"
    assert header["style"]["fill"]["background_color"] == "00FF0000"
    assert data["value"] == "North"
    assert data["style"]["fill"]["background_color"] == "0000FF00"


def test_process_cell_pattern_fill_keeps_colour(parser: ExcelParser, styled_sheet: Worksheet) -> None:
    cell = parser._process_cell(styled_sheet.cell(2, 2), "Sales", 2, 2)
    assert cell["value"] == 10
    assert cell["style"]["fill"]["background_color"] == "FFFFFF00"


def test_process_cell_theme_colours_give_none(parser: ExcelParser, styled_sheet: Worksheet) -> None:
    cell = parser._process_cell(styled_sheet.cell(1, 2), None, 1, 2)
    assert cell["value"] == "Sales"
    assert cell["style"]["fill"]["background_color"] is None
    assert cell["style"]["font"]["color"] is None


def test_process_cell_default_fill_unchanged(parser: ExcelParser, styled_sheet: Worksheet) -> None:
    # An unstyled cell has an empty PatternFill; its colour is what it was before.
    cell = parser._process_cell(styled_sheet.cell(2, 3), "Plain", 2, 3)
    assert cell["value"] == "x"
    assert cell["style"]["fill"]["background_color"] == "00000000"


def test_missing_fill_gives_none() -> None:
    from types import SimpleNamespace

    from app.modules.parsers.excel.excel_parser import get_cell_background_color

    assert get_cell_background_color(SimpleNamespace(fill=None)) is None
    assert get_cell_background_color(SimpleNamespace()) is None


def test_style_helpers_never_raise() -> None:
    from app.modules.parsers.excel.excel_parser import (
        get_cell_background_color,
        get_cell_font_color,
    )

    class _Broken:
        @property
        def font(self) -> object:
            raise RuntimeError("bad style")

        @property
        def fill(self) -> object:
            raise RuntimeError("bad style")

    assert get_cell_font_color(_Broken()) is None
    assert get_cell_background_color(_Broken()) is None


@pytest.mark.asyncio
async def test_create_blocks_lightweight_gradient_fills(parser: ExcelParser) -> None:
    parser.load_workbook_from_binary(_styled_xlsx_bytes())
    container = await parser.create_blocks_lightweight()
    rows = [b for b in container.blocks if b.type == BlockType.TABLE_ROW]
    assert len(rows) == 1
    # Values survive; the gradient data cell is present in the row text/metadata.
    assert any("North" in str(getattr(b, "data", "")) for b in rows)
