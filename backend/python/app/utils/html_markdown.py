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
"""

import logging
from html import escape

import html_to_markdown as _lib
from lxml import html as lxml_html

logger = logging.getLogger(__name__)

# Worst case measured for one block of inline links or bold text: 10k elements
# 0.17 s, 20k 0.6 s, 40k 2 s, 200k minutes. Linear shapes (tables, lists, spans)
# take 0.3 s at 80k elements, so 25k keeps ordinary large tables intact and a
# pathological block near 1 s.
MAX_ELEMENTS_PER_BLOCK = 25_000

_OPTIONS = _lib.ConversionOptions(
    preprocessing=_lib.PreprocessingOptions(enabled=False),
    hidden_content=_lib.HiddenContent.ALL,
)

# Elements it is safe to split between children without changing the Markdown.
_CONTAINERS = frozenset({"html", "body", "div", "section", "article", "main"})


def _convert(html: str) -> str:
    return _lib.convert(html, _OPTIONS).content


def _pieces(node, plain_blocks: list[int]) -> list[str]:
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
        count = sum(1 for _ in child.iter())
        if count > MAX_ELEMENTS_PER_BLOCK:
            flush()
            if child.tag in _CONTAINERS:
                out.extend(_pieces(child, plain_blocks))
            else:
                plain_blocks[0] += 1
                out.append(" ".join(" ".join(child.itertext()).split()))
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


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to Markdown text.

    A block with more than ``MAX_ELEMENTS_PER_BLOCK`` elements is reduced to its
    text (links and emphasis dropped); the rest of the document keeps its markup.
    """
    if not isinstance(html, str):
        raise TypeError(f"html must be str, got {type(html).__name__}")
    # Each element has at least one "<", so this bounds the element count cheaply.
    if html.count("<") <= MAX_ELEMENTS_PER_BLOCK:
        return _convert(html)

    plain_blocks = [0]
    parts = _pieces(lxml_html.document_fromstring(html), plain_blocks)
    if plain_blocks[0]:
        logger.warning(
            "html_to_markdown: %d block(s) over %d elements were reduced to plain text",
            plain_blocks[0],
            MAX_ELEMENTS_PER_BLOCK,
        )
    return "\n\n".join(p.strip() for p in parts if p.strip())
