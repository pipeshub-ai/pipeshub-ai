"""Selectolax-backed HTML parser.

Converts HTML directly to ``BlocksContainer`` by walking the DOM with
Selectolax (Lexbor).  No ML models, no Docling pipeline — just a fast
structural parse.

For the Docling-backed parser (richer layout analysis, bounding boxes, file
parsing), use :class:`DoclingHtmlParser` (or the ``HTMLParser`` alias in
``html_parser.py``) instead.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.parsing.interface import ParseResult
from bs4 import BeautifulSoup

from app.models.blocks import BlocksContainer
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.modules.parsers.html_parser.url_utils import replace_relative_image_urls


class SelectolaxHtmlParser:
    """HTML parser backed by Selectolax (Lexbor).

    Responsibilities
    ----------------
    * ``parse_to_blocks`` – convert an HTML string directly to a
      ``BlocksContainer`` without involving Docling.  Accepts optional
      ``base_url`` and ``caption_map`` so relative image ``src`` values and
      alt-text keys can be resolved before block emission.
    * ``replace_relative_image_urls`` – shared pre-processing step (same
      logic as :class:`DoclingHtmlParser`) that absolutizes relative image URLs
      using ``<base>``, canonical link, or other heuristics in the document.
    """

    def __init__(self, **kwargs: object) -> None:
        self._converter = HtmlToBlocksConverter()
        self._logger = kwargs.get("logger") or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        if isinstance(content, bytes):
            html_content = content.decode("utf-8")
        else:
            html_content = content

        html_content = html_content.strip()

        html_content = self.clean_html(html_content)
        html_content = self.replace_relative_image_urls(html_content)

        # Extract image URLs and convert to base64 (mirrors the Markdown flow)
        caption_map: Dict[str, str] = {}
        modified_html, images = self.extract_and_replace_images(html_content)

        if images:
            
            urls_to_convert = [image["url"] for image in images]
            base64_urls = await ImageParser.urls_to_base64(urls_to_convert)

            for i, image in enumerate(images):
                if base64_urls[i]:
                    caption_map[image["new_alt_text"]] = base64_urls[i]

        block_containers = await self.parse_to_blocks(
            modified_html,
            caption_map=caption_map if caption_map else None,
        )
        return ParseResult(
            block_container=block_containers,
            metadata={"record_name": record_name},
        )
        
    async def parse_to_blocks(
        self,
        html_content: str,
        caption_map: Dict[str, str] | None = None,
        base_url: str | None = None,
        name: str | None = None,
    ) -> BlocksContainer:
        """Convert HTML directly to a ``BlocksContainer``.

        Args:
            html_content: HTML source string.
            caption_map: Optional mapping of image alt-text labels to
                base-64 data URIs (or any string value).  When provided,
                matching image blocks will have their ``data["uri"]`` set.
            base_url: Optional base URL for resolving relative image ``src``
                attributes when no ``<base>`` tag is present.
            name: Unused; kept for signature compatibility with other HTML backends.

        Returns:
            Populated ``BlocksContainer``.
        """
        return self._converter.convert(
            html_content,
            base_url=base_url,
            caption_map=caption_map,
        )

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

    def extract_and_replace_images(
        self, html_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract image URLs and replace alt-text with sequential ``Image_N`` labels.

        Analogous to the Markdown parser's ``extract_and_replace_images``.
        Call before ``parse`` / ``parse_to_blocks`` to collect image URLs for
        base-64 conversion; the returned label list lets you build a
        ``caption_map`` keyed by ``new_alt_text``.

        Args:
            html_content: HTML source string (should already have relative URLs
                absolutized via ``replace_relative_image_urls``).

        Returns:
            A 2-tuple of:
            - Modified HTML with ``alt`` attributes rewritten to ``Image_N``.
            - List of dicts describing each image::

                {
                    "url": "<original src>",
                    "alt_text": "<original alt>",
                    "new_alt_text": "Image_N",
                }
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

    def replace_relative_image_urls(self, html_content: str) -> str:
        """Replace relative image URLs with absolute URLs in the HTML string.

        Identical behaviour to :meth:`DoclingHtmlParser.replace_relative_image_urls`.
        Call this before ``parse_to_blocks`` when image blocks should carry
        fully qualified URLs.

        Args:
            html_content: Raw HTML source.

        Returns:
            HTML with relative ``img`` ``src`` values rewritten when a base
            URL can be inferred from the document.
        """
        return replace_relative_image_urls(html_content)
