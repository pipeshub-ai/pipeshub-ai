"""Workbooks the old estimating guard let through although they are as costly to load as the ones it refused.

Rewritten for the worker (see test_excel_size_guard_bypasses.py): the same inputs, built as
loadable workbooks at a size the 32 MB test allowance cannot hold, are refused with
EXCEL_TOO_LARGE; the sparse sheet with an inflated dimension, which costs nothing, still loads.
"""

import os
import subprocess
import sys
from pathlib import Path

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



def test_compact_empty_cells_without_a_dimension_are_refused() -> None:
    # Measured at full size: 8 M empty cells, 144 MB of XML, no <dimension> -> 3.7 GB peak.
    assert_refused_as_too_large(workbook_zip(f"<sheetData>{empty_cells(2000, 80)}</sheetData>"))


def test_a_sheet_outside_xl_worksheets_is_refused() -> None:
    body = f'<dimension ref="A1:H500"/><sheetData>{empty_cells(2000, 80)}</sheetData>'
    assert_refused_as_too_large(workbook_zip(body, sheet_path="xl/data/s1.xml"))


def test_parts_other_than_sheets_are_bounded() -> None:
    # A tiny sheet plus a styles part: loading builds one object per <xf> (1.7 KB each).
    xfs = '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' * 60_000
    styles = f'<?xml version="1.0"?><styleSheet xmlns="{MAIN_NS}"><cellXfs count="60000">{xfs}</cellXfs></styleSheet>'
    data = workbook_zip(f"<sheetData>{empty_cells(2, 2)}</sheetData>", extra={"xl/styles.xml": styles})
    assert_refused_as_too_large(data)


def test_a_sparse_sheet_with_an_inflated_dimension_still_loads() -> None:
    # Four real cells; the writer declared the whole grid. It loaded in 0.05 s and 0 MB before any guard.
    body = (
        '<dimension ref="A1:T1048576"/><sheetData>'
        '<row r="1"><c r="A1"><v>1</v></c></row><row r="1048576"><c r="T1048576"><v>2</v></c></row></sheetData>'
    )
    sheet = load(workbook_zip(body)).workbook["S"]
    assert sheet.cell(1, 1).value == 1
    assert sheet.cell(1048576, 20).value == 2


@pytest.mark.parametrize("value", ["", "0", "-5", "abc"])
def test_a_bad_limit_in_the_environment_does_not_break_the_module(value: str) -> None:
    backend = Path(__file__).resolve().parents[4]
    code = "from app.modules.parsers.excel import excel_parser as m; print(m.EXCEL_LOAD_MAX_MB)"
    env = {**os.environ, "EXCEL_LOAD_MAX_MB": value, "PYTHONPATH": str(backend)}
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=backend, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-300:]
    assert int(done.stdout.strip().splitlines()[-1]) >= 256
