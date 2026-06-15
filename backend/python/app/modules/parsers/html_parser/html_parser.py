"""Environment-driven HTML parser backend.

``HTML_PARSER_BACKEND`` selects which parser implementation is exported as
``HTMLParser``:

- ``selectolax`` (default): Fast Selectolax DOM-to-blocks parser, no ML models.
- ``docling``: Docling-backed parser with richer layout analysis.
"""

from __future__ import annotations

import os
from typing import Dict, Protocol

from app.models.blocks import BlocksContainer


class HTMLParserProtocol(Protocol):
    """Unified interface for HTML parser backends."""

    def clean_html(self, html_content: str) -> str:
        """Remove non-content elements (script, style, nav, etc.) from HTML."""
        ...

    def replace_relative_image_urls(self, html_content: str) -> str:
        """Absolutize relative ``img`` ``src`` values when a base URL is inferable."""
        ...

    async def parse(
        self,
        html_content: str,
        caption_map: Dict[str, str] | None = None,
        base_url: str | None = None,
    ) -> BlocksContainer:
        """Parse HTML content into a ``BlocksContainer``.

        Automatically cleans HTML and absolutizes image URLs before parsing.
        """
        ...


_BACKEND = os.getenv("HTML_PARSER_BACKEND", "selectolax").lower()

if _BACKEND == "docling":
    from app.modules.parsers.html_parser.docling_html_parser import (
        DoclingHtmlParser as HTMLParser,
    )
else:
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser as HTMLParser,
    )

__all__ = ["HTMLParser", "HTMLParserProtocol"]
