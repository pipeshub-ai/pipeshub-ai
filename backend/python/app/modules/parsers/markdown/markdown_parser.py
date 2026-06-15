"""Environment-driven Markdown parser backend.

``MARKDOWN_PARSER_BACKEND`` selects which parser implementation is exported as
``MarkdownParser``:

- ``markdownit`` (default): Fast markdown-it-py parser, no ML models.
- ``docling``: Docling-backed parser with richer layout analysis.
"""

from __future__ import annotations

import os
from typing import Dict, List, Protocol, Tuple

from app.models.blocks import BlocksContainer


class MarkdownParserProtocol(Protocol):
    """Unified interface for Markdown parser backends."""

    def extract_and_replace_images(
        self, md_content: str
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Extract images and normalise alt-text to sequential ``Image_N`` labels."""
        ...

    async def parse(
        self,
        md_content: str,
        caption_map: Dict[str, str] | None = None,
        name: str | None = None,
    ) -> BlocksContainer:
        """Parse Markdown content into a ``BlocksContainer``."""
        ...


_BACKEND = os.getenv("MARKDOWN_PARSER_BACKEND", "markdownit").lower()

if _BACKEND == "docling":
    from app.modules.parsers.markdown.docling_markdown_parser import (
        DoclingMarkdownParser as MarkdownParser,
    )
else:
    from app.modules.parsers.markdown.markdown_it_parser import (
        MarkdownItParser as MarkdownParser,
    )

__all__ = ["MarkdownParser", "MarkdownParserProtocol"]
