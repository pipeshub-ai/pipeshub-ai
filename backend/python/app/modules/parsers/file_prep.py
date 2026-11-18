"""What the indexer does to a file's bytes before either pipeline parses them.

Runs once, ahead of the in-process parsers and the parsing service alike, for the types whose
bytes need preparing: the password check first, on the file as it arrived; then a macro-enabled
or template Office package becomes the plain package it holds, and a log or config file becomes
capped UTF-8 text. A binary file named .log or .cfg comes back as None.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config.file_types import FileType, Prep
from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.encryption import (
    PASSWORD_PROTECTED_MESSAGE,
    has_encrypted_package,
    raise_if_odf_encrypted,
)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_OCTET = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class PreparedFile:
    content: bytes | str
    extension: str | None
    mime_type: str | None
    truncated: bool = False  # only the first and last part of a .log/.cfg is kept


def needs_preparing(file_type: FileType | None) -> bool:
    return file_type is not None and (file_type.prep is not Prep.NONE or file_type.odf_container)


async def prepare_file(
    content: bytes | str, file_type: FileType, *, extension: str | None, mime_type: str | None
) -> PreparedFile | None:
    """*content* as the parsers should read it, and the extension and mime to read it under."""
    from app.modules.parsers.ooxml.main_part import as_plain_package
    from app.modules.parsers.text.capped_text import BinaryFile, prepare_capped_text

    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    label = file_type.extensions[0]
    if file_type.ooxml_container and has_encrypted_package(data):
        raise PasswordProtectedError(PASSWORD_PROTECTED_MESSAGE, details={"format": label})
    if file_type.odf_container:
        await asyncio.to_thread(raise_if_odf_encrypted, data)
    if file_type.prep is Prep.OOXML_WORD:
        return PreparedFile(await asyncio.to_thread(as_plain_package, data, "word", label=label), "docx", _DOCX_MIME)
    if file_type.prep is Prep.OOXML_SLIDES:
        return PreparedFile(await asyncio.to_thread(as_plain_package, data, "slides", label=label), "pptx", _PPTX_MIME)
    if file_type.prep is Prep.CAPPED_TEXT:
        prepared = await asyncio.to_thread(prepare_capped_text, data)
        if isinstance(prepared, BinaryFile):
            return None
        # The bytes are now the capped text only the log/cfg reader reads; no stored mime may
        # send them to another parser.
        # With no extension of its own (the event's "unknown"), the parsing service still has to
        # find the log/cfg reader.
        if not extension or extension == "unknown":
            extension = label
        return PreparedFile(prepared.text.encode("utf-8"), extension, _OCTET, prepared.truncated)
    return PreparedFile(content, extension, mime_type)


async def bound_text(content: bytes | str) -> tuple[bytes | str, bool]:
    """A .log/.cfg read by another reader (text, code, JSON...), held to the same size cap.

    Unchanged when it fits; else its first and last lines as UTF-8 text, and True. The reader is
    the one the file always had, binary or not.
    """
    from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text

    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    prepared = await asyncio.to_thread(prepare_capped_text, data, refuse_binary=False)
    if not isinstance(prepared, CappedText) or not prepared.truncated:
        return content, False
    return prepared.text.encode("utf-8"), True
