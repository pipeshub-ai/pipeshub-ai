"""Ways round 4 of the size guard still admits parts that openpyxl loads in full.

Limits are scaled down so a few KB stand in for the gigabytes. Measured at full
size (default limits, openpyxl 3.1.5): see notes/qa/mem6-r4-report.md.
"""

import io
import zipfile

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _zip(sheet_xml: str, sst_xml: str | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if sst_xml is not None:
            z.writestr("xl/sharedStrings.xml", sst_xml)
    return out.getvalue()


@pytest.fixture(autouse=True)
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "EXCEL_MAX_CELLS", 1_000)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 10_000_000)


def _cells(n: int) -> str:
    return "".join(f'<row r="{i}"><c r="A{i}"/><c r="B{i}"/></row>' for i in range(1, n // 2 + 1))


def _sheet(body: str, root: str = "worksheet", head: str = "") -> str:
    return f'<?xml version="1.0"?>{head}<{root} {_NS}>{body}</{root}>'


def _sst(n: int, root: str = "sst") -> str:
    return f'<?xml version="1.0"?><{root} {_NS}>' + "".join(f"<si><t>s{i}</t></si>" for i in range(n)) + f"</{root}>"


@pytest.mark.parametrize("head", ["<!DOCTYPE worksheet>", "<!DOCTYPE worksheet [<!ELEMENT a ANY>]>"])
def test_sheet_with_a_doctype_is_not_admitted_uncounted(head: str) -> None:
    # openpyxl loads a DOCTYPE without entities; the guard stops at it and counts nothing.
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(_sheet(f"<sheetData>{_cells(3000)}</sheetData>", head=head)))


@pytest.mark.parametrize("root", ["calcChain", "xml", "sst"])
def test_sheet_whose_root_element_is_renamed_is_still_counted(root: str) -> None:
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(_sheet(f"<sheetData>{_cells(3000)}</sheetData>", root=root)))


@pytest.mark.parametrize("root", ["worksheet", "calcChain", "xml"])
def test_shared_strings_under_another_root_are_counted(root: str) -> None:
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(_sheet("<sheetData/>"), _sst(3000, root)))


def _children(kind: str, n: int) -> str:
    if kind == "hyperlink":
        return "<sheetData/><hyperlinks>" + "".join(f'<hyperlink ref="A{i}" location="S!A1"/>' for i in range(n)) + "</hyperlinks>"
    if kind == "mergeCell":
        return "<sheetData/><mergeCells>" + "".join(f'<mergeCell ref="A{i}:B{i}"/>' for i in range(n)) + "</mergeCells>"
    if kind == "conditionalFormatting":
        return "<sheetData/>" + "".join(
            f'<conditionalFormatting sqref="A{i}"><cfRule type="cellIs" priority="{i}" operator="equal"><formula>1</formula></cfRule></conditionalFormatting>'
            for i in range(n)
        )
    if kind == "dataValidation":
        return "<sheetData/><dataValidations>" + "".join(
            f'<dataValidation type="whole" sqref="A{i}"><formula1>1</formula1></dataValidation>' for i in range(n)
        ) + "</dataValidations>"
    return "<sheetData>" + "".join(f'<row r="{i}" ht="20" customHeight="1"/>' for i in range(n)) + "</sheetData>"


@pytest.mark.parametrize("kind", ["hyperlink", "mergeCell", "conditionalFormatting", "dataValidation", "row"])
def test_sheet_children_other_than_cells_spend_the_budget(kind: str) -> None:
    # Measured (500k each, openpyxl 3.1.5): 0.7 to 1.5 KB of memory per item, more than a cell.
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(_sheet(_children(kind, 3000))))
