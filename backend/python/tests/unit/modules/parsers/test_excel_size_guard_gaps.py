"""Workbooks the size guard lets through although they are as costly to load as the ones it refuses.

Each test scales the limits down so a few KB stand in for the real gigabytes.
"""

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError

_SHEET_HEAD = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'


def _col(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _zip(sheet_xml: str, sheet_path: str = "xl/worksheets/sheet1.xml", extra: dict[str, str] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(sheet_path, sheet_xml)
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return out.getvalue()


def _empty_cells_sheet(rows: int, cols: int, dimension: str = "") -> str:
    names = [_col(c) for c in range(1, cols + 1)]
    body = "".join(
        f'<row r="{r}">' + "".join(f'<c r="{n}{r}"/>' for n in names) + "</row>" for r in range(1, rows + 1)
    )
    return f"{_SHEET_HEAD}{dimension}<sheetData>{body}</sheetData></worksheet>"


@pytest.fixture
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1,000 cells and the production ratio (150 MB for 3 M cells: 52 bytes of XML per cell).
    monkeypatch.setattr(module, "EXCEL_MAX_CELLS", 1_000)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 52_428)


def test_compact_empty_cells_without_a_dimension_are_refused(small_limits: None) -> None:
    # 2,500 cells (2.5x the limit) in about 45 KB of XML, under the byte cap. Measured at full size:
    # 8 M empty cells, 144 MB of XML, no <dimension> -> admitted, 3.7 GB peak.
    data = _zip(_empty_cells_sheet(250, 10))
    assert len(_empty_cells_sheet(250, 10)) < module.EXCEL_MAX_SHEET_XML_BYTES
    with pytest.raises(ParseError):
        module.check_workbook_size(data)


def test_a_sheet_outside_xl_worksheets_is_counted(small_limits: None) -> None:
    sheet = _empty_cells_sheet(500, 8, dimension='<dimension ref="A1:H500"/>')
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip(sheet, sheet_path="xl/data/s1.xml"))


def test_parts_other_than_sheets_are_bounded(small_limits: None) -> None:
    # A tiny sheet plus a 5 MB-equivalent styles part: loading builds one object per <xf>.
    xfs = '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' * 5_000
    styles = f"<styleSheet><cellXfs>{xfs}</cellXfs></styleSheet>"
    data = _zip(_empty_cells_sheet(2, 2), extra={"xl/styles.xml": styles})
    with pytest.raises(ParseError):
        module.check_workbook_size(data)


def test_a_sparse_sheet_with_an_inflated_dimension_still_loads(small_limits: None) -> None:
    # Four real cells; the writer declared the whole grid. It loaded in 0.05 s and 0 MB before the guard.
    sheet = (
        f'{_SHEET_HEAD}<dimension ref="A1:T1048576"/><sheetData>'
        '<row r="1"><c r="A1"><v>1</v></c></row><row r="1048576"><c r="T1048576"><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )
    module.check_workbook_size(_zip(sheet))


@pytest.mark.parametrize("value", ["", "0", "-5", "abc"])
def test_a_bad_limit_in_the_environment_does_not_break_the_module(value: str) -> None:
    backend = Path(__file__).resolve().parents[4]
    code = "from app.modules.parsers.excel import excel_parser as m; print(m.EXCEL_MAX_CELLS)"
    env = {**os.environ, "EXCEL_MAX_CELLS": value, "PYTHONPATH": str(backend)}
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=backend, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-300:]
    assert int(done.stdout.strip().splitlines()[-1]) >= 1_000
