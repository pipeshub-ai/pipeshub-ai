"""HTML to Markdown, in one place.

``html-to-markdown`` 3.x returns a ``ConversionResult`` object from
``convert()`` (the text is on ``.content``) where 2.x returned a ``str``.
Callers import ``html_to_markdown`` from here so they always get a ``str``.

Two behaviours are pinned here so a library bump cannot change them silently:

* Nothing is dropped. 3.x by default removes ``<header>``, ``<nav>``, ``<form>``
  and hidden or ``display:none`` text, which is real content in a ticket or
  article body. Page chrome is the parsers' job (``clean_html``), not this one.
* One block with a very large number of inline elements is converted in time
  quadratic in its size (about 2 s at 40k elements, minutes at a few hundred k), so
  such a document cannot be handed to the library whole.

Anything the split path cannot do faithfully (too large, too deep, a parse that
loses text) degrades to the document's plain text, never to an empty string.
"""

import logging
import re
from html import escape, unescape
from html.parser import HTMLParser

import html_to_markdown as _lib
from lxml import html as lxml_html

logger = logging.getLogger(__name__)

# Worst case measured for one block of inline links or bold text: 10k elements
# 0.17 s, 20k 0.6 s, 40k 2 s, 200k minutes. Linear shapes (tables, lists, spans)
# take 0.3 s at 80k elements, so 25k keeps ordinary large tables intact and a
# pathological block near 1 s.
MAX_ELEMENTS_PER_BLOCK = 25_000

# Split-path cost is about 1.6 s and 25x the input in memory per MB of tiny
# elements (3.2 MB of 400k paragraphs: 5 s, 210 MB peak). 8 Mi characters keeps
# the worst case near 13 s and 200 MB, and is far above any ticket, article or
# email body; larger input is reduced to text instead (about 0.5 s per MB).
MAX_INPUT_CHARS = 8 * 1024 * 1024

# Text kept by the parsed tree must be at least this share of the input's text,
# else the parse lost content (libxml2 stops silently on very deep nesting).
_MIN_KEPT_RATIO = 0.98

_OPTIONS = _lib.ConversionOptions(
    preprocessing=_lib.PreprocessingOptions(enabled=False),
    hidden_content=_lib.HiddenContent.ALL,
)

# Elements it is safe to split between children without changing the Markdown.
_CONTAINERS = frozenset({"html", "body", "div", "section", "article", "main"})

_SKIPPED_TEXT = frozenset({"script", "style", "template"})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tr", "ul",
})  # fmt: skip
_TAG = re.compile(r"<[^<>]*>")  # not `[^>]*`: a run of bare "<" would be quadratic
_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")


class _TextExtractor(HTMLParser):
    """Visible text of any HTML, without lxml and without recursion."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIPPED_TEXT:
            self._skip += 1
        self.parts.append("\n" if tag in _BLOCK_TAGS else " ")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.parts.append("\n" if tag in _BLOCK_TAGS else " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TEXT and self._skip:
            self._skip -= 1
        self.parts.append("\n" if tag in _BLOCK_TAGS else " ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _plain_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return "\n\n".join(line for line in lines if line)


def _text_chars(text: str) -> int:
    return sum(len(word) for word in text.split())


def _convert(html: str) -> str:
    return _lib.convert(html, _OPTIONS).content


def _subtree_sizes(root: lxml_html.HtmlElement) -> dict:
    """Element count under each node, in one pass (``iter()`` per level is quadratic)."""
    nodes = list(root.iter())  # keeps the proxies alive, so they are valid dict keys
    sizes = dict.fromkeys(nodes, 1)
    for node in reversed(nodes):
        parent = node.getparent()
        if parent is not None:
            sizes[parent] += sizes[node]
    return sizes


def _input_text_chars(html: str) -> int:
    """Characters of text outside tags: a cheap reference that does not use lxml."""
    return _text_chars(unescape(_TAG.sub(" ", html)))


def _tree_text_chars(root: lxml_html.HtmlElement) -> int:
    total = 0
    for node in root.iter():
        if node.text:
            total += _text_chars(node.text)
        if node.tail:
            total += _text_chars(node.tail)
    return total


def _pieces(node: lxml_html.HtmlElement, sizes: dict, plain_blocks: list[int]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buf:
            out.append(_convert("".join(buf)))
            buf.clear()
        size = 0

    def add_text(text: str | None) -> None:
        if text:
            buf.append(escape(text))

    add_text(node.text)
    for child in node:
        if not isinstance(child.tag, str):  # comment or processing instruction
            add_text(child.tail)
            continue
        count = sizes[child]
        if count > MAX_ELEMENTS_PER_BLOCK:
            flush()
            if child.tag in _CONTAINERS:
                out.extend(_pieces(child, sizes, plain_blocks))
            else:
                plain_blocks[0] += 1
                out.append(_plain_text(lxml_html.tostring(child, encoding="unicode", with_tail=False)))
            add_text(child.tail)
            continue
        # Small siblings are batched: one library call costs ~0.15 ms, which
        # dominates for a document made of hundreds of thousands of tiny blocks.
        if size + count > MAX_ELEMENTS_PER_BLOCK:
            flush()
        buf.append(lxml_html.tostring(child, encoding="unicode", with_tail=True))
        size += count
    flush()
    return out


def _split_convert(html: str) -> str | None:
    """Convert block by block; ``None`` when the parse cannot be trusted."""
    parser = lxml_html.HTMLParser(huge_tree=True)
    root = lxml_html.document_fromstring(_XML_DECLARATION.sub("", html, count=1), parser=parser)
    if _tree_text_chars(root) < _MIN_KEPT_RATIO * _input_text_chars(html):
        return None
    plain_blocks = [0]
    parts = _pieces(root, _subtree_sizes(root), plain_blocks)
    if plain_blocks[0]:
        logger.warning(
            "html_to_markdown: %d block(s) over %d elements were reduced to plain text",
            plain_blocks[0],
            MAX_ELEMENTS_PER_BLOCK,
        )
    return "\n\n".join(p.strip() for p in parts if p.strip())


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to Markdown text.

    A block with more than ``MAX_ELEMENTS_PER_BLOCK`` elements is reduced to its
    text (links and emphasis dropped); the rest of the document keeps its markup.
    A document over ``MAX_INPUT_CHARS``, or one the splitter cannot parse
    completely, is reduced to its text as a whole.
    """
    if not isinstance(html, str):
        raise TypeError(f"html must be str, got {type(html).__name__}")
    if len(html) > MAX_INPUT_CHARS:
        reason = "is over %d characters" % MAX_INPUT_CHARS
    # Each element has at least one "<", so this bounds the element count cheaply.
    elif html.count("<") <= MAX_ELEMENTS_PER_BLOCK:
        return _convert(html)
    else:
        try:
            result = _split_convert(html)
        except Exception:  # parse errors, recursion limits: all end in plain text
            result = None
            reason = "could not be parsed"
        else:
            reason = "lost text while parsing"
        if result is not None:
            return result
    logger.warning("html_to_markdown: document %s, converted as plain text", reason)
    return _plain_text(html)
