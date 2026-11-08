"""Telling that a file needs a password to open, from the file itself.

Two formats, two kinds of evidence, and neither reads an error message:

- PDF: pdfium's own status code for "password required". A PDF restricted by an
  owner password alone opens without one and is not affected.
- DOCX, XLSX and PPTX: a password turns the OOXML zip into an OLE compound file
  (CFBF) whose directory holds an ``EncryptedPackage`` stream. Only the 512-byte
  header, the FAT entries the directory chain walks through and the directory
  itself are read, never the content, so the cost does not grow with the file.

Legacy .doc, .xls and .ppt are not covered: their encryption flags sit inside
streams (the Word FIB, the BIFF ``FilePass`` record, the PowerPoint
``CurrentUser`` atom) and, for small files, inside the mini stream. They still
end as an unreadable file.

Runs in parse worker processes (see ``untrusted_pdf``): keep imports light.
"""
from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import PasswordProtectedError

if TYPE_CHECKING:
    from collections.abc import Collection

_logger = logging.getLogger(__name__)

_CFBF_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_HEADER_SIZE = 512
_END_OF_CHAIN = 0xFFFFFFFE
_MAX_REGULAR_SECTOR = 0xFFFFFFFA
_DIRECTORY_ENTRY_SIZE = 128
_STREAM_OBJECT = 2
_ENCRYPTED_PACKAGE = "EncryptedPackage"
# A directory this large is not an Office file: encrypted packages list about ten
# entries. Bounds what a hostile header can make the reader touch.
_MAX_DIRECTORY_SECTORS = 64
_MAX_DIFAT_SECTORS = 8

# Formats whose encrypted form is a CFBF holding an EncryptedPackage stream.
OOXML_EXTENSIONS = frozenset({"docx", "xlsx", "pptx", "docm", "xlsm", "pptm"})
OOXML_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
})

PASSWORD_PROTECTED_MESSAGE = "The file is password-protected, so it cannot be read"


def has_encrypted_package(content: bytes | memoryview) -> bool:
    """True when *content* is an OLE compound file with an ``EncryptedPackage`` stream.

    False for anything else, including a compound file that is damaged: a
    malformed container is left for the parser to report as unreadable. Never
    raises and reads a bounded part of *content*.
    """
    view = memoryview(content)
    if len(view) < _HEADER_SIZE or bytes(view[:8]) != _CFBF_MAGIC:
        return False
    try:
        return _ENCRYPTED_PACKAGE in _directory_stream_names(view)
    except (struct.error, ValueError, IndexError, OverflowError):
        _logger.debug("Not a readable OLE compound file; leaving it to the parser", exc_info=True)
        return False


def raise_if_password_protected_office(
    content: object, *, extension: str | None = None, mime_type: str | None = None
) -> None:
    """Raise ``PasswordProtectedError`` for an encrypted DOCX, XLSX or PPTX.

    Applies only to those formats, named by *extension* or *mime_type*; any other
    kind of file, and any non-bytes content, passes untouched.
    """
    if not isinstance(content, (bytes, bytearray, memoryview)):
        return
    ext = (extension or "").lower().lstrip(".")
    if ext not in OOXML_EXTENSIONS and (mime_type or "").lower() not in OOXML_MIME_TYPES:
        return
    if has_encrypted_package(content):
        raise PasswordProtectedError(PASSWORD_PROTECTED_MESSAGE, details={"format": ext or mime_type})


def pdfium_password_error(exc: BaseException) -> bool:
    """Whether *exc* is pdfium refusing a PDF for want of a password."""
    import pypdfium2
    import pypdfium2.raw as pdfium_raw

    if not isinstance(exc, pypdfium2.PdfiumError):
        return False
    # The code pdfium reported for this load, carried by the exception (pypdfium2 5+). Not
    # FPDF_GetLastError(): that is the library's state for whatever failed last in the thread.
    return getattr(exc, "err_code", None) == pdfium_raw.FPDF_ERR_PASSWORD


def raise_if_pdf_needs_password(content: bytes) -> None:
    """Raise ``PasswordProtectedError`` when pdfium needs a password to open *content*.

    A PDF pdfium cannot open for another reason, or opens, passes. Only worth
    calling once another reader has failed on the file: it opens the PDF again.
    """
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(content)
    except Exception as exc:
        if pdfium_password_error(exc):
            raise PasswordProtectedError(
                PASSWORD_PROTECTED_MESSAGE, details={"format": "pdf"}
            ) from exc
        return
    document.close()


def _directory_stream_names(view: memoryview) -> Collection[str]:
    sector_shift = struct.unpack_from("<H", view, 30)[0]
    if sector_shift not in (9, 12):
        raise ValueError("unsupported sector size")
    sector_size = 1 << sector_shift
    first_directory_sector = struct.unpack_from("<I", view, 48)[0]
    first_difat_sector, difat_count = struct.unpack_from("<II", view, 68)

    def sector(index: int) -> memoryview:
        start = (index + 1) << sector_shift
        end = start + sector_size
        if index > _MAX_REGULAR_SECTOR or end > len(view):
            raise ValueError("sector outside the file")
        return view[start:end]

    fat_sectors = list(struct.unpack_from("<109I", view, 76))
    next_difat = first_difat_sector
    for _ in range(min(difat_count, _MAX_DIFAT_SECTORS)):
        if next_difat > _MAX_REGULAR_SECTOR:
            break
        data = sector(next_difat)
        per_sector = sector_size // 4 - 1
        fat_sectors.extend(struct.unpack_from(f"<{per_sector}I", data, 0))
        next_difat = struct.unpack_from("<I", data, per_sector * 4)[0]

    entries_per_fat_sector = sector_size // 4

    def next_in_chain(index: int) -> int:
        fat_index, offset = divmod(index, entries_per_fat_sector)
        if fat_index >= len(fat_sectors) or fat_sectors[fat_index] > _MAX_REGULAR_SECTOR:
            raise ValueError("sector missing from the FAT")
        return struct.unpack_from("<I", sector(fat_sectors[fat_index]), offset * 4)[0]

    names: list[str] = []
    visited: set[int] = set()
    current = first_directory_sector
    while current != _END_OF_CHAIN:
        if current in visited or len(visited) >= _MAX_DIRECTORY_SECTORS:
            raise ValueError("directory chain loops or is too long")
        visited.add(current)
        data = sector(current)
        for start in range(0, sector_size, _DIRECTORY_ENTRY_SIZE):
            entry = data[start:start + _DIRECTORY_ENTRY_SIZE]
            name_bytes, object_type = struct.unpack_from("<H", entry, 64)[0], entry[66]
            if object_type != _STREAM_OBJECT or not 2 <= name_bytes <= 64:
                continue
            names.append(bytes(entry[:name_bytes - 2]).decode("utf-16-le", errors="replace"))
        current = next_in_chain(current)
    return names
