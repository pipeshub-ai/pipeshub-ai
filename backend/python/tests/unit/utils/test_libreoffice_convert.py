"""Unit tests for app.utils.libreoffice_convert.convert_with_libreoffice."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.services.llm_gateway.limiter import CrossLoopSemaphore
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.parsing.interface import ParseError, ParseErrorCode
from app.utils import libreoffice_convert
from app.utils.libreoffice_convert import (
    LibreOfficeCouldNotReadFileError,
    convert_with_libreoffice,
    unreadable_file_as_parse_error,
)


def _fake_proc(returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


@pytest.mark.asyncio
async def test_raises_when_libreoffice_not_installed() -> None:
    which_proc = _fake_proc(returncode=1, stderr=b"not found")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=which_proc)):
        with pytest.raises(DocumentProcessingError, match="not installed"):
            await convert_with_libreoffice(b"doc data", "doc", "docx")


@pytest.mark.asyncio
async def test_raises_on_nonzero_exit() -> None:
    which_proc = _fake_proc(returncode=0)
    convert_proc = _fake_proc(returncode=1, stderr=b"boom")

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=[which_proc, convert_proc]),
    ):
        with pytest.raises(DocumentProcessingError, match=r"failed \(exit code 1\)"):
            await convert_with_libreoffice(b"doc data", "doc", "docx")


@pytest.mark.asyncio
async def test_raises_on_timeout() -> None:
    which_proc = _fake_proc(returncode=0)
    convert_proc = _fake_proc(returncode=0)

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=[which_proc, convert_proc]),
    ), patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError())):
        with pytest.raises(DocumentProcessingError, match="timed out"):
            await convert_with_libreoffice(b"doc data", "doc", "docx")

    convert_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_raises_when_output_file_missing() -> None:
    which_proc = _fake_proc(returncode=0)
    convert_proc = _fake_proc(returncode=0)

    with patch(
        "asyncio.create_subprocess_exec",
        AsyncMock(side_effect=[which_proc, convert_proc]),
    ), patch("os.path.exists", return_value=False):
        with pytest.raises(DocumentProcessingError, match="output file not found"):
            await convert_with_libreoffice(b"doc data", "doc", "docx")


@pytest.mark.asyncio
async def test_success_returns_output_bytes() -> None:
    which_proc = _fake_proc(returncode=0)
    convert_proc = _fake_proc(returncode=0)
    fake_output = b"PK\x03\x04fake docx bytes"

    async def _fake_exec(*args: object, **_kwargs: object):
        if args[:2] == ("which", "libreoffice"):
            return which_proc
        # Simulate LibreOffice writing the converted file into --outdir.
        outdir = args[args.index("--outdir") + 1]
        (Path(outdir) / "input.docx").write_bytes(fake_output)
        return convert_proc

    with patch("asyncio.create_subprocess_exec", _fake_exec):
        result = await convert_with_libreoffice(b"doc data", "doc", "docx")

    assert result == fake_output


def test_unreadable_file_is_reported_as_parse_error() -> None:
    original = LibreOfficeCouldNotReadFileError("nope", details={"reason": "bad"})

    with pytest.raises(ParseError) as exc_info:
        with unreadable_file_as_parse_error("docx"):
            raise original

    parse_error = exc_info.value
    assert parse_error.code == ParseErrorCode.PARSE_FAILED
    assert ".docx" in parse_error.message
    assert parse_error.details == {"reason": "bad"}
    assert parse_error.__cause__ is original


def test_other_errors_propagate_unchanged() -> None:
    with pytest.raises(RuntimeError, match="not installed") as exc_info:
        with unreadable_file_as_parse_error("docx"):
            raise RuntimeError("not installed")

    assert not isinstance(exc_info.value, ParseError)


def _run_libreoffice_as(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> tuple[list[asyncio.subprocess.Process], asyncio.Event]:
    """Answer ``which`` as installed and run ``script`` in place of LibreOffice."""
    started: list[asyncio.subprocess.Process] = []
    running = asyncio.Event()
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        if args[:2] == ("which", "libreoffice"):
            return _fake_proc(returncode=0)
        process = await real_exec("/bin/sh", "-c", script, **kwargs)
        started.append(process)
        running.set()
        return process

    monkeypatch.setattr(libreoffice_convert.asyncio, "create_subprocess_exec", fake_exec)
    return started, running


@pytest.mark.asyncio
async def test_a_cancelled_conversion_stops_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    started, running = _run_libreoffice_as(monkeypatch, "exec sleep 60")

    task = asyncio.create_task(convert_with_libreoffice(b"doc data", "doc", "docx"))
    await asyncio.wait_for(running.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started[0].returncode is not None


@pytest.mark.asyncio
async def test_a_timed_out_conversion_that_ignores_terminate_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    started, _ = _run_libreoffice_as(monkeypatch, "trap '' TERM; exec sleep 60")
    monkeypatch.setattr(libreoffice_convert, "LIBREOFFICE_CONVERT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(libreoffice_convert, "_STOP_GRACE_SECONDS", 0.5)

    with pytest.raises(DocumentProcessingError, match="timed out") as caught:
        await DocParser().convert_doc_to_docx_async(b"doc data")

    assert started[0].returncode is not None
    # The indexer runs this conversion in-process: a timeout must not be retried forever.
    assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TERMINAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "convert",
    [DocParser().convert_doc_to_docx_async, PPTParser().convert_ppt_to_pptx_async],
    ids=["doc", "ppt"],
)
async def test_an_unreadable_file_converted_in_process_is_terminal(convert) -> None:
    with patch(
        "app.utils.libreoffice_convert._run_subprocess", AsyncMock(return_value=(0, b""))
    ), patch(
        "app.utils.libreoffice_convert._run_libreoffice",
        AsyncMock(side_effect=LibreOfficeCouldNotReadFileError("source file could not be loaded")),
    ), pytest.raises(ParseError) as caught:
        await convert(b"not really an office file")

    assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TERMINAL


@pytest.mark.asyncio
async def test_concurrent_conversions_never_exceed_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(libreoffice_convert, "_conversion_slots", CrossLoopSemaphore(2))
    running = 0
    peak = 0

    class _Process:
        returncode = 0

        def __init__(self, outdir: str) -> None:
            self._outdir = outdir

        async def communicate(self) -> tuple[bytes, bytes]:
            nonlocal running
            await asyncio.sleep(0.01)
            (Path(self._outdir) / "input.docx").write_bytes(b"docx")
            running -= 1
            return b"", b""

    async def fake_exec(*args: str, **_kwargs: object) -> object:
        nonlocal running, peak
        if args[:2] == ("which", "libreoffice"):
            return _fake_proc(returncode=0)
        running += 1
        peak = max(peak, running)
        return _Process(args[args.index("--outdir") + 1])

    monkeypatch.setattr(libreoffice_convert.asyncio, "create_subprocess_exec", fake_exec)

    results = await asyncio.gather(*(convert_with_libreoffice(b"doc", "doc", "docx") for _ in range(6)))

    assert results == [b"docx"] * 6
    assert peak == 2
