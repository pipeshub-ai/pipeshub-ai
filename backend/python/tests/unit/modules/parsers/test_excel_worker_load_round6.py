"""Round 6 of the worker-load design (MEM-6b): the load's CPU time is bounded, not only its memory."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.excel import excel_parser as module
from app.modules.parsers.excel import limits
from app.modules.parsers.excel.workbook_model import (
    WorkbookModel,
    WorkbookRefused,
    load_workbook_model,
)
from app.services.parsing.interface import ParseError
from app.services.resource_governor import policy as policy_mod
from app.services.resource_governor.kinds import ParseKind
from app.utils import user_errors
from tests.support.excel_workbooks import workbook_zip
from tests.support.excel_worker_jobs import (
    cpu_limit_around_a_load,
    spin_under_the_cpu_limit,
)
from tests.unit.services.resource_governor.test_policy import _snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

MERGES = 2500
LINKS = 2000  # 5 M hyperlinks x merges: under the precheck bound, about 80 s of openpyxl


def _links_on_merges() -> bytes:
    body = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        f'<mergeCells count="{MERGES}">'
        + "".join(f'<mergeCell ref="A{r}:B{r}"/>' for r in range(3, 3 + MERGES))
        + "</mergeCells><hyperlinks>"
        + "".join(f'<hyperlink ref="B{3 + (i % MERGES)}" location="S!A1"/>' for i in range(LINKS))
        + "</hyperlinks>"
    )
    return workbook_zip(body)


def _load_model(data: bytes, seconds: float | None) -> WorkbookModel:
    return load_workbook_model(
        data, module.EXCEL_LOAD_MAX_BYTES, module.EXCEL_MAX_SHEET_XML_BYTES, module.EXCEL_MAX_TEXT_CHARS, seconds
    )


# The kernel ends the worker at its CPU limit only where the worker could mark itself non-dumpable (``allow_cpu_limit``).
cpu_limit_ends_the_worker = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the worker sets no CPU limit off Linux")


@cpu_limit_ends_the_worker
def test_a_load_past_its_cpu_limit_is_refused_as_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    # the worker's own CPU limit ends the load, not the pool's timeout: the worker is ended by the
    # kernel and the parser refuses the workbook as it does for the timeout
    real_run_blocking = parse_pool.run_blocking

    def with_a_long_timeout(fn: object, *args: object, **kwargs: object) -> object:
        return real_run_blocking(fn, *args, **{**kwargs, "timeout": 120})

    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_SECONDS", 1)
    monkeypatch.setattr(module.parse_pool, "run_blocking", with_a_long_timeout)
    parser = module.ExcelParser(logging.getLogger("excel-test"), MagicMock())
    started = time.monotonic()
    with pytest.raises(ParseError) as raised:
        parser.load_workbook_from_binary(_links_on_merges())
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE
    # wall-clock-ok: a backstop many times the limit under test; unbounded the load runs about 80 s
    assert time.monotonic() - started < 30  # the limit plus slack; unbounded this runs about 80 s


@cpu_limit_ends_the_worker
def test_the_cpu_limit_never_raises_into_the_worker() -> None:
    # An exception raised from a signal handler can land between a lock's acquire and its release
    # and hang the reused worker on a later job; the limit must end the worker instead.
    started = time.monotonic()
    with pytest.raises(parse_pool.ParseWorkerError) as raised:
        parse_pool.run_blocking(spin_under_the_cpu_limit, 1, label="spin", timeout=120, kind=ParseKind.EXCEL_LOAD)
    assert raised.value.cause == "timed_out"
    # wall-clock-ok: a backstop many times the limit under test; unbounded the load runs about 80 s
    assert time.monotonic() - started < 30, "the CPU limit ended it, not the pool's timeout"


@cpu_limit_ends_the_worker
def test_a_job_that_is_not_an_excel_load_ended_by_sigxcpu_is_the_retryable_crash() -> None:
    # Only the Excel load sets that limit: another job dying of it (an operator's `ulimit -t`) is not a refused file.
    with pytest.raises(parse_pool.ParseWorkerError) as raised:
        parse_pool.run_blocking(spin_under_the_cpu_limit, 1, label="spin", timeout=120)
    assert raised.value.cause != "timed_out"


@pytest.fixture
def one_worker_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    monkeypatch.setenv(parse_pool.WORKERS_ENV, "1")
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=4)
    parse_pool.set_resource_governor(governor)
    try:
        yield governor
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


@cpu_limit_ends_the_worker
async def test_jobs_queued_behind_a_worker_ended_by_its_cpu_limit_still_run(one_worker_pool: MagicMock) -> None:
    outcomes = await asyncio.gather(
        parse_pool.submit(len, b"before"),
        parse_pool.submit(spin_under_the_cpu_limit, 1, label="spin", timeout=120, kind=ParseKind.EXCEL_LOAD),
        parse_pool.submit(len, b"after one"),
        parse_pool.submit(len, b"after two"),
        return_exceptions=True,
    )
    assert outcomes[0] == len(b"before")
    assert isinstance(outcomes[1], parse_pool.ParseWorkerError), outcomes[1]
    assert outcomes[1].cause == "timed_out"
    assert outcomes[2:] == [len(b"after one"), len(b"after two")]
    one_worker_pool.report_memory_incident.assert_not_called()  # a CPU limit is not memory pressure


def test_a_normal_workbook_loads_within_the_limit() -> None:
    body = '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
    assert _load_model(workbook_zip(body), 60).sheetnames


def test_the_cpu_limit_is_put_back_after_the_load() -> None:
    data = workbook_zip('<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>')
    before, after = parse_pool.run_blocking(
        cpu_limit_around_a_load, data, module.EXCEL_LOAD_MAX_BYTES, module.EXCEL_MAX_SHEET_XML_BYTES, 60, timeout=120
    )
    assert after == before


def test_the_pool_timeout_ends_a_load_in_a_worker_as_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    # through the real path: a worker process, the job timeout, and the parser's mapping to EXCEL_TOO_LARGE
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_SECONDS", 3)
    parser = module.ExcelParser(logging.getLogger("excel-test"), MagicMock())
    started = time.monotonic()
    with pytest.raises(ParseError) as raised:
        parser.load_workbook_from_binary(_links_on_merges())
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE
    # wall-clock-ok: a backstop many times the limit under test; unbounded the load runs about 80 s
    assert time.monotonic() - started < 30


def test_the_default_load_limit_is_above_measured_legitimate_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCEL_LOAD_MAX_SECONDS", raising=False)
    assert limits.excel_load_max_seconds() == limits.DEFAULT_LOAD_MAX_SECONDS == 240
    assert limits.DEFAULT_LOAD_MAX_SECONDS >= 3 * 72  # slowest admitted load measured: 72 s
    monkeypatch.setenv("EXCEL_LOAD_MAX_SECONDS", "30")
    assert limits.excel_load_max_seconds() == 30  # an explicit setting wins


# -- the cap counts what openpyxl expands --------------------------------------------------------


def test_a_mergecell_outside_mergecells_is_not_counted() -> None:
    body = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row></sheetData>'
        '<mergeCell ref="A1:Z200000"/>'
    )
    assert _load_model(workbook_zip(body), 60).sheetnames  # openpyxl ignores it, so it loads


def test_a_doctype_in_a_utf16_part_is_refused() -> None:
    import io
    import zipfile

    src = workbook_zip('<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>')
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = ('<!DOCTYPE x [<!ENTITY e "v">]>' + body.decode()).encode("utf-16")
            zout.writestr(info.filename, body)
    with pytest.raises(WorkbookRefused):
        _load_model(out.getvalue(), 60)


# -- an explicit governor setting wins ----------------------------------------------------------


def test_an_explicit_heavy_parse_setting_is_used_exactly_and_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("EXCEL_MAX_TEXT_MB", raising=False)
    monkeypatch.delenv("EXCEL_LOAD_MAX_MB", raising=False)
    monkeypatch.setenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", "0.5")
    monkeypatch.setattr(policy_mod, "HEAVY_PARSE_WORKING_SET_GB", 0.5)
    monkeypatch.setattr(policy_mod, "_WARNED", set())
    snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=32 * 1024**3)
    with caplog.at_level(logging.WARNING, logger=policy_mod.__name__):
        assert policy_mod.heavy_parse_working_set_gb() == 0.5
        assert policy_mod.heavy_memory_cap(snap) == int(31 / 0.5)
        policy_mod.heavy_memory_cap(snap)
    assert len([r for r in caplog.records if "below what one Excel document" in r.getMessage()]) == 1


def test_an_unset_heavy_parse_setting_follows_the_excel_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", raising=False)
    monkeypatch.setattr(policy_mod, "HEAVY_PARSE_WORKING_SET_GB", 1.5)
    monkeypatch.setenv("EXCEL_MAX_TEXT_MB", "2048")
    assert policy_mod.heavy_parse_working_set_gb() == pytest.approx(6.0)


@pytest.mark.parametrize("non_dumpable", [True, False])
def test_the_cpu_limit_is_armed_only_in_a_worker_that_cannot_dump_core(monkeypatch: pytest.MonkeyPatch, non_dumpable: bool) -> None:
    # A piped core_pattern ignores RLIMIT_CORE: a worker the kernel could still dump is ended only by the pool's timeout.
    from app.modules.parsers import parse_worker

    monkeypatch.setenv(parse_worker._IN_WORKER_ENV, "")  # registers the restore, so no case leaves pytest marked as a worker
    parse_worker.allow_cpu_limit(non_dumpable=non_dumpable)
    assert parse_worker.in_worker() is non_dumpable
