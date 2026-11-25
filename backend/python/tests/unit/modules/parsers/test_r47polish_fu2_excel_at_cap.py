"""R47POLISH-FU-2: a workbook load that sits at its memory allowance is refused at once, not after the CPU limit.

At the cap openpyxl can go on at a crawl (40,000 ``<mergeCell>`` at 38 MB: past 40 s, memory flat at the cap) or sit
inside one C call that runs no signal handler. The parse pool stops a worker that stays at its data limit from outside;
nothing is raised into the load from a signal handler (``_cpu_limited`` ends the worker instead).
"""

from __future__ import annotations

import os
import signal
from types import SimpleNamespace

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.excel import excel_parser
from app.modules.parsers.excel import workbook_model as wm
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import load, workbook_zip
from tests.support.excel_worker_jobs import wedged_at_the_cap

LIMIT = 64 * 1024 * 1024


def test_a_load_below_its_allowance_is_not_interrupted() -> None:
    before = signal.getsignal(signal.SIGXCPU) if hasattr(signal, "SIGXCPU") else None
    model = wm.load_workbook_model(workbook_zip('<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>'), LIMIT, LIMIT, None, 30)
    assert model.sheetnames == ["S"]
    if before is not None:
        assert signal.getsignal(signal.SIGXCPU) == before


@pytest.mark.skipif(not hasattr(signal, "SIGXCPU") or wm.resource is None, reason="POSIX only")
def test_a_load_wedged_in_c_at_the_cap_is_stopped_by_the_pool() -> None:
    # No signal handler runs inside the C call, so only the pool, outside the worker, can end it. The cause tells
    # the at-limit stop from the 60 s timeout, which pytest-timeout's 30 s would cut short anyway.
    with pytest.raises(parse_pool.ParseWorkerError) as stopped:
        parse_pool.run_blocking(wedged_at_the_cap, workbook_zip(""), LIMIT, LIMIT, label="wedged", timeout=60)
    assert stopped.value.cause == "at_memory_limit"


def test_the_excel_loader_reports_a_worker_stopped_at_its_limit_as_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    def stopped(*_args: object, **_kwargs: object) -> object:
        raise parse_pool.ParseWorkerError(user_errors.PARSE_WORKER_OUT_OF_MEMORY, "at_memory_limit")

    monkeypatch.setattr(excel_parser.parse_pool, "run_blocking", stopped)
    with pytest.raises(ParseError) as refused:
        load(workbook_zip(""))
    assert refused.value.message == user_errors.EXCEL_TOO_LARGE


def test_a_process_far_below_any_data_limit_is_not_at_it() -> None:
    assert parse_pool._at_data_limit(SimpleNamespace(process=SimpleNamespace(pid=os.getpid()))) is False
    assert parse_pool._at_data_limit(SimpleNamespace(process=None)) is False
