"""Round-4 spellings (renamed roots, shared strings, sheet children), against the worker's allowance.

Rewritten for the worker (see test_excel_size_guard_bypasses.py). A DOCTYPE is still
refused outright (an exact check made in the worker before loading); everything else is
built at a size the 32 MB test allowance cannot hold and must be refused with EXCEL_TOO_LARGE,
or fail to load at all - it is never admitted.
"""

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import MAIN_NS, load, workbook_zip


@pytest.fixture(autouse=True)
def small_worker_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker may grow by 32 MB, so a few hundred thousand cells stand in for the gigabytes."""
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_BYTES", 32 * 1024 * 1024)


def assert_refused_as_too_large(data: bytes) -> None:
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE



def _cells(n: int) -> str:
    return "".join(f'<row r="{i}"><c r="A{i}"/><c r="B{i}"/></row>' for i in range(1, n // 2 + 1))


def _sheet_xml(body: str, root: str = "worksheet", head: str = "") -> str:
    return f'<?xml version="1.0"?>{head}<{root} xmlns="{MAIN_NS}">{body}</{root}>'


def _sst(n: int, root: str = "sst") -> str:
    return f'<?xml version="1.0"?><{root} xmlns="{MAIN_NS}">' + "".join(f"<si><t>s{i}</t></si>" for i in range(n)) + f"</{root}>"


@pytest.mark.parametrize("head", ["<!DOCTYPE worksheet>", "<!DOCTYPE worksheet [<!ELEMENT a ANY>]>"])
def test_sheet_with_a_doctype_is_refused_as_unreadable(head: str) -> None:
    data = workbook_zip("", sheet_xml=_sheet_xml(f"<sheetData>{_cells(3000)}</sheetData>", head=head))
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.UNREADABLE_FILE


@pytest.mark.parametrize("root", ["calcChain", "xml", "sst"])
def test_sheet_whose_root_element_is_renamed_is_never_admitted(root: str) -> None:
    data = workbook_zip("", sheet_xml=_sheet_xml(f"<sheetData>{_cells(160_000)}</sheetData>", root=root))
    assert_refused_as_too_large(data)  # openpyxl reads <sheetData> whatever the root is called


@pytest.mark.parametrize("root", ["sst", "calcChain", "xml"])
def test_shared_strings_under_any_root_are_refused(root: str) -> None:
    # 600 000 shared strings (about 100 MB loaded when read as shared strings).
    sheet = '<sheetData><row r="1"><c r="A1" t="s"><v>599999</v></c></row></sheetData>'
    data = workbook_zip(sheet, extra={"xl/sharedStrings.xml": _sst(600_000, root)})
    assert_refused_as_too_large(data)  # openpyxl reads the <si> elements whatever the root is called


def _children(kind: str, n: int) -> str:
    if kind == "hyperlink":
        return "<sheetData/><hyperlinks>" + "".join(f'<hyperlink ref="A{i}" location="S!A1"/>' for i in range(1, n)) + "</hyperlinks>"
    if kind == "mergeCell":
        return "<sheetData/><mergeCells>" + "".join(f'<mergeCell ref="A{i}:B{i}"/>' for i in range(1, n)) + "</mergeCells>"
    if kind == "conditionalFormatting":
        return "<sheetData/>" + "".join(
            f'<conditionalFormatting sqref="A{i}"><cfRule type="cellIs" priority="{i}" operator="equal"><formula>1</formula></cfRule></conditionalFormatting>'
            for i in range(1, n)
        )
    if kind == "dataValidation":
        return "<sheetData/><dataValidations>" + "".join(
            f'<dataValidation type="whole" sqref="A{i}"><formula1>1</formula1></dataValidation>' for i in range(1, n)
        ) + "</dataValidations>"
    return "<sheetData>" + "".join(f'<row r="{i}" ht="20" customHeight="1"/>' for i in range(1, n)) + "</sheetData>"


@pytest.mark.parametrize("kind", ["hyperlink", "mergeCell", "conditionalFormatting", "dataValidation", "row"])
def test_sheet_children_other_than_cells_are_refused(kind: str) -> None:
    # Measured (500k each, openpyxl 3.1.5): 0.7 to 1.5 KB of memory per item, more than a cell.
    assert_refused_as_too_large(workbook_zip(_children(kind, 120_000)))
