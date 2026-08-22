"""Parser provider that uses OCR (VLM) for scanned PDFs."""
from __future__ import annotations

import logging
from typing import Any

from app.models.blocks import (
    Block,
    BlocksContainer,
)
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParseResult,
)

logger = logging.getLogger(__name__)


class OCRParser:
    """Delegates to :class:`OCRHandler`, then converts output to BlocksContainer."""

    def __init__(
        self,
        ocr_handler: OCRHandler,
        md_parser: Any,
    ) -> None:
        self._handler = ocr_handler
        self._md_parser = md_parser

    def supported_formats(self) -> list[str]:
        return ["pdf"]

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        from app.config.constants.ai_models import OCRProvider

        ocr_result = await self._handler.process_document(content)

        provider_name = getattr(self._handler, "provider", "")

        if provider_name == OCRProvider.VLM_OCR.value:
            block_containers = await self._process_vlm_ocr_result(ocr_result, record_name)
        else:
            raise ParseError(
                ParseErrorCode.PROVIDER_UNAVAILABLE,
                f"Unsupported OCR provider: '{provider_name}'",
            )

        return ParseResult(
            block_container=block_containers,
            metadata={"ocr_provider": provider_name, "record_name": record_name},
        )

    async def _process_vlm_ocr_result(
        self, ocr_result: dict, record_name: str
    ) -> BlocksContainer:
        """VLM OCR → markdown per page → markdown parser → BlocksContainer."""
        pages = ocr_result.get("pages", [])
        all_blocks: list[Block] = []
        all_block_groups: list = []
        block_index_offset = 0
        block_group_index_offset = 0

        for page in pages:
            page_number = page.get("page_number")
            page_markdown = page.get("markdown", "")
            if not page_markdown.strip():
                continue

            page_bc = await self._md_parser.parse_to_blocks(page_markdown, page_number=page_number)
            if not page_bc:
                continue

            for block in page_bc.blocks:
                block.index = block.index + block_index_offset
                if block.parent_index is not None:
                    block.parent_index = block.parent_index + block_group_index_offset
                all_blocks.append(block)

            for bg in page_bc.block_groups:
                bg.index = bg.index + block_group_index_offset
                if bg.parent_index is not None:
                    bg.parent_index = bg.parent_index + block_group_index_offset
                if bg.children:
                    for r in bg.children.block_ranges:
                        r.start += block_index_offset
                        r.end += block_index_offset
                    for r in bg.children.block_group_ranges:
                        r.start += block_group_index_offset
                        r.end += block_group_index_offset
                all_block_groups.append(bg)

            block_index_offset = len(all_blocks)
            block_group_index_offset = len(all_block_groups)

        return BlocksContainer(blocks=all_blocks, block_groups=all_block_groups)

assert isinstance(OCRParser.__new__(OCRParser), IParser)
