"""markdown-it-py backed Markdown parser.

Converts Markdown directly to ``BlocksContainer`` by walking the
markdown-it-py token stream.  No ML models, no Docling pipeline, no HTML
round-trip — just a fast structural parse.

For the Docling-backed parser (richer layout analysis, bounding boxes, file
parsing), use :class:`DoclingMarkdownParser` instead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.models.blocks import BlocksContainer
from app.modules.parsers.markdown.docling_markdown_parser import (
    _extract_and_replace_images,
)
from app.modules.parsers.markdown.markdown_to_blocks import MarkdownToBlocksConverter
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.parsing.interface import ParseResult


class MarkdownItParser:
    """Markdown parser backed by markdown-it-py.

    Responsibilities
    ----------------
    * ``parse_to_blocks`` – convert a Markdown string directly to a
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

    async def parse_to_blocks(
        self,
        md_content: str,
        caption_map: Dict[str, str] | None = None,
        page_number: int | None = None,
    ) -> BlocksContainer:
        """Convert Markdown directly to a ``BlocksContainer``.

        Args:
            md_content: Markdown source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
                Keys must be unique per image. The intended usage is::

                    modified_md, images = parser.extract_and_replace_images(md)
                    caption_map = {img["new_alt_text"]: base64_uri for img, base64_uri in ...}
                    container = parser.parse_to_blocks(modified_md, caption_map)

                The ``extract_and_replace_images`` step normalises alt-text to
                unique ``Image_N`` labels, ensuring no key collisions.

        Returns:
            Populated ``BlocksContainer``.
        """
        return self._converter.convert(
            md_content,
            caption_map=caption_map,
            page_number=page_number,
        )

    async def parse(
        self,
        md_content: str,
        caption_map: Dict[str, str] | None = None,
        name: str | None = None,
        page_number: int | None = None,
    ) -> BlocksContainer:
        """Async wrapper around ``parse_to_blocks`` for protocol conformance."""
        return self.parse_to_blocks(
            md_content,
            caption_map,
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
