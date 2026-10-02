"""Headless LibreOffice conversion to PDF, safe to stream after the caller returns.

The converted file is written under a private temp directory whose name is a
random stem, never the name the user gave the file. A private LibreOffice
profile per run keeps concurrent conversions from locking each other out.
The directory is removed when the stream finishes or when conversion fails.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode

PDF_CONVERT_CHUNK_BYTES = 1024 * 1024
DEFAULT_PDF_TIMEOUT_SECONDS = 60.0

WriteSource = Callable[[str], Awaitable[None]]


def safe_conversion_input(directory: str, extension: str) -> str:
    """Path inside ``directory`` whose name is a random stem plus a safe extension."""
    cleaned = "".join(ch for ch in extension.lower() if ch.isalnum())[:10]
    name = f"{uuid4().hex}.{cleaned}" if cleaned else uuid4().hex
    path = os.path.join(directory, name)
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(directory):
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail="Invalid filename",
        )
    return path


async def libreoffice_to_pdf(
    file_path: str,
    temp_dir: str,
    *,
    timeout: float = DEFAULT_PDF_TIMEOUT_SECONDS,
) -> str:
    """Convert ``file_path`` to a PDF in ``temp_dir`` and return the PDF path.

    Raises ``HTTPException`` when LibreOffice is missing, times out, or writes
    nothing. A per-run profile is passed so two conversions do not share the
    default LibreOffice profile.
    """
    pdf_path = os.path.join(temp_dir, f"{Path(file_path).stem}.pdf")
    profile_uri = Path(os.path.join(temp_dir, ".libreoffice-profile")).as_uri()
    command = [
        "soffice",
        f"-env:UserInstallation={profile_uri}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        temp_dir,
        file_path,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, conversion_error = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as error:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="PDF conversion timed out",
            ) from error

        if process.returncode != 0:
            _ = conversion_error.decode("utf-8", errors="replace") if conversion_error else ""
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Failed to convert file to PDF",
            )

        if not os.path.exists(pdf_path):
            pdf_files = sorted(
                entry
                for entry in os.listdir(temp_dir)
                if entry.lower().endswith(".pdf")
            )
            if not pdf_files:
                raise HTTPException(
                    status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                    detail="PDF conversion failed - output file not found",
                )
            pdf_path = os.path.join(temp_dir, pdf_files[0])
        return pdf_path
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Error converting file to PDF",
        ) from error


async def stream_file_then_remove(
    path: str,
    directory: str,
    *,
    chunk_bytes: int = PDF_CONVERT_CHUNK_BYTES,
) -> AsyncGenerator[bytes, None]:
    """Yield the file in chunks, then delete ``directory``."""
    try:
        with open(path, "rb") as handle:
            while chunk := await asyncio.to_thread(handle.read, chunk_bytes):
                yield chunk
    finally:
        shutil.rmtree(directory, ignore_errors=True)


async def convert_file_to_pdf_stream(
    extension: str,
    write_source: WriteSource,
    *,
    timeout: float = DEFAULT_PDF_TIMEOUT_SECONDS,
) -> AsyncGenerator[bytes, None]:
    """Write a source file under a private directory, convert it, and stream the PDF.

    ``write_source`` receives the safe input path. The directory is removed when
    the stream ends and when conversion fails before the stream starts.
    """
    directory = tempfile.mkdtemp()
    try:
        input_path = safe_conversion_input(directory, extension)
        await write_source(input_path)
        pdf_path = await libreoffice_to_pdf(input_path, directory, timeout=timeout)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    async for chunk in stream_file_then_remove(pdf_path, directory):
        yield chunk
