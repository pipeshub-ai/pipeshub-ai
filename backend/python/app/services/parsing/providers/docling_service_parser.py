"""Parser provider that delegates PDF parsing to the external Docling HTTP service."""
from __future__ import annotations

import asyncio
from typing import Any

from app.services.docling.client import DoclingClient, DoclingParseError
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)


class DoclingServiceParser:
    """Delegates to the existing external Docling service (port 8081)."""

    def __init__(self, docling_client: DoclingClient) -> None:
        self._client = docling_client

    def supported_formats(self) -> list[str]:
        return ["pdf"]

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        record_name_pdf = record_name if record_name.lower().endswith(".pdf") else f"{record_name}.pdf"

        try:
            doc = await self._client.parse_pdf_batched(record_name_pdf, content)
        except DoclingParseError as exc:
            raise ParseError(ParseErrorCode.PARSE_FAILED, exc.message) from exc

        raw_document = await asyncio.to_thread(doc.model_dump_json)
        return ParseResult(
            raw_document=raw_document,
            provider_used=ParserProvider.DOCLING,
            metadata={"record_name": record_name},
        )


assert isinstance(DoclingServiceParser.__new__(DoclingServiceParser), IParser)
