"""Spellings of a cell, or of its sheet, that openpyxl reads but the size guard does not count.

Each test scales the limits down so a few KB stand in for the real gigabytes.
Measured at full size (3.2 M and 8 M empty cells, 7-20 MB files): all admitted by the guard and
loaded by openpyxl; 3.2 M cells peaked at 1.96 GB, 8 M cells at 3.7 GB.
"""

import io
import zipfile

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _col(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cells(rows: int, cols: int, cell) -> str:
    return "".join(
        f'<row r="{r}">' + "".join(cell(f"{_col(c)}{r}") for c in range(1, cols + 1)) + "</row>" for r in range(1, rows + 1)
    )


def _zip(sheet_xml: str, sheet_path: str = "xl/worksheets/sheet1.xml") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(sheet_path, sheet_xml)
    return out.getvalue()


@pytest.fixture(autouse=True)
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "EXCEL_MAX_CELLS", 1_000)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 52_428)


def _plain(body: str, head: str = "") -> str:
    return f'<?xml version="1.0"?><worksheet {_NS}>{head}<sheetData>{body}</sheetData></worksheet>'


@pytest.mark.parametrize("sep", ["\t", "\r", "\r\n"])
def test_cells_whose_attributes_follow_other_whitespace_are_counted(sep: str) -> None:
    xml = _plain(_cells(250, 10, lambda ref: f'<c{sep}r="{ref}"/>'))
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(xml))


def test_cells_with_a_prefix_other_than_the_sheetdata_prefix_are_counted() -> None:
    # Both prefixes are bound to the main namespace, so openpyxl reads x:c as a cell.
    xml = (
        f'<?xml version="1.0"?><worksheet {_NS} xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{_cells(250, 10, lambda ref: f'<x:c r={chr(34)}{ref}{chr(34)}/>')}</sheetData></worksheet>"
    )
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(xml))


def test_a_decoy_sheetdata_in_a_comment_does_not_hide_the_cells() -> None:
    xml = _plain(_cells(250, 10, lambda ref: f'<c r="{ref}"/>'), head="<!-- <z:sheetData> -->")
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(xml))


def test_a_sheet_part_that_does_not_end_in_xml_is_counted() -> None:
    # openpyxl opens the Target of the workbook relationship whatever its extension.
    xml = _plain(_cells(250, 10, lambda ref: f'<c r="{ref}"/>'))
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(xml, "xl/worksheets/sheet1.bin"))


def test_cell_like_text_in_cdata_and_a_prefixed_workbook_within_the_limits_still_load() -> None:
    cdata = _plain('<row r="1"><c r="A1" t="inlineStr"><is><t><![CDATA[<c r="x"/>]]></t></is></c></row>')
    module.check_workbook_size(_zip(cdata))
    prefixed = (
        '<x:worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><x:sheetData>'
        + _cells(5, 5, lambda ref: f'<x:c r="{ref}"/>')
        + "</x:sheetData></x:worksheet>"
    )
    module.check_workbook_size(_zip(prefixed))
