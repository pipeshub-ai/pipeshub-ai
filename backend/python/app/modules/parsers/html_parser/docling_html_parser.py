"""Docling-backed HTML parser.

Feeds HTML into Docling's ``DocumentConverter``.  Use this parser when you need
Docling's layout-analysis output (bounding boxes, page numbers, richer table
detection) for HTML content stored as a file or routed through the Docling
pipeline.

For a faster, purely structural parse that produces ``BlocksContainer`` directly
without ML overhead, use :class:`SelectolaxHtmlParser` instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

from bs4 import BeautifulSoup
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlockType, BlocksContainer
from app.modules.parsers.html_parser import url_utils


class DoclingHtmlParser:
    """HTML parser backed by Docling.

    Responsibilities
    ----------------
    * ``parse_string`` – encode HTML content as UTF-8 bytes for Docling ingestion.
    * ``parse_file`` – parse an HTML file on disk via Docling's
      ``DocumentConverter`` and return a ``DoclingDocument``.
    * ``replace_relative_image_urls`` – pre-process image references so relative
      ``src`` values are absolutized using ``<base>``, canonical link, or other
      heuristics.  Shared by this parser and :class:`SelectolaxHtmlParser`.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        config_service: object | None = None,
    ) -> None:
        self.converter = DocumentConverter()
        self._logger = logger or logging.getLogger(__name__)
        self._config_service = config_service

    def parse_string(self, html_content: str) -> bytes:
        """
        Parse HTML content from a string.

        Args:
            html_content (str): HTML content as a string

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Convert string to bytes
        html_bytes = html_content.encode("utf-8")
        return html_bytes


    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse HTML content from a file.

        Args:
            file_path (str): Path to the HTML file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise DocumentProcessingError(
                f"Failed to parse HTML: {result.status}",
                details={"status": str(result.status)},
            )

        return result.document

    def get_base_url_from_html(self, soup: BeautifulSoup) -> str | None:
        """
        Extract base URL from HTML document using multiple fallback strategies.

        Args:
            soup: BeautifulSoup object

        Returns:
            Base URL as string, or None if not found
        """
        return url_utils.get_base_url_from_html(soup)

    def extract_and_replace_images(
        self, html_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract image URLs and replace alt-text with sequential ``Image_N`` labels.

        Analogous to the Markdown parser's ``extract_and_replace_images``.

        Args:
            html_content: HTML source string (should already have relative URLs
                absolutized via ``replace_relative_image_urls``).

        Returns:
            A 2-tuple of:
            - Modified HTML with ``alt`` attributes rewritten to ``Image_N``.
            - List of dicts describing each image.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        images: List[Dict[str, str]] = []
        image_counter = 1

        for img_tag in soup.find_all("img"):
            src = img_tag.get("src", "").strip()
            if not src:
                srcset = img_tag.get("srcset", "")
                if srcset:
                    first_part = srcset.split(",")[0].split()
                    if first_part:
                        src = first_part[0].strip()
            if not src:
                continue
            if src.startswith("data:"):
                continue

            original_alt = img_tag.get("alt", "").strip()
            new_alt = f"Image_{image_counter}"
            img_tag["alt"] = new_alt
            images.append({
                "url": src,
                "alt_text": original_alt,
                "new_alt_text": new_alt,
            })
            image_counter += 1

        return str(soup), images

    def replace_relative_image_urls(self, html_string: str) -> str:
        """
        Replace all relative image URLs with absolute URLs.
        Absolute URLs are left unchanged.
        Base URL is automatically extracted from the HTML document.

        Args:
            html_string: HTML content as string

        Returns:
            Modified HTML string with absolute image URLs
        """
        return url_utils.replace_relative_image_urls(html_string)

    def clean_html(self, html_content: str) -> str:
        """Remove non-content elements from HTML.

        Strips script, style, noscript, iframe, nav, footer, and header elements.

        Args:
            html_content: Raw HTML source.

        Returns:
            Cleaned HTML string.
        """
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for element in soup(
                ["script", "style", "noscript", "iframe", "nav", "footer", "header"]
            ):
                element.decompose()
            return str(soup)
        except Exception as e:
            self._logger.warning("Failed to clean HTML: %s", e)
            return html_content

    async def parse(
        self,
        html_content: str,
        caption_map: Dict[str, str] | None = None,
        base_url: str | None = None,
        name: str | None = None,
    ) -> BlocksContainer:
        """Parse preprocessed HTML to ``BlocksContainer`` via the Docling pipeline.

        Caller must run ``clean_html`` and ``replace_relative_image_urls`` first.

        Args:
            html_content: Preprocessed HTML source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
            base_url: Unused; kept for protocol signature compatibility.
            name: Optional source filename or record name used for Docling ingestion.

        Returns:
            Populated ``BlocksContainer``.
        """
        from html_to_markdown import convert

        from app.modules.parsers.pdf.docling_processor import DoclingProcessor

        markdown = convert(html_content)
        md_bytes = markdown.encode("utf-8")

        processor = DoclingProcessor(logger=self._logger, config=self._config_service)
        filename = f"{Path(name).stem}.md" if name else "document.md"
        doc = await processor.parse_document(filename, md_bytes)
        container = await processor.create_blocks(doc)

        if caption_map:
            _apply_caption_map(container, caption_map, self._logger)

        return container


def _apply_caption_map(
    container: BlocksContainer,
    caption_map: Dict[str, str],
    logger: logging.Logger,
) -> None:
    """Attach base-64 URIs to image blocks using caption keys."""
    for block in container.blocks:
        if block.type == BlockType.IMAGE.value and block.image_metadata:
            captions = block.image_metadata.captions
            if captions:
                caption = captions[0]
                uri = caption_map.get(caption)
                if uri:
                    if block.data is None:
                        block.data = {}
                    if isinstance(block.data, dict):
                        block.data["uri"] = uri
                    else:
                        block.data = {"uri": uri}
                else:
                    logger.warning(
                        "Skipping image with caption '%s' - no valid base64 data available",
                        caption,
                    )

