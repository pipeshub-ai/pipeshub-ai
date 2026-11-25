"""The one explicit extension-to-type table for files the harness produces (sandbox outputs, engine-published files).

It cannot import ``app.config.file_types`` (K6); ``test_harness_mime_pinned`` holds each entry to that registry.

Python's built-in ``mimetypes`` table has no Office types, and a host's ``/etc/mime.types`` may be missing, so a
file's type never depends on either for the formats people open.
"""

from __future__ import annotations

import mimetypes
import posixpath

from harness_core.archive_reader import ArchiveLimits, ArchiveRefused, open_zip

OCTET_STREAM = "application/octet-stream"

EXTENSION_TO_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
}

# Leading bytes of the formats people open, for a file whose name says nothing (no extension, or one no table knows).
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# An Office Open XML file is a zip whose parts sit under one folder per application.
_OOXML_FOLDERS: tuple[tuple[str, str], ...] = (
    ("ppt/", EXTENSION_TO_MIME[".pptx"]),
    ("word/", EXTENSION_TO_MIME[".docx"]),
    ("xl/", EXTENSION_TO_MIME[".xlsx"]),
)


def mime_for(name: str, content: bytes | None = None) -> str:
    """A file's type: the explicit table by extension, then the host's table, then the leading bytes of ``content``."""
    extension = posixpath.splitext(name.lower())[1]
    if extension in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[extension]
    guessed = mimetypes.guess_type(name)[0] if extension else None
    if guessed:
        return guessed
    return _sniffed(content) if content else OCTET_STREAM


def _sniffed(content: bytes) -> str:
    for signature, mime in _SIGNATURES:
        if content.startswith(signature):
            return mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"PK\x03\x04"):
        return _zipped(content)
    return OCTET_STREAM


# Only the central directory is read, never a member; the count is bounded because each entry parsed costs memory.
_SNIFF_LIMITS = ArchiveLimits(max_total_bytes=1, max_entry_bytes=1, max_entries=50_000)


def _zipped(content: bytes) -> str:
    try:
        with open_zip(content, _SNIFF_LIMITS) as archive:
            names = archive.namelist()
    except ArchiveRefused:
        return OCTET_STREAM
    for folder, mime in _OOXML_FOLDERS:
        if any(n.startswith(folder) for n in names):
            return mime
    return EXTENSION_TO_MIME[".zip"]


__all__ = ["EXTENSION_TO_MIME", "OCTET_STREAM", "mime_for"]
