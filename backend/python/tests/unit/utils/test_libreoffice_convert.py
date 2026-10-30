"""Unit tests for app.utils.libreoffice_convert.convert_with_libreoffice."""
from __future__ import annotations

import asyncio
import os
import signal
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
from app.utils.pdf_stream_conversion import libreoffice_to_pdf


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
async def test_stop_process_kills_a_child_that_outlives_its_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    # Like oosplash -> soffice.bin: the launcher dies on TERM, its child ignores TERM.
    monkeypatch.setattr(libreoffice_convert, "_STOP_GRACE_SECONDS", 0.5)
    launcher = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", "sh -c \"trap '' TERM; echo ready; exec sleep 60\" & wait",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert await asyncio.wait_for(launcher.stdout.readline(), timeout=5) == b"ready\n"

    await libreoffice_convert.stop_process(launcher)

    assert launcher.returncode == -signal.SIGTERM
    # The child holds the launcher's stdout, so EOF means it is dead too.
    assert await asyncio.wait_for(launcher.stdout.read(), timeout=5) == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pid",
    [MagicMock(), 0, 1, "own group"],
    ids=["mock-pid", "zero", "init", "own-group"],
)
@pytest.mark.parametrize(
    ("exits_on_terminate", "killed"),
    [(True, False), (False, True)],
    ids=["exits", "ignores-terminate"],
)
async def test_stop_process_never_signals_a_group_it_does_not_lead(
    pid: object, exits_on_terminate: bool, killed: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A MagicMock pid indexes as 1: killpg(1) signalled init's group and took down a CI worker.
    monkeypatch.setattr(libreoffice_convert, "_STOP_GRACE_SECONDS", 0.01)
    wait = AsyncMock(return_value=0) if exits_on_terminate else AsyncMock(side_effect=asyncio.TimeoutError)
    process = MagicMock(pid=os.getpgrp() if pid == "own group" else pid, wait=wait)

    with patch.object(libreoffice_convert.os, "killpg") as killpg:
        await libreoffice_convert.stop_process(process)

    killpg.assert_not_called()
    process.terminate.assert_called_once()
    assert process.kill.called is killed


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
async def test_concurrent_conversions_of_both_kinds_share_one_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Office -> docx (indexing, agents) and -> PDF (Gmail, Drive previews) take slots from one cap.
    monkeypatch.setattr(libreoffice_convert, "conversion_slots", CrossLoopSemaphore(2))
    running = 0
    peak = 0

    class _Process:
        returncode = 0

        def __init__(self, output: Path) -> None:
            self._output = output

        async def communicate(self) -> tuple[bytes, bytes]:
            nonlocal running
            await asyncio.sleep(0.01)
            self._output.write_bytes(self._output.suffix.encode())
            running -= 1
            return b"", b""

    async def fake_exec(*args: str, **_kwargs: object) -> object:
        nonlocal running, peak
        if args[:2] == ("which", "libreoffice"):
            return _fake_proc(returncode=0)
        running += 1
        peak = max(peak, running)
        outdir, target = Path(args[args.index("--outdir") + 1]), args[args.index("--convert-to") + 1]
        return _Process(outdir / f"{Path(args[-1]).stem}.{target}")

    monkeypatch.setattr(libreoffice_convert.asyncio, "create_subprocess_exec", fake_exec)

    async def to_pdf(n: int) -> str:
        temp_dir = tmp_path / str(n)
        temp_dir.mkdir()
        return await libreoffice_to_pdf(str(temp_dir / "attachment.docx"), str(temp_dir))

    results = await asyncio.gather(
        *(convert_with_libreoffice(b"doc", "doc", "docx") for _ in range(3)),
        *(to_pdf(n) for n in range(3)),
    )

    assert results[:3] == [b".docx"] * 3
    assert [Path(pdf).name for pdf in results[3:]] == ["attachment.pdf"] * 3
    assert peak == 2
