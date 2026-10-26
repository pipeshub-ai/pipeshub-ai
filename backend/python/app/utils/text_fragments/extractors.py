"""Turn stored block text into the blocks of text a browser would render.

A text directive term must fit inside one rendered block (paragraph, list item,
table cell, heading), so selection has to start from blocks and not from the
raw string, which may carry markdown or HTML syntax the page never shows.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from markdown_it import MarkdownIt
from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.utils.text_fragments.models import SourceFormat

if TYPE_CHECKING:
    from collections.abc import Mapping


class BlockTextExtractor(Protocol):
    def extract(self, text: str) -> list[str]:
        """Return rendered-text blocks in document order (not yet normalized)."""
        ...


_LINE_BREAK = re.compile(r"[\r\n\u2028\u2029]+")
# Search-result snippets join disjoint passages with an ellipsis.
_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|\u2026)\s*")
# Inline HTML that ends a run of text: a line break or a replaced element.
_INLINE_BOUNDARY_TAG = re.compile(r"<(?:br|img|svg|video|audio|iframe|object|embed|canvas)\b", re.IGNORECASE)


class PlainTextBlockExtractor:
    def extract(self, text: str) -> list[str]:
        blocks: list[str] = []
        for line in _LINE_BREAK.split(text):
            blocks.extend(part for part in _ELLIPSIS.split(line) if part.strip())
        return blocks


class MarkdownBlockExtractor:
    def __init__(self) -> None:
        self._parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])
        self._html = HtmlBlockExtractor()

    def extract(self, text: str) -> list[str]:
        blocks: list[str] = []
        for token in self._parser.parse(text):
            if token.type == "inline":
                blocks.extend(self._inline_blocks(token.children or []))
            elif token.type in ("fence", "code_block"):
                blocks.extend(line for line in token.content.splitlines() if line.strip())
            elif token.type == "html_block":
                blocks.extend(self._html.extract(token.content))
        return [block for block in blocks if block.strip()]

    @staticmethod
    def _inline_blocks(children: list) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []

        def flush() -> None:
            blocks.append("".join(current))
            current.clear()

        for child in children:
            kind = child.type
            if kind in ("text", "code_inline"):
                current.append(child.content)
            elif kind == "softbreak":
                current.append(" ")
            elif kind in ("hardbreak", "image"):
                flush()
            elif kind == "html_inline" and _INLINE_BOUNDARY_TAG.match(child.content.strip()):
                flush()
        flush()
        return blocks


_BLOCK_TAGS = frozenset(
    """address article aside blockquote body caption dd details dialog div dl dt
    fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li
    main nav ol p pre section summary table tbody td tfoot th thead tr ul br""".split()
)
_SKIPPED_TAGS = frozenset("head script style noscript template datalist".split())
# Replaced and form-control elements contribute no text and, in Chromium's text
# search, end the run of text around them, so a term cannot span one.
_BOUNDARY_TAGS = frozenset(
    """iframe img svg video audio object embed canvas meter progress select input
    button textarea""".split()
)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


class HtmlBlockExtractor:
    def extract(self, text: str) -> list[str]:
        root = LexborHTMLParser(text).body
        if root is None:
            return []
        blocks: list[str] = []
        current: list[str] = []

        def flush() -> None:
            if current:
                blocks.append("".join(current))
                current.clear()

        def visit(node: LexborNode) -> None:
            child = node.child
            while child is not None:
                tag = child.tag
                if tag == "-text":
                    current.append(child.text(deep=False))
                elif tag in _SKIPPED_TAGS or _is_hidden(child):
                    pass
                elif tag in _BOUNDARY_TAGS:
                    flush()
                elif tag in _BLOCK_TAGS:
                    flush()
                    visit(child)
                    flush()
                elif not tag.startswith("-") and not tag.startswith("_"):
                    visit(child)
                child = child.next

        visit(root)
        flush()
        return [block for block in blocks if block.strip()]


def _is_hidden(node: LexborNode) -> bool:
    attributes = node.attributes
    if "hidden" in attributes:
        return True
    style = attributes.get("style")
    return bool(style and _HIDDEN_STYLE.search(style))


class ExtractorRegistry:
    def __init__(
        self,
        extractors: Mapping[SourceFormat, BlockTextExtractor],
        default: SourceFormat,
    ) -> None:
        if default not in extractors:
            raise ValueError(f"no extractor registered for default format {default!r}")
        self._extractors = dict(extractors)
        self._default = default

    def get(self, source_format: SourceFormat | None) -> BlockTextExtractor:
        return self._extractors.get(source_format or self._default, self._extractors[self._default])

    @classmethod
    def with_defaults(cls, default: SourceFormat = SourceFormat.MARKDOWN) -> ExtractorRegistry:
        return cls(
            {
                SourceFormat.MARKDOWN: MarkdownBlockExtractor(),
                SourceFormat.HTML: HtmlBlockExtractor(),
                SourceFormat.PLAIN: PlainTextBlockExtractor(),
            },
            default,
        )
