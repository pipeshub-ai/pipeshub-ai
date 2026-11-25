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
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.utils import libreoffice_convert

PDF_CONVERT_CHUNK_BYTES = 1024 * 1024
DEFAULT_PDF_TIMEOUT_SECONDS = 60.0

logger = logging.getLogger(__name__)

WriteSource = Callable[[str], Awaitable[None]]

logger = logging.getLogger(__name__)

# The web app and the Node proxy key off these to offer the original file
# instead of a broken preview; the message is what a person reads.
CONVERTER_UNAVAILABLE = "converter_unavailable"
CONVERSION_FAILED = "conversion_failed"
CONVERSION_TIMED_OUT = "conversion_timed_out"


def _conversion_error(status: HttpStatusCode, reason_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.value,
        detail={"reasonCode": reason_code, "message": message},
    )


def document_conversion_available() -> bool:
    """Whether the LibreOffice binary previews convert with is on PATH."""
    return shutil.which("soffice") is not None


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

    Raises ``HTTPException`` with a ``{reasonCode, message}`` detail when
    LibreOffice is missing (503 ``converter_unavailable``), times out (504),
    fails, or writes nothing (500); each is logged with its cause. A per-run profile is passed so two conversions do not share the
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

    async with libreoffice_convert.conversion_slots.slot():
        try:
            # Its own process group, so a stop reaches soffice.bin and not only the launcher.
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as error:
            logger.error(
                "PDF conversion is unavailable: LibreOffice (soffice) is not installed on this server",
                exc_info=True,
            )
            raise _conversion_error(
                HttpStatusCode.SERVICE_UNAVAILABLE,
                CONVERTER_UNAVAILABLE,
                "Preview isn't available on this server because document conversion is not installed. "
                "Download the file to open it.",
            ) from error
        except Exception as error:
            logger.error("PDF conversion could not start LibreOffice", exc_info=True)
            raise _conversion_error(
                HttpStatusCode.INTERNAL_SERVER_ERROR, CONVERSION_FAILED, "Error converting file to PDF"
            ) from error

        try:
            _stdout, conversion_error = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as error:
            await libreoffice_convert.stop_process(process)
            logger.error("PDF conversion timed out after %ss", timeout)
            raise _conversion_error(
                HttpStatusCode.GATEWAY_TIMEOUT, CONVERSION_TIMED_OUT, "PDF conversion timed out"
            ) from error
        except asyncio.CancelledError:
            # The caller removes temp_dir next; soffice must not outlive it.
            await libreoffice_convert.stop_process(process)
            raise

    if process.returncode != 0:
        logger.error(
            "LibreOffice PDF conversion failed (exit code %s): %s",
            process.returncode,
            (conversion_error or b"").decode("utf-8", errors="replace").strip(),
        )
        raise _conversion_error(
            HttpStatusCode.INTERNAL_SERVER_ERROR, CONVERSION_FAILED, "Failed to convert file to PDF"
        )

    if not os.path.exists(pdf_path):
        pdf_files = sorted(
            entry
            for entry in os.listdir(temp_dir)
            if entry.lower().endswith(".pdf")
        )
        if not pdf_files:
            logger.error("PDF conversion failed: soffice exited 0 but wrote no PDF")
            raise _conversion_error(
                HttpStatusCode.INTERNAL_SERVER_ERROR,
                CONVERSION_FAILED,
                "PDF conversion failed - output file not found",
            )
        pdf_path = os.path.join(temp_dir, pdf_files[0])
    return pdf_path


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
