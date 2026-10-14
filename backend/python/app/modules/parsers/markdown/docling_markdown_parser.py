"""Docling-backed Markdown parser.

Converts Markdown → HTML (via python-markdown), then feeds the HTML bytes into
Docling's ``DocumentConverter``.  Use this parser when you need Docling's
layout-analysis output (bounding boxes, page numbers, richer table detection)
for Markdown content that has been stored as a file or must round-trip through
the Docling pipeline.

For a faster, purely structural parse that produces ``BlocksContainer`` directly
without ML overhead, use :class:`MarkdownItParser` instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import markdown as markdown_lib
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.parsing.interface import ParseResult
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer
from app.modules.parsers.markdown.image_references import (
    extract_and_replace_images as _extract_and_replace_images,
)
from app.modules.parsers.text_decoding import decode_text
from app.utils.converters.caption_map import apply_caption_map

class DoclingMarkdownParser:
    """Markdown parser backed by Docling.

    Responsibilities
    ----------------
    * ``parse_string`` – convert a Markdown string to HTML bytes ready for
      Docling ingestion.
    * ``parse_file`` – parse a ``.md`` file on disk via Docling's
      ``DocumentConverter`` and return a ``DoclingDocument``.
    * ``extract_and_replace_images`` – pre-process image references so that
      alt-text labels are normalised to ``Image_N`` before parsing.  This is
      needed both by this parser and the markdownit parser, so callers can use
      whichever parser they have in hand.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        config_service: object | None = None,
    ) -> None:
        self.converter = DocumentConverter()
        self._logger = logger or logging.getLogger(__name__)
        self._config_service = config_service

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        md_content = decode_text(content)

        markdown = md_content.strip()

        modified_markdown, images = self.extract_and_replace_images(markdown)
        caption_map = {}

        # Collect all image URLs
        urls_to_convert = [image["url"] for image in images]

        # Convert URLs to base64 if there are any images
        if urls_to_convert:
            base64_urls = await ImageParser.urls_to_base64(urls_to_convert)

            # Create caption map with base64 URLs
            for i, image in enumerate(images):
                if base64_urls[i]:
                    caption_map[image["new_alt_text"]] = base64_urls[i]

        from app.modules.parsers.pdf.docling_processor import DoclingProcessor  # noqa: PLC0415

        html_bytes = self.parse_string(modified_markdown)
        processor = DoclingProcessor(logger=self._logger, config=self._config_service)
        filename = f"{Path(record_name).stem}.md" if record_name else "document.md"
        doc = await processor.parse_document(filename, html_bytes)

        return ParseResult(
            raw_document=doc.model_dump_json(),
            metadata={"record_name": record_name, "caption_map": caption_map or None},
        )
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_string(self, md_content: str) -> bytes:
        """Convert Markdown to HTML bytes for Docling ingestion.

        The returned bytes should be passed to
        ``DoclingProcessor.parse_document`` as the file content.

        Args:
            md_content: Markdown source string.

        Returns:
            UTF-8 encoded HTML.
        """
        html = markdown_lib.markdown(md_content, extensions=["md_in_html"])
        return html.encode("utf-8")

    def parse_file(self, file_path: str) -> DoclingDocument:
        """Parse a Markdown file via Docling.

        Args:
            file_path: Absolute or relative path to the ``.md`` file.

        Returns:
            Parsed ``DoclingDocument``.

        Raises:
            ValueError: If Docling reports a non-success status.
        """
        result = self.converter.convert(file_path)
        if result.status.value != "success":
            raise DocumentProcessingError(
                f"Failed to parse Markdown: {result.status}",
                details={"status": str(result.status)},
            )
        return result.document

    def extract_and_replace_images(
        self, md_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract images and replace alt-text with sequential ``Image_N`` labels.

        Handles inline Markdown images, reference-style images, and ``<img>``
        HTML tags embedded in the Markdown.

        Args:
            md_content: Raw Markdown source.

        Returns:
            A 2-tuple of:
            - Modified Markdown with normalised alt-text.
            - List of dicts describing each image::

                {
                    'original_text': str,
                    'url': str,
                    'alt_text': str,      # original alt text
                    'new_alt_text': str,  # Image_N
                    'image_type': str,    # 'markdown' | 'reference' | 'html'
                }
        """
        return _extract_and_replace_images(md_content)

    async def parse_to_blocks(
        self,
        md_content: str,
        caption_map: Dict[str, str] | None = None,
        name: str | None = None,
        page_number: int | None = None,
    ) -> BlocksContainer:
        """Parse Markdown to ``BlocksContainer`` via the Docling pipeline.

        Args:
            md_content: Markdown source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
            name: Optional source filename or record name used for Docling ingestion.

        Returns:
            Populated ``BlocksContainer``.
        """
        from app.modules.parsers.pdf.docling_processor import DoclingProcessor

        html_bytes = self.parse_string(md_content)
        processor = DoclingProcessor(logger=self._logger, config=self._config_service)
        filename = f"{Path(name).stem}.md" if name else "document.md"
        doc = await processor.parse_document(filename, html_bytes)
        container = await processor.create_blocks(doc, page_number=page_number)

        if caption_map:
            apply_caption_map(container, caption_map, self._logger)
        return container
