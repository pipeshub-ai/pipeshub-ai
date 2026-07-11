"""Parser provider using the local (in-process) Docling library.

Handles PDF, DOCX, PPTX, MD without an external HTTP call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)

_SUPPORTED_FORMATS = ["pdf", "docx", "doc", "pptx", "ppt", "md", "mdx"]

_FORMAT_EXTS = {
    "pdf": ".pdf",
    "docx": ".docx",
    "doc": ".docx",   # converted upstream
    "pptx": ".pptx",
    "ppt": ".pptx",   # converted upstream
    "md": ".md",
    "mdx": ".md",
}


class LocalDoclingParser:
    """Wraps :class:`DoclingProcessor` for in-process Docling parsing."""

    def __init__(self, docling_processor: DoclingProcessor) -> None:
        self._processor = docling_processor

    def supported_formats(self) -> list[str]:
        return list(_SUPPORTED_FORMATS)

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        ext = Path(record_name).suffix.lower().lstrip(".")
        target_ext = _FORMAT_EXTS.get(ext, Path(record_name).suffix)
        # Ensure filename has correct extension for Docling
        stem = Path(record_name).stem
        doc_name = f"{stem}{target_ext}"

        conv_res = await self._processor.parse_document(doc_name, content)
        block_containers = await self._processor.create_blocks(conv_res)
        if block_containers is None or block_containers is False:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Local Docling processor returned empty result for '{record_name}'",
            )

        return ParseResult(
            block_container=block_containers,  # type: ignore[arg-type]
            provider_used=ParserProvider.DOCLING,
            metadata={"record_name": record_name},
        )


assert isinstance(LocalDoclingParser.__new__(LocalDoclingParser), IParser)
