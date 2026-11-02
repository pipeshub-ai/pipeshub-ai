"""Spellings of a cell, or of its sheet, that the old estimating guard did not count.

Rewritten for the worker: these workbooks are built at a size the worker's allowance
cannot hold (32 MB here, 1.5 GB in production), so whatever the spelling, the load is
refused with EXCEL_TOO_LARGE; and the ones that fit still load. The guard's counting
internals (check_workbook_size, EXCEL_MAX_CELLS) no longer exist, so the tests assert
the outcome instead of the count. Measured at full size on the old guard (3.2 M and
8 M empty cells, 7-20 MB files): 1.96 GB and 3.7 GB.
"""

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import MAIN_NS, empty_cells, load, workbook_zip


@pytest.fixture(autouse=True)
def small_worker_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker may grow by 32 MB, so a few hundred thousand cells stand in for the gigabytes."""
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_BYTES", 32 * 1024 * 1024)


def assert_refused_as_too_large(data: bytes) -> None:
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE


ROWS, COLS = 2000, 80  # 160 000 cells


@pytest.mark.parametrize("sep", ["\t", "\r", "\r\n"])
def test_cells_whose_attributes_follow_other_whitespace_are_refused(sep: str) -> None:
    body = f"<sheetData>{empty_cells(ROWS, COLS, lambda ref: f'<c{sep}r=\"{ref}\"/>')}</sheetData>"
    assert_refused_as_too_large(workbook_zip(body))


def test_cells_with_a_prefix_other_than_the_sheetdata_prefix_are_refused() -> None:
    # Both prefixes are bound to the main namespace, so openpyxl reads x:c as a cell.
    cells = empty_cells(ROWS, COLS, lambda ref: f'<x:c r="{ref}"/>')
    xml = (
        f'<?xml version="1.0"?><worksheet xmlns="{MAIN_NS}" xmlns:x="{MAIN_NS}">'
        f"<sheetData>{cells}</sheetData></worksheet>"
    )
    assert_refused_as_too_large(workbook_zip("", sheet_xml=xml))


def test_a_decoy_sheetdata_in_a_comment_does_not_hide_the_cells() -> None:
    body = f"<sheetData>{empty_cells(ROWS, COLS)}</sheetData>"
    assert_refused_as_too_large(workbook_zip(body, head="<!-- <z:sheetData> -->"))


def test_a_sheet_part_that_does_not_end_in_xml_is_refused() -> None:
    # openpyxl opens the Target of the workbook relationship whatever its extension.
    body = f"<sheetData>{empty_cells(ROWS, COLS)}</sheetData>"
    assert_refused_as_too_large(workbook_zip(body, sheet_path="xl/worksheets/sheet1.bin"))


def test_cell_like_text_in_cdata_and_a_prefixed_workbook_within_the_limit_still_load() -> None:
    cdata = '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t><![CDATA[<c r="x"/>]]></t></is></c></row></sheetData>'
    assert load(workbook_zip(cdata)).workbook["S"].cell(1, 1).value == '<c r="x"/>'
    prefixed = (
        f'<x:worksheet xmlns:x="{MAIN_NS}"><x:sheetData>'
        + "".join(f'<x:row r="{r}"><x:c r="A{r}"><x:v>1</x:v></x:c></x:row>' for r in range(1, 6))
        + "</x:sheetData></x:worksheet>"
    )
    assert load(workbook_zip("", sheet_xml=prefixed)).workbook["S"].max_row == 5
