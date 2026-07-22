"""Parser provider that delegates PDF parsing to the external Docling HTTP service."""
from __future__ import annotations

from typing import Any

from app.services.base_client import ServiceCallError
from app.services.docling.client import DoclingClient, DoclingClientError
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

        # Two-phase: parse → create blocks
        try:
            parse_result_json = await self._client.parse_pdf(record_name_pdf, content)
            block_containers = await self._client.create_blocks(parse_result_json)
        except (DoclingClientError, ServiceCallError) as e:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Docling service failed to parse '{record_name}': {e}",
            ) from e

        return ParseResult(
            block_container=block_containers,
            provider_used=ParserProvider.DOCLING,
            metadata={"record_name": record_name},
        )


assert isinstance(DoclingServiceParser.__new__(DoclingServiceParser), IParser)
