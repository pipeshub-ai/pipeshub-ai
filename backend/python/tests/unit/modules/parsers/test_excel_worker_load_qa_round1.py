"""QA round 1 of the worker-load design (MEM-6b): inputs the worker's classification gets wrong.

Real worker processes, as in test_excel_worker_load.py.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import MAIN_NS, column_letters, load, workbook_zip
from tests.support.excel_worker_jobs import after_a_big_job_then_load

MB = 1024 * 1024


def _corrupt_workbook() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<broken")
    return out.getvalue()


def test_a_corrupt_workbook_is_not_called_too_large_because_an_earlier_job_in_the_worker_was_big() -> (
    None
):
    # A pool worker lives across jobs and is non-dumpable, so it cannot reset its peak-RSS
    # mark (/proc/self/clear_refs is refused). The near-limit rule then sees the earlier job's
    # peak as growth by this load and reports a damaged file as EXCEL_TOO_LARGE.
    outcome = parse_pool.run_blocking(
        after_a_big_job_then_load, _corrupt_workbook(), 64 * MB, 400 * MB
    )
    assert outcome.startswith("error:"), outcome


def test_the_decompressed_bytes_cap_refuses_a_workbook_that_would_fit_the_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The backstop (EXCEL_MAX_SHEET_XML_MB) had no test: removing it changed no result. A sheet of
    # about 3 MB of XML that loads in a few MB is refused when the cap is below that.
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_BYTES", 1024 * MB)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 1 * MB)
    cells = "".join(
        f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 100_000)
    )
    data = workbook_zip(f"<sheetData>{cells}</sheetData>")
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 400 * MB)
    assert (
        load(data).workbook["S"].max_row == 99_999
    )  # the same workbook loads under the real cap


def test_blocks_are_not_a_multiple_of_the_workbook_text_they_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    # The worker bounds the load, not what the parent builds from the model: a shared string used by
    # many cells is one object in the workbook and one copy per use in the blocks. 60 strings of
    # 30 KB (1.8 MB of XML) used by 25 cells each (1.5 MB file after compression: tiny) become about
    # 25x that in the parent; at the 400 MB decompressed cap the same shape needs more than 10 GB.
    # The text the blocks would hold (a shared string counted per use) is bounded before any block
    # is built: over EXCEL_MAX_TEXT_CHARS the workbook is refused.
    n, width, uses = 60, 30_000, 25
    strings = "".join(
        f"<si><t>{chr(97 + i % 26)}{i:04d}{'x' * width}</t></si>" for i in range(n)
    )
    sst = f'<?xml version="1.0"?><sst xmlns="{MAIN_NS}" count="{n * uses}" uniqueCount="{n}">{strings}</sst>'
    rows = "".join(
        f'<row r="{i + 1}">'
        + "".join(
            f'<c r="{column_letters(c + 1)}{i + 1}" t="s"><v>{(i + c) % n}</v></c>'
            for c in range(uses)
        )
        + "</row>"
        for i in range(n)
    )
    data = workbook_zip(f"<sheetData>{rows}</sheetData>", extra={"xl/sharedStrings.xml": sst})
    text = n * uses * width  # what the blocks would hold, about 25x the shared-string XML

    monkeypatch.setattr(module, "EXCEL_MAX_TEXT_CHARS", text // 2)
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE

    monkeypatch.setattr(module, "EXCEL_MAX_TEXT_CHARS", text * 2)
    produced = len(load(data)._build_basic_block_container().model_dump_json())
    assert produced < 4 * text, f"{produced} bytes of blocks for {text} characters of cell text"
