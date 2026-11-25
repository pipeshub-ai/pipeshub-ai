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
import threading
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


async def _run_soffice(file_path: str, temp_dir: str, *, timeout: float) -> tuple[str | None, str]:
    """One LibreOffice run: the PDF's path and its stderr. The path is None when it
    could not load the file, or exited 0 without writing one."""
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
            conversion_output, conversion_error = await asyncio.wait_for(
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

    stderr = (conversion_error or b"").decode("utf-8", errors="replace").strip()
    # LibreOffice 26 exits 1 for a file it cannot load, earlier releases 0.
    not_loaded = libreoffice_convert.source_not_loaded(stderr) and process.returncode in (0, 1)
    if process.returncode != 0 and not not_loaded:
        logger.error(
            "LibreOffice PDF conversion failed (exit code %s): %r",
            process.returncode,
            stderr,
        )
        raise _conversion_error(
            HttpStatusCode.INTERNAL_SERVER_ERROR, CONVERSION_FAILED, "Failed to convert file to PDF"
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
    # LibreOffice's own output is the only account of what went wrong.
    # %r keeps it on one log line.
    logger.warning(
        "LibreOffice exited %s and wrote no PDF for a %r file. stdout: %r stderr: %r",
        process.returncode,
        Path(file_path).suffix,
        (conversion_output or b"").decode("utf-8", errors="replace"),
        stderr,
    )
    return None, stderr


async def _rebuild_package(source: str, target: str, *, timeout: float) -> bool:
    """Rebuild the package in a thread, within the conversion limit. The slot is
    held until the thread is done, also when the caller is cancelled."""
    stop = threading.Event()
    async with libreoffice_convert.conversion_slots.slot():
        worker = asyncio.ensure_future(asyncio.to_thread(rebuild_package, Path(source), Path(target), stop.is_set))
        try:
            return await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
        except asyncio.TimeoutError as error:
            logger.error("Rebuilding an Office package timed out after %ss", timeout)
            raise _conversion_error(
                HttpStatusCode.GATEWAY_TIMEOUT, CONVERSION_TIMED_OUT, "PDF conversion timed out"
            ) from error
        finally:
            # The caller removes the directory next; the thread must not outlive it.
            stop.set()
            cancelled = False
            while not worker.done():
                try:
                    await asyncio.wait([worker])
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError


async def libreoffice_to_pdf(
    file_path: str,
    temp_dir: str,
    *,
    timeout: float = DEFAULT_PDF_TIMEOUT_SECONDS,
) -> str:
    """Convert ``file_path`` to a PDF in ``temp_dir`` and return the PDF path.

    An Office package LibreOffice cannot load is rebuilt and tried once more.

    Raises ``HTTPException`` with a ``{reasonCode, message}`` detail when
    LibreOffice is missing (503 ``converter_unavailable``), times out (504),
    cannot load this file but loads a known-good one of its format (422), fails,
    or writes nothing (500); each is logged with its cause. A per-run profile is
    passed so two conversions do not share the default LibreOffice profile.
    """
    pdf_path, stderr = await _run_soffice(file_path, temp_dir, timeout=timeout)
    if pdf_path is not None:
        return pdf_path

    if libreoffice_convert.source_not_loaded(stderr):
        rebuilt = safe_conversion_input(temp_dir, Path(file_path).suffix)
        if await _rebuild_package(file_path, rebuilt, timeout=timeout):
            # Its own budget: time spent queueing for the first run is not the file's fault.
            pdf_path, stderr = await _run_soffice(rebuilt, temp_dir, timeout=timeout)
            if pdf_path is not None:
                logger.info("LibreOffice converted the file after its package was rebuilt")
                return pdf_path

    if libreoffice_convert.source_not_loaded(stderr) and await libreoffice_convert.can_convert_known_good_file(
        Path(file_path).suffix.lower().lstrip("."), "pdf"
    ):
        # The same answer every time, so a 4xx: callers do not retry it.
        raise _conversion_error(
            HttpStatusCode.UNPROCESSABLE_ENTITY, CONVERSION_FAILED, FILE_PREVIEW_UNAVAILABLE
        )
    logger.error("PDF conversion failed: soffice wrote no PDF. stderr: %r", stderr)
    raise _conversion_error(
        HttpStatusCode.INTERNAL_SERVER_ERROR,
        CONVERSION_FAILED,
        "PDF conversion failed - output file not found",
    )


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
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    async for chunk in stream_file_then_remove(pdf_path, directory):
        yield chunk
