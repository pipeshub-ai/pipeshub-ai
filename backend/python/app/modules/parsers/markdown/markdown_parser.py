"""Backward-compatible re-export.

``MarkdownParser`` is an alias for :class:`DoclingMarkdownParser` — the
default parser backed by Docling.

To use the faster markdown-it-py parser instead (no ML models, direct
AST-to-blocks, no Docling pipeline), import :class:`MarkdownItParser`
directly::

    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

Switching the default
---------------------
Change the import below to point at ``MarkdownItParser`` and everything that
imports ``MarkdownParser`` will transparently switch to the markdown-it-py
backend:

    from app.modules.parsers.markdown.markdown_it_parser import (
        MarkdownItParser as MarkdownParser,
    )
"""

from app.modules.parsers.markdown.docling_markdown_parser import (
    DoclingMarkdownParser as MarkdownParser,
)

__all__ = ["MarkdownParser"]
