"""Async LibreOffice document conversion.

DocParser, PPTParser and XLSParser each convert legacy Office formats
(.doc/.ppt/.xls) to their OOXML equivalents via headless LibreOffice before
handing off to the OOXML-capable parser. The synchronous versions of that
conversion (``subprocess.run``) live on each of those classes for backward
compatibility with callers outside the standalone parsing service (e.g.
``app/events/processor.py``, ``app/agents/actions/util/parse_file.py``).

This module provides the async equivalent — ``asyncio.create_subprocess_exec``
instead of ``subprocess.run`` — for use inside the parsing service, where
LibreOffice's ~seconds-long runtime must not block the event loop nor
occupy a slot in the bounded parsing thread pool.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    UnsupportedFormatError,
)
from app.utils.user_errors import unsupported_file_type

if TYPE_CHECKING:
    from collections.abc import Iterator

LIBREOFFICE_CONVERT_TIMEOUT_SECONDS = 60
# Printed by LibreOffice (desktop/source/app/dispatchwatcher.cxx) when loading
# the input leaves no document: a damaged file, but also a missing import filter
# or an I/O error, so it only blames the file once the probe below passes.
_SOURCE_NOT_LOADED = "source file could not be loaded"

logger = logging.getLogger(__name__)

_FLAT_ODF = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.{kind}">'
    "<office:body>{body}</office:body></office:document>"
)
_FODT = _FLAT_ODF.format(kind="text", body="<office:text><text:p>probe</text:p></office:text>")
_FODS = _FLAT_ODF.format(
    kind="spreadsheet",
    body='<office:spreadsheet><table:table table:name="S"><table:table-row><table:table-cell>'
    "<text:p>probe</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>",
)
_FODP = _FLAT_ODF.format(
    kind="presentation", body='<office:presentation><draw:page draw:name="p1"/></office:presentation>'
)

# A known-good file of each input format is made by exporting a flat ODF sample
# to it, so the probe needs no binary fixtures and exercises the same component.
_PROBE_SAMPLES: dict[str, tuple[str, str]] = {
    "doc": ("fodt", _FODT),
    "xls": ("fods", _FODS),
    "ppt": ("fodp", _FODP),
}
# Formats LibreOffice can write but not open, in any release (its only EPUB
# filter is export-only). Converting from them is refused before LibreOffice
# runs, as an unsupported format rather than a retryable failure.
_IMPORT_UNSUPPORTED = frozenset({"epub"})

# A passing probe is kept for the life of the process. A failing one is retried
# after a while, so one bad moment does not keep every file of that type retrying.
_PROBE_FAILURE_TTL_SECONDS = 300.0
_format_probe_results: dict[tuple[str, str], tuple[bool, float]] = {}


class LibreOfficeCouldNotReadFileError(DocumentProcessingError):
    """LibreOffice ran but could not open or convert this particular file."""


@contextmanager
def unreadable_file_as_parse_error(input_ext: str) -> Iterator[None]:
    """Report a file LibreOffice cannot read as a parse failure of that file.

    Left as a plain exception it reaches the parsing service as a 500, which
    the indexer retries and counts against its circuit breaker as an outage.
    Every other LibreOffice failure (not installed, timed out, killed, profile
    or disk errors) propagates unchanged and is retried: those are about the
    deployment or the load, not the file.
    """
    try:
        yield
    except LibreOfficeCouldNotReadFileError as exc:
        raise ParseError(
            ParseErrorCode.PARSE_FAILED,
            f"LibreOffice could not read this .{input_ext} file. It may be damaged "
            f"or not really a .{input_ext} file. Open it in the app that made it, "
            "save a fresh copy, and upload it again.",
            details=exc.details,
        ) from exc


async def _run_subprocess(*args: str) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode or 0, stderr


@dataclass
class _Run:
    returncode: int | None
    stdout: str
    stderr: str
    output: bytes | None


async def _run_libreoffice(binary: bytes, input_ext: str, output_ext: str, stem: str = "input") -> _Run:
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = os.path.join(temp_dir, f"{stem}.{input_ext}")
        output_path = os.path.join(temp_dir, f"{stem}.{output_ext}")

        await asyncio.to_thread(Path(input_path).write_bytes, binary)

        # A private profile per conversion: runs that share the default profile
        # lock each other out, and the loser fails or exits having written nothing.
        profile_url = Path(temp_dir, "lo-profile").as_uri()
        convert_proc = await asyncio.create_subprocess_exec(
            "libreoffice",
            f"-env:UserInstallation={profile_url}",
            "--headless",
            "--convert-to",
            output_ext,
            "--outdir",
            temp_dir,
            input_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                convert_proc.communicate(), timeout=LIBREOFFICE_CONVERT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as e:
            convert_proc.kill()
            await convert_proc.wait()
            raise DocumentProcessingError(
                f"LibreOffice conversion timed out after {LIBREOFFICE_CONVERT_TIMEOUT_SECONDS} seconds",
                details={"timeout": f"{LIBREOFFICE_CONVERT_TIMEOUT_SECONDS}s"},
            ) from e

        output = None
        if os.path.exists(output_path):
            output = await asyncio.to_thread(Path(output_path).read_bytes)
        return _Run(
            returncode=convert_proc.returncode,
            stdout=(stdout or b"").decode("utf-8", errors="replace"),
            stderr=(stderr or b"").decode("utf-8", errors="replace"),
            output=output,
        )


async def _libreoffice_can_convert(input_ext: str, output_ext: str) -> bool | None:
    """Whether LibreOffice on this host converts a known-good *input_ext* file
    to *output_ext*, or None when there is no sample to check that format with.
    Only run after a conversion has already failed."""
    sample = _PROBE_SAMPLES.get(input_ext)
    if sample is None:
        return None
    key = (input_ext, output_ext)
    now = time.monotonic()
    cached = _format_probe_results.get(key)
    if cached is not None and (cached[0] or now - cached[1] < _PROBE_FAILURE_TTL_SECONDS):
        return cached[0]

    sample_ext, sample_text = sample
    ok = False
    try:
        made = await _run_libreoffice(sample_text.encode("utf-8"), sample_ext, input_ext, stem="probe")
        if made.returncode == 0 and made.output:
            read = await _run_libreoffice(made.output, input_ext, output_ext, stem="probe")
            ok = read.returncode == 0 and read.output is not None
    # Any failure here, expected or not, means support could not be confirmed.
    except Exception:
        logger.debug("LibreOffice probe for .%s -> .%s raised", input_ext, output_ext, exc_info=True)
    _format_probe_results[key] = (ok, now)
    return ok


async def convert_with_libreoffice(binary: bytes, input_ext: str, output_ext: str) -> bytes:
    """Convert *binary* from *input_ext* to *output_ext* via headless LibreOffice.

    Runs the LibreOffice subprocess without blocking the calling event loop.
    Temp-file I/O runs on the default executor so it doesn't block the loop
    either.

    Raises:
        UnsupportedFormatError: *input_ext* is a format LibreOffice cannot open
            in any release (EPUB); LibreOffice is not started.
        LibreOfficeCouldNotReadFileError: LibreOffice could not load this file
            (its "could not be loaded" message, or a clean exit with no output
            and no other diagnostics), and it does load a known-good file of
            the same format on this host.
        DocumentProcessingError: LibreOffice is missing, times out, is killed,
            cannot load this format at all, or fails for any other reason.
    """
    if input_ext in _IMPORT_UNSUPPORTED:
        raise UnsupportedFormatError(input_ext, unsupported_file_type(input_ext))

    which_code, which_stderr = await _run_subprocess("which", "libreoffice")
    if which_code != 0:
        raise DocumentProcessingError(
            "LibreOffice is not installed. Please install it using: sudo apt-get install libreoffice",
            details={"stderr": which_stderr.decode("utf-8", errors="replace")},
        )

    run = await _run_libreoffice(binary, input_ext, output_ext)
    returncode = run.returncode
    details = {"exit_code": returncode, "stdout": run.stdout, "stderr": run.stderr}
    if returncode is not None and returncode < 0:
        raise DocumentProcessingError(
            f"LibreOffice was stopped by signal {-returncode} while converting to .{output_ext}",
            details=details,
        )
    if returncode == 0 and run.output is not None:
        return run.output

    if returncode != 0:
        message = f"LibreOffice conversion to .{output_ext} failed (exit code {returncode})"
    else:
        message = f"{output_ext.upper()} conversion failed - output file not found"

    # Anything other than LibreOffice's own load-failure message (a profile or
    # disk problem, a crash, a failed export) is about the host, not the file.
    load_failed = _SOURCE_NOT_LOADED in run.stderr
    if not load_failed and (returncode != 0 or run.stderr.strip()):
        raise DocumentProcessingError(message, details=details)

    can_convert = await _libreoffice_can_convert(input_ext, output_ext)
    if can_convert is None:
        raise DocumentProcessingError(
            f"{message}; there is no known-good .{input_ext} sample to tell a damaged file "
            "from a LibreOffice problem, so it is treated as retryable",
            details=details,
        )
    if not can_convert:
        logger.warning(
            "LibreOffice could not convert a known-good .%s sample to .%s on this server, "
            "so the LibreOffice component that reads .%s files looks missing or broken. "
            "These files will be retried instead of being marked damaged.",
            input_ext, output_ext, input_ext,
        )
        raise DocumentProcessingError(
            f"{message}; LibreOffice on this server could not convert a known-good .{input_ext} file either",
            details=details,
        )
    raise LibreOfficeCouldNotReadFileError(message, details=details)
