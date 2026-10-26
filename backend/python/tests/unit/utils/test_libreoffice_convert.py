"""Unit tests for app.utils.libreoffice_convert.convert_with_libreoffice."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.parsing.interface import ParseError, ParseErrorCode
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
