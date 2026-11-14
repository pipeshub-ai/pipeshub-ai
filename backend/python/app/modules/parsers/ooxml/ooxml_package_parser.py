"""The parsing service's parser for .docm, .dotx and .potx: the plain package, read by Docling."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.encryption import (
    PASSWORD_PROTECTED_MESSAGE,
    has_encrypted_package,
)
from app.modules.parsers.ooxml.main_part import Family, as_plain_package

if TYPE_CHECKING:
    from app.services.parsing.interface import IParser, ParseResult

_PLAIN_EXTENSION: dict[Family, str] = {"word": "docx", "slides": "pptx"}


class OoxmlPackageParser:
    def __init__(self, docling_parser: IParser, family: Family) -> None:
        self._docling = docling_parser
        self._family = family

    async def parse(self, content: bytes, record_name: str, config: dict[str, Any] | None = None) -> ParseResult:
        label = Path(record_name).suffix.lstrip(".").lower() or _PLAIN_EXTENSION[self._family]
        if has_encrypted_package(content):
            raise PasswordProtectedError(PASSWORD_PROTECTED_MESSAGE, details={"format": label})
        plain = await asyncio.to_thread(as_plain_package, content, self._family, label=label)
        name = f"{Path(record_name).stem}.{_PLAIN_EXTENSION[self._family]}"
        return await self._docling.parse(plain, name, config)

    def supported_formats(self) -> list[str]:
        return ["docm", "dotx"] if self._family == "word" else ["potx"]
