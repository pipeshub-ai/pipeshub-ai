"""Headless LibreOffice conversion to PDF, safe to stream after the caller returns.

The converted file is written under a private temp directory whose name is a
random stem, never the name the user gave the file. A private LibreOffice
profile per run keeps concurrent conversions from locking each other out.
The directory is removed when the stream finishes or when conversion fails.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.utils import libreoffice_convert
from app.utils.ooxml_repair import rebuild_package
from app.utils.user_messages import FILE_PREVIEW_UNAVAILABLE

PDF_CONVERT_CHUNK_BYTES = 1024 * 1024
DEFAULT_PDF_TIMEOUT_SECONDS = 60.0

logger = logging.getLogger(__name__)

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


async def _run_soffice(file_path: str, temp_dir: str, *, timeout: float) -> tuple[str | None, str]:
    """One LibreOffice run: the PDF's path (None when it exited 0 without one) and its stderr."""
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

    async with libreoffice_convert.conversion_slots.slot():
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            conversion_output, conversion_error = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as error:
            await libreoffice_convert.stop_process(process)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="PDF conversion timed out",
            ) from error
        except asyncio.CancelledError:
            # The caller removes temp_dir next; soffice must not outlive it.
            await libreoffice_convert.stop_process(process)
            raise

    stderr = (conversion_error or b"").decode("utf-8", errors="replace")
    if process.returncode != 0:
        logger.error(
            "LibreOffice PDF conversion failed (exit code %s): %s",
            process.returncode,
            stderr,
        )
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to convert file to PDF",
        )

    if os.path.exists(pdf_path):
        return pdf_path, stderr
    pdf_files = sorted(
        entry
        for entry in os.listdir(temp_dir)
        if entry.lower().endswith(".pdf")
    )
    if pdf_files:
        return os.path.join(temp_dir, pdf_files[0]), stderr
    # LibreOffice exits 0 here: its own output is the only account of what went wrong.
    # %r keeps it on one log line.
    logger.warning(
        "LibreOffice exited 0 but wrote no PDF for a .%s file of %s bytes. stdout: %r stderr: %r",
        Path(file_path).suffix.lstrip("."),
        os.path.getsize(file_path),
        (conversion_output or b"").decode("utf-8", errors="replace"),
        stderr,
    )
    return None, stderr


async def libreoffice_to_pdf(
    file_path: str,
    temp_dir: str,
    *,
    timeout: float = DEFAULT_PDF_TIMEOUT_SECONDS,
) -> str:
    """Convert ``file_path`` to a PDF in ``temp_dir`` and return the PDF path.

    An Office package LibreOffice cannot load is rebuilt and tried once more.

    Raises ``HTTPException``: 422 when LibreOffice cannot load this file but
    loads a known-good one of its format, otherwise 500 (LibreOffice missing,
    timed out, or it wrote nothing for another reason). A per-run profile is
    passed so two conversions do not share the default LibreOffice profile.
    """
    try:
        started = time.monotonic()
        pdf_path, stderr = await _run_soffice(file_path, temp_dir, timeout=timeout)
        if pdf_path is not None:
            return pdf_path

        if libreoffice_convert.source_not_loaded(stderr):
            source = Path(file_path)
            rebuilt = source.with_name(f"{uuid4().hex}{source.suffix}")
            if await asyncio.to_thread(rebuild_package, source, rebuilt):
                remaining = max(1.0, timeout - (time.monotonic() - started))
                pdf_path, stderr = await _run_soffice(str(rebuilt), temp_dir, timeout=remaining)
                if pdf_path is not None:
                    logger.info("LibreOffice converted the file after its package was rebuilt")
                    return pdf_path

        if libreoffice_convert.source_not_loaded(stderr) and await libreoffice_convert.can_convert_known_good_file(
            Path(file_path).suffix.lower().lstrip("."), "pdf"
        ):
            raise HTTPException(
                status_code=HttpStatusCode.UNPROCESSABLE_ENTITY.value,
                detail=FILE_PREVIEW_UNAVAILABLE,
            )
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="PDF conversion failed - output file not found",
        )
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
