"""Reference implementation of the WICG text-directive matching algorithm.

Deliberately independent of `app.utils.text_fragments` (own block walker, own
folding, own word boundaries) so that a bug in the generator cannot hide behind
the same bug in the checker. It models what a browser highlights for a
directive that has no context terms, which is all the generator emits:

* matching is case- and accent-insensitive, whitespace-collapsed;
* every term must lie inside one rendered block;
* every term edge must be a word boundary (spec 3.6.2). Boundaries between two
  characters of an unspaced script (CJK, Thai) are treated as *not* boundaries,
  since a real browser needs a dictionary to place them; this is the strict
  reading, so a term cut mid-word fails here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.utils.text_fragments import (
    TextDirective,
    parse_fragment_directive,
    split_fragment_directive,
)

_BLOCK_TAGS = frozenset(
    """address article aside blockquote body caption dd details dialog div dl dt
    fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li
    main nav ol p pre section summary table tbody td tfoot th thead tr ul br
    center dir menu""".split()
)
_INVISIBLE_TAGS = frozenset("head script style noscript template".split())
# Chromium's text search cannot match across a replaced element: it ends the run of text.
_REPLACED_TAGS = frozenset("iframe img svg video audio object embed canvas meter progress".split())
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_ZERO_WIDTH = dict.fromkeys([0x00AD, 0x200B, 0x200E, 0x200F, 0x2060, 0xFEFF])

_MID_LETTER = set("'\u2019.:\u00b7")
_MID_NUMBER = set(",;.'\u2019")


def _wordish(ch: str) -> bool:
    return ch.isalnum() or ch == "_" or unicodedata.category(ch).startswith("M")


def rendered_blocks(html: str) -> list[str]:
    """Visible text of `html`, one string per block, whitespace-collapsed."""
    root = LexborHTMLParser(html).body
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = " ".join("".join(current).translate(_ZERO_WIDTH).split())
        current.clear()
        if text:
            blocks.append(text)

    def hidden(node: LexborNode) -> bool:
        attrs = node.attributes
        return "hidden" in attrs or bool(attrs.get("style") and _HIDDEN_STYLE.search(attrs["style"]))

    def walk(node: LexborNode) -> None:
        child = node.child
        while child is not None:
            tag = child.tag
            if tag == "-text":
                current.append(child.text(deep=False))
            elif tag in _INVISIBLE_TAGS or tag.startswith("_") or hidden(child):
                pass
            elif tag in _REPLACED_TAGS:
                flush()
            elif tag in _BLOCK_TAGS:
                flush()
                walk(child)
                flush()
            else:
                walk(child)
            child = child.next

    if root is not None:
        walk(root)
    flush()
    return blocks


@dataclass(frozen=True)
class _Folded:
    text: str
    origin: list[int]  # folded index -> original index


def _fold(text: str) -> _Folded:
    chars: list[str] = []
    origin: list[int] = []
    for index, ch in enumerate(text):
        for piece in unicodedata.normalize("NFD", ch.casefold()):
            if unicodedata.category(piece) == "Mn":
                continue
            for out in unicodedata.normalize("NFC", piece).casefold():
                chars.append(out)
                origin.append(index)
    return _Folded("".join(chars), origin)


def _is_boundary(text: str, position: int) -> bool:
    if position <= 0 or position >= len(text):
        return True
    before, after = text[position - 1], text[position]
    if not (_wordish(before) and _wordish(after)):
        if _wordish(before) and after in (_MID_LETTER | _MID_NUMBER) and position + 1 < len(text):
            return not _joins(before, text[position + 1], after)
        if before in (_MID_LETTER | _MID_NUMBER) and _wordish(after) and position >= 2:
            return not _joins(text[position - 2], after, before)
        return True
    # Two word characters in a row never split, including unspaced scripts,
    # where we cannot tell where a dictionary would.
    return False


def _joins(left: str, right: str, mid: str) -> bool:
    if left.isalpha() and right.isalpha():
        return mid in _MID_LETTER
    if left.isdigit() and right.isdigit():
        return mid in _MID_NUMBER
    return False


def _find(
    folded_block: _Folded,
    original: str,
    term: str,
    start_at: int,
    start_bounded: bool,
    end_bounded: bool,
) -> tuple[int, int] | None:
    """First `(orig_start, orig_end)` of `term` in the block at/after original index `start_at`."""
    needle = _fold(term).text
    if not needle:
        return None
    haystack = folded_block.text
    search = 0
    while True:
        found = haystack.find(needle, search)
        if found < 0:
            return None
        search = found + 1
        orig_start = folded_block.origin[found]
        if orig_start < start_at:
            continue
        end_index = found + len(needle) - 1
        orig_end = folded_block.origin[end_index] + 1
        while orig_end < len(original) and unicodedata.category(original[orig_end]) == "Mn":
            orig_end += 1
        if start_bounded and not _is_boundary(original, orig_start):
            continue
        if end_bounded and not _is_boundary(original, orig_end):
            continue
        return orig_start, orig_end


def match_directive(blocks: list[str], directive: TextDirective) -> str | None:
    """The text a browser would highlight, or None when the directive does not match."""
    if directive.prefix or directive.suffix:
        raise NotImplementedError("context terms are not emitted by the generator")

    folded = [_fold(block) for block in blocks]
    for i, block in enumerate(blocks):
        start = _find(folded[i], block, directive.start, 0, True, True)
        if start is None:
            continue
        if directive.end is None:
            return block[start[0]:start[1]]
        # The spec stops at the first start match: a later start cannot
        # find an end that an earlier one could not.
        end_hit = _find_end(blocks, folded, i, start[1], directive.end)
        if end_hit is None:
            return None
        end_block, end_pos = end_hit
        if end_block == i:
            return block[start[0]:end_pos]
        parts = [block[start[0]:], *blocks[i + 1:end_block], blocks[end_block][:end_pos]]
        return " ".join(parts)
    return None


def _find_end(
    blocks: list[str], folded: list[_Folded], block_index: int, after: int, term: str
) -> tuple[int, int] | None:
    hit = _find(folded[block_index], blocks[block_index], term, after, True, True)
    if hit is not None:
        return block_index, hit[1]
    for j in range(block_index + 1, len(blocks)):
        hit = _find(folded[j], blocks[j], term, 0, True, True)
        if hit is not None:
            return j, hit[1]
    return None


def highlight_for_url(html: str, url: str) -> str | None:
    """Highlight produced by the first text directive of `url` on page `html`."""
    _, directive = split_fragment_directive(url)
    if not directive:
        return None
    parsed = parse_fragment_directive(directive)
    if not parsed:
        return None
    return match_directive(rendered_blocks(html), parsed[0])
