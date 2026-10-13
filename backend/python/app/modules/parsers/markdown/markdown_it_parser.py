"""markdown-it-py backed Markdown parser.

Converts Markdown directly to ``BlocksContainer`` by walking the
markdown-it-py token stream.  No ML models, no Docling pipeline, no HTML
round-trip — just a fast structural parse.

For the Docling-backed parser (richer layout analysis, bounding boxes, file
parsing), use :class:`DoclingMarkdownParser` instead.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from app.models.blocks import BlocksContainer
from app.modules.parsers.markdown.image_references import (
    extract_and_replace_images as _extract_and_replace_images,
)
from app.modules.parsers import parse_pool
from app.modules.parsers.markdown.markdown_to_blocks import (
    MarkdownToBlocksConverter,
    convert_markdown_to_blocks,
)
from app.modules.parsers.text_decoding import decode_text
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.parsing.interface import ParseResult


class MarkdownItParser:
    """Markdown parser backed by markdown-it-py.

    Responsibilities
    ----------------
    * ``parse`` – :class:`IParser` entry point: decode bytes, extract images,
      fetch base64 URIs, return :class:`ParseResult`.
    * ``parse_to_blocks`` – convert a preprocessed Markdown string directly to a
      ``BlocksContainer`` without involving Docling.  Accepts an optional
      ``caption_map`` so that image alt-text keys can be resolved to
      pre-fetched base-64 data URIs.
    * ``extract_and_replace_images`` – shared pre-processing step (same
      logic as :class:`DoclingMarkdownParser`) that normalises image
      alt-text to ``Image_N`` labels before parsing.
    """

    def __init__(self, **kwargs: object) -> None:
        self._converter = MarkdownToBlocksConverter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        """Parse file bytes into a :class:`ParseResult` (:class:`IParser` contract)."""
        md_content = decode_text(content)

        markdown = md_content.strip()

        # Regex-based image extraction is synchronous CPU work; keep it off
        # the event loop. Each regex pass over a large document is one C call
        # that holds the GIL, so those go to a parse worker process.
        if parse_pool.should_offload(len(markdown)):
            modified_markdown, images = await parse_pool.submit(
                _extract_and_replace_images, markdown, label=record_name, size=len(markdown)
            )
        else:
            modified_markdown, images = await asyncio.to_thread(
                self.extract_and_replace_images, markdown
            )
        caption_map: Dict[str, str] = {}

        urls_to_convert = [image["url"] for image in images]
        if urls_to_convert:
            base64_urls = await ImageParser.urls_to_base64(urls_to_convert)
            for i, image in enumerate(images):
                if base64_urls[i]:
                    caption_map[image["new_alt_text"]] = base64_urls[i]

        block_container = await self.parse_to_blocks(
            modified_markdown,
            caption_map=caption_map or None,
            name=record_name,
        )
        return ParseResult(
            block_container=block_container,
            metadata={"record_name": record_name},
        )

    async def parse_to_blocks(
        self,
        md_content: str,
        caption_map: Dict[str, str] | None = None,
        name: str | None = None,
        page_number: int | None = None,
    ) -> BlocksContainer:
        """Convert Markdown directly to a ``BlocksContainer``.

        Args:
            md_content: Markdown source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
                Keys must be unique per image. The intended usage is::

                    modified_md, images = parser.extract_and_replace_images(md)
                    caption_map = {img["new_alt_text"]: base64_uri for img, base64_uri in ...}
                    container = await parser.parse_to_blocks(modified_md, caption_map)

                The ``extract_and_replace_images`` step normalises alt-text to
                unique ``Image_N`` labels, ensuring no key collisions.
            name: Names the document in logs when it is parsed in a worker process.
            page_number: Optional page number stamped on emitted blocks.

        Returns:
            Populated ``BlocksContainer``.
        """
        if parse_pool.should_offload(len(md_content)):
            return await parse_pool.submit(
                convert_markdown_to_blocks,
                md_content,
                caption_map,
                page_number,
                label=name or "markdown",
                size=len(md_content),
            )
        return await asyncio.to_thread(
            self._converter.convert,
            md_content,
            caption_map=caption_map,
            page_number=page_number,
        )

    def extract_and_replace_images(
        self, md_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract images and replace alt-text with sequential ``Image_N`` labels.

        Identical behaviour to ``DoclingMarkdownParser.extract_and_replace_images``.
        Call this before ``parse_to_blocks`` when you need to map remote image
        URLs to base-64 URIs.

        Args:
            md_content: Raw Markdown source.

        Returns:
            A 2-tuple of modified Markdown and a list of image-detail dicts.
        """
        return _extract_and_replace_images(md_content)
