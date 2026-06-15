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
from typing import Dict

from bs4 import BeautifulSoup

from app.models.blocks import BlocksContainer
from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter


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
        self._docling_html_parser = DoclingHtmlParser()
        self._logger = kwargs.get("logger") or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_to_blocks(
        self,
        html_content: str,
        *,
        base_url: str | None = None,
        caption_map: Dict[str, str] | None = None,
    ) -> BlocksContainer:
        """Convert HTML directly to a ``BlocksContainer``.

        Args:
            html_content: HTML source string.
            base_url: Optional base URL for resolving relative image ``src``
                attributes when no ``<base>`` tag is present.
            caption_map: Optional mapping of image alt-text labels to
                base-64 data URIs (or any string value).  When provided,
                matching image blocks will have their ``data["uri"]`` set.

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

    async def parse(
        self,
        html_content: str,
        caption_map: Dict[str, str] | None = None,
        base_url: str | None = None,
    ) -> BlocksContainer:
        """Parse HTML to ``BlocksContainer``.

        Cleans HTML (removes script/style/nav/etc.), absolutizes image URLs,
        then converts to blocks via Selectolax.

        Args:
            html_content: HTML source string.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
            base_url: Optional base URL for resolving relative image URLs.

        Returns:
            Populated ``BlocksContainer``.
        """
        # self._logger.info(f"selectolax_html_parser.parse starting")
        cleaned = self.clean_html(html_content)
        cleaned = self.replace_relative_image_urls(cleaned)
        return self.parse_to_blocks(
            cleaned,
            base_url=base_url,
            caption_map=caption_map,
        )

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
        return self._docling_html_parser.replace_relative_image_urls(html_content)
