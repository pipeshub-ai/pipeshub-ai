"""PDF conversion keeps the source name inside a private temp directory."""

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.utils import pdf_stream_conversion
from app.utils.pdf_stream_conversion import (
    convert_file_to_pdf_stream,
    libreoffice_to_pdf,
    safe_conversion_input,
)


def test_a_traversal_filename_stays_inside_the_temp_directory(tmp_path: Path) -> None:
    path = safe_conversion_input(str(tmp_path), "../../x.docx")

    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(tmp_path)
    assert ".." not in os.path.basename(path)
    assert path.endswith(".xdocx")


async def test_the_temp_directory_is_removed_after_success_and_after_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    real_mkdtemp = pdf_stream_conversion.tempfile.mkdtemp

    def tracking_mkdtemp() -> str:
        directory = real_mkdtemp(dir=tmp_path)
        created.append(directory)
        return directory

    async def convert(file_path: str, temp_dir: str, *, timeout: float = 60) -> str:
        pdf_path = os.path.join(temp_dir, "out.pdf")
        Path(pdf_path).write_bytes(b"%PDF-1.4")
        return pdf_path

    monkeypatch.setattr(pdf_stream_conversion.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(pdf_stream_conversion, "libreoffice_to_pdf", convert)

    async def write_source(path: str) -> None:
        Path(path).write_bytes(b"source")

    body = b"".join([chunk async for chunk in convert_file_to_pdf_stream("docx", write_source)])
    assert body == b"%PDF-1.4"
    assert created
    assert not os.path.exists(created[0])

    async def failing_write(_path: str) -> None:
        raise RuntimeError("download failed")

    with pytest.raises(RuntimeError):
        _ = [chunk async for chunk in convert_file_to_pdf_stream("docx", failing_write)]
    assert not os.path.exists(created[1])


async def test_concurrent_conversions_use_separate_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    class _Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args: str, **_kwargs: object) -> _Process:
        commands.append(list(args))
        outdir = args[args.index("--outdir") + 1]
        Path(os.path.join(outdir, f"{Path(args[-1]).stem}.pdf")).write_bytes(b"%PDF")
        return _Process()

    monkeypatch.setattr(pdf_stream_conversion.asyncio, "create_subprocess_exec", fake_exec)
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "one.bin").write_bytes(b"a")
    (second / "two.bin").write_bytes(b"b")

    await libreoffice_to_pdf(str(first / "one.bin"), str(first))
    await libreoffice_to_pdf(str(second / "two.bin"), str(second))

    profiles = [arg for command in commands for arg in command if arg.startswith("-env:UserInstallation=")]
    assert len(profiles) == 2
    assert profiles[0] != profiles[1]
    assert all(command[0] == "soffice" for command in commands)
    assert all("--outdir" in command for command in commands)


async def test_a_missing_pdf_raises_the_output_not_found_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*_args: str, **_kwargs: object) -> _Process:
        return _Process()

    monkeypatch.setattr(pdf_stream_conversion.asyncio, "create_subprocess_exec", fake_exec)
    source = tmp_path / "file.bin"
    source.write_bytes(b"a")

    with pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path))

    assert raised.value.detail == "PDF conversion failed - output file not found"


def _run_instead_of_soffice(
    monkeypatch: pytest.MonkeyPatch, script: str
) -> tuple[list[asyncio.subprocess.Process], asyncio.Event]:
    started: list[asyncio.subprocess.Process] = []
    running = asyncio.Event()
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*_args: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real_exec("/bin/sh", "-c", script, **kwargs)
        started.append(process)
        running.set()
        return process

    monkeypatch.setattr(pdf_stream_conversion.asyncio, "create_subprocess_exec", fake_exec)
    return started, running


async def test_a_cancelled_conversion_stops_libreoffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started, running = _run_instead_of_soffice(monkeypatch, "exec sleep 60")
    source = tmp_path / "file.bin"
    source.write_bytes(b"a")

    task = asyncio.create_task(libreoffice_to_pdf(str(source), str(tmp_path)))
    await asyncio.wait_for(running.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started[0].returncode is not None


async def test_a_conversion_that_ignores_terminate_is_killed_and_reaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started, _ = _run_instead_of_soffice(monkeypatch, "trap '' TERM; exec sleep 60")
    monkeypatch.setattr(pdf_stream_conversion, "_STOP_GRACE_SECONDS", 0.5)
    source = tmp_path / "file.bin"
    source.write_bytes(b"a")

    with pytest.raises(HTTPException) as raised:
        await libreoffice_to_pdf(str(source), str(tmp_path), timeout=0.5)

    assert raised.value.detail == "PDF conversion timed out"
    assert started[0].returncode is not None
