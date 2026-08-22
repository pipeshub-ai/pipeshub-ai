"""Environment-driven HTML parser backend.

``HTML_PARSER_BACKEND`` selects which parser implementation is exported as
``HTMLParser``:

- ``selectolax`` (default): Fast Selectolax DOM-to-blocks parser, no ML models.
- ``docling``: Docling-backed parser with richer layout analysis.
"""

from __future__ import annotations

import os
from typing import Dict, List, Protocol, Tuple

from app.models.blocks import BlocksContainer


class HTMLParserProtocol(Protocol):
    """Unified interface for HTML parser backends."""

    def clean_html(self, html_content: str) -> str:
        """Remove non-content elements (script, style, nav, etc.) from HTML."""
        ...

    def replace_relative_image_urls(self, html_content: str) -> str:
        """Absolutize relative ``img`` ``src`` values when a base URL is inferable."""
        ...

    def extract_and_replace_images(
        self, html_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract image URLs and replace alt-text with sequential ``Image_N`` labels."""
        ...

    async def parse_to_blocks(
        self,
        html_content: str,
        caption_map: Dict[str, str] | None = None,
        base_url: str | None = None,
        name: str | None = None,
    ) -> BlocksContainer:
        """Parse preprocessed HTML into a ``BlocksContainer``.

        Caller must run ``clean_html`` and ``replace_relative_image_urls`` first.
        """
        ...


_BACKEND = os.getenv("PARSER_BACKEND", "selectolax").lower()

if _BACKEND == "docling":
    from app.modules.parsers.html_parser.docling_html_parser import (
        DoclingHtmlParser as HTMLParser,
    )
else:
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser as HTMLParser,
    )

__all__ = ["HTMLParser", "HTMLParserProtocol"]
