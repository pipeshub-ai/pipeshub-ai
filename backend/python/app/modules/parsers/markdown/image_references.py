"""Finding the images a Markdown document refers to.

Shared by both Markdown parser backends. In a module of its own, without the
Docling imports of ``docling_markdown_parser``, so a parse worker process can
run it on a large document without loading Docling.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from collections.abc import Iterator

# Every scan below is a single left-to-right pass. A regular expression with an
# unbounded run (``[^\]]*``) rescans the rest of the document from each opener, so
# a long input made of repeated openers ("![![![...") took time quadratic in its
# size. Here each "next closing character" lookup is remembered (``_Next``), so
# any number of openers share one search, and the tag scan is capped (see
# ``_iter_html_img_tags``).


class _Next:
    """``pattern.search`` from a position, remembering the last answer.

    Queries from a start inside the span the last search already covered return
    the same answer without scanning again, which keeps a scan that asks the
    same question from many openers linear.
    """

    __slots__ = ("_from", "_hit", "_pat", "_text")

    def __init__(self, text: str, pattern: str) -> None:
        self._text = text
        self._pat = re.compile(pattern)
        self._from = len(text) + 1
        self._hit = -2

    def __call__(self, pos: int) -> int:
        if self._from <= pos and (self._hit == -1 or pos <= self._hit):
            return self._hit
        m = self._pat.search(self._text, pos)
        self._from = pos
        self._hit = m.start() if m else -1
        return self._hit


_WS = re.compile(r"\s*")
_NON_WS = re.compile(r"\S+")
_IMG_OPEN = re.compile(r"<img(?=[\s/>])", re.IGNORECASE)
# Right after the tag name a "/" separates too: <img/src="...">.
_TAG_FIRST_SEPARATOR = re.compile(r"\s+|/|")
# CommonMark's grammar for an attribute name and an unquoted value, so an <img>
# is rewritten exactly when markdown-it would treat it as a tag.
_TAG_ATTR_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:-]*")
_TAG_UNQUOTED_VALUE = re.compile(r"[^\s\"'=<>`]+")
# A scan that fails (a tag never closed) gives up on the text it covered; the
# budget bounds the total of those, so crafted input cannot make it quadratic.
_TAG_SCAN_BUDGET_FACTOR = 8
_TAG_SCAN_BUDGET_FLOOR = 4096


def is_inline_raster_image(src: str) -> bool:
    """Whether ``src`` is a ``data:`` URI holding a raster image (not SVG)."""
    head = src[:16].lower()
    return head.startswith("data:image/") and not head.startswith("data:image/svg")


# Inside the parentheses of an inline image, whitespace may include one line
# ending but not a blank line, which ends the paragraph.
_PAD = re.compile(r"[^\S\n]*(?:\n[^\S\n]*)?")


def _skip_pad(text: str, pos: int) -> int:
    return _PAD.match(text, pos).end()  # type: ignore[union-attr]


def _skip_ws(text: str, pos: int) -> int:
    return _WS.match(text, pos).end()  # type: ignore[union-attr]


def _iter_reference_usages(text: str) -> Iterator[tuple[int, int, str, str]]:
    """``![alt][ref]`` as ``(start, end, alt, ref)``."""
    # One cache per lookup: each sees only rising positions, which is what keeps
    # _Next linear. A shared one was sent back by every label lookup, so each
    # later opener rescanned to the same bracket ("![" * n + "][]").
    alt_close = _Next(text, r"\]")
    label_close = _Next(text, r"\]")
    i = 0
    while True:
        i = text.find("![", i)
        if i < 0:
            return
        j = alt_close(i + 2)
        if j < 0:
            return
        if text.startswith("[", j + 1):
            k = label_close(j + 2)
            if k < 0:
                return
            if k > j + 2:
                yield i, k + 1, text[i + 2 : j], text[j + 2 : k]
                i = k + 1
                continue
        i += 1


class _InlineImageScanner:
    """``![alt](destination "title")`` as ``(start, end, alt, url, angle)``."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._close = _Next(text, r"\]")
        self._stop = _Next(text, r"[\s)]")
        self._angle_end = _Next(text, r"[<>\n]")
        self._open_paren = _Next(text, r"\(")
        self._title_end = {
            '"': _Next(text, '"'),
            "'": _Next(text, "'"),
            "(": _Next(text, r"[()]"),
        }

    def _tail(self, dest_end: int) -> int:
        """End of the ``)`` closing the parentheses after a destination, or -1."""
        text = self._text
        w = _skip_pad(text, dest_end)
        if text.startswith(")", w):
            return w + 1
        # A title needs whitespace before it.
        if w == dest_end or w >= len(text) or text[w] not in self._title_end:
            return -1
        c = self._title_end[text[w]](w + 1)
        if c < 0 or (text[w] == "(" and text[c] != ")"):
            return -1
        w = _skip_pad(text, c + 1)
        return w + 1 if text.startswith(")", w) else -1

    def _destination(self, p: int) -> tuple[str, int, bool] | None:
        text = self._text
        padded = _skip_pad(text, p) > p
        p = _skip_pad(text, p)
        if text.startswith("<", p):
            q = self._angle_end(p + 1)
            if q >= 0 and text[q] == ">":
                end = self._tail(q + 1)
                if end >= 0:
                    return text[p + 1 : q], end, True
        s = self._stop(p)
        # After padding the destination follows CommonMark, where parentheses
        # must balance; unpadded, the long-standing "up to the first )" applies.
        if s > p and padded and 0 <= self._open_paren(p) < s:
            return None
        if s > p:
            end = self._tail(s)
            if end >= 0:
                return text[p:s], end, False
        return None

    def __iter__(self) -> Iterator[tuple[int, int, str, str, bool]]:
        text = self._text
        i = 0
        last_j = -1
        last: tuple[str, int, bool] | None = None
        while True:
            i = text.find("![", i)
            if i < 0:
                return
            j = self._close(i + 2)
            if j < 0:
                return
            if text.startswith("(", j + 1):
                # The outcome depends on ``j`` only: openers sharing a ``]``
                # share it, so none of them rescans the whitespace after it.
                if j != last_j:
                    last_j, last = j, self._destination(j + 2)
                if last is not None:
                    url, end, angle = last
                    yield i, end, text[i + 2 : j], url, angle
                    i = end
                    continue
            i += 1


def _iter_reference_definitions(text: str) -> Iterator[tuple[str, str]]:
    """``[label]: destination "title"`` lines as ``(label, destination)``."""
    n = len(text)
    close = _Next(text, r"\]")
    angle_end = _Next(text, r"[<>\n]")
    title_end = {
        '"': _Next(text, '"'),
        "'": _Next(text, "'"),
        "(": _Next(text, r"[()]"),
    }

    def line_end(pos: int) -> int:
        e = text.find("\n", pos)
        return n if e < 0 else e

    def rest_blank(pos: int) -> bool:
        return not text[pos : line_end(pos)].strip()

    pos = 0
    while pos <= n:
        k = pos
        while k < pos + 3 and text.startswith(" ", k):
            k += 1
        next_line = None
        if text.startswith("[", k):
            e = close(k + 1)
            if e > k + 1 and text.startswith(":", e + 1):
                p = _skip_ws(text, e + 2)
                dest = None
                if p == e + 2:
                    pass  # the destination needs whitespace after the colon
                elif text.startswith("<", p):
                    q = angle_end(p + 1)
                    if q >= 0 and text[q] == ">" and (q + 1 >= n or text[q + 1].isspace()):
                        dest = (text[p + 1 : q], q + 1)
                if dest is None and p > e + 2:
                    m = _NON_WS.match(text, p)
                    if m:
                        dest = (m.group(0), m.end())
                if dest is not None:
                    url, d = dest
                    end = -1
                    w = _skip_ws(text, d)
                    if w > d and w < n and text[w] in title_end:
                        c = title_end[text[w]](w + 1)
                        if c >= 0 and (text[w] != "(" or text[c] == ")") and rest_blank(c + 1):
                            end = line_end(c + 1)
                    if end < 0 and rest_blank(d):
                        end = line_end(d)
                    if end >= 0:
                        yield text[k + 1 : e], url
                        next_line = end + 1
        if next_line is None:
            next_line = line_end(pos) + 1
        pos = next_line


def _tag_end(
    text: str, pos: int, quote_end: dict[str, _Next]
) -> tuple[int, int]:
    """End of the ``<img`` tag whose name ends at ``pos`` as ``(end, scanned)``.

    ``end`` is -1 when the tag is never closed; ``scanned`` is how far the
    attempt got. Attribute names and unquoted values cannot contain ``<``, so
    an unclosed tag stops at the next opener; only a quoted value can span one.
    """
    n = len(text)
    start = p = pos
    separator = _TAG_FIRST_SEPARATOR
    while True:
        q = separator.match(text, p).end()  # type: ignore[union-attr]
        separator = _WS
        if q >= n:
            return -1, n - start
        if text[q] == ">":
            return q + 1, q + 1 - start
        if text.startswith("/>", q):
            return q + 2, q + 2 - start
        m = _TAG_ATTR_NAME.match(text, q) if q > p else None  # needs a separator
        if m is None:
            return -1, q - start
        r = m.end()
        w = _skip_ws(text, r)
        if w < n and text[w] == "=":
            v = _skip_ws(text, w + 1)
            if v >= n:
                return -1, n - start
            if text[v] in quote_end:
                c = quote_end[text[v]](v + 1)
                if c < 0:
                    return -1, n - start
                p = c + 1
            else:
                m = _TAG_UNQUOTED_VALUE.match(text, v)
                if m is None:
                    return -1, v - start
                p = m.end()
        else:
            p = r


def _iter_html_img_tags(text: str) -> Iterator[tuple[int, int]]:
    """``<img ...>`` tags as ``(start, end)``, the way an HTML parser reads a tag.

    Quoted values are single units, so a ``>`` inside alt text does not end the
    tag, and an unclosed ``<img`` followed by prose does not match at all.
    ``/`` and whitespace both separate attributes (``<img/src=...>``).
    """
    quote_end = {'"': _Next(text, '"'), "'": _Next(text, "'")}
    budget = _TAG_SCAN_BUDGET_FACTOR * len(text) + _TAG_SCAN_BUDGET_FLOOR
    pos = 0
    while True:
        m = _IMG_OPEN.search(text, pos)
        if m is None:
            return
        end, scanned = _tag_end(text, m.end(), quote_end)
        if end >= 0:
            yield m.start(), end
            pos = end
        else:
            budget -= scanned
            if budget < 0:
                return
            pos = m.end()


def _rewrite(text: str, spans: Iterator[tuple[int, int, str]]) -> str:
    """``text`` with each ``(start, end, replacement)`` span replaced."""
    out: list[str] = []
    last = 0
    for start, end, replacement in spans:
        out.append(text[last:start])
        out.append(replacement)
        last = end
    out.append(text[last:])
    return "".join(out)


def extract_and_replace_images(
    md_content: str,
) -> tuple[str, list[dict[str, str]]]:
    """Extract images and replace their alt-text with sequential ``Image_N`` labels.

    Reference-style images are numbered first, then inline ones, then ``<img>``
    tags. Time is linear in the size of ``md_content`` for any input.
    """
    images: list[dict[str, str]] = []
    image_counter = 1

    reference_map: dict[str, str] = {}
    for label, url in _iter_reference_definitions(md_content):
        reference_map[label.lower()] = url.strip()

    def reference_spans() -> Iterator[tuple[int, int, str]]:
        nonlocal image_counter
        for start, end, original_alt, ref_id in _iter_reference_usages(md_content):
            url = reference_map.get(ref_id.lower(), f"[unknown reference: {ref_id}]")
            new_alt = f"Image_{image_counter}"
            images.append({
                "original_text": md_content[start:end],
                "url": url,
                "alt_text": original_alt,
                "new_alt_text": new_alt,
                "image_type": "reference",
            })
            image_counter += 1
            yield start, end, f"![{new_alt}][{ref_id}]"

    modified = _rewrite(md_content, reference_spans())

    def inline_spans(source: str) -> Iterator[tuple[int, int, str]]:
        nonlocal image_counter
        for start, end, original_alt, url, angle in _InlineImageScanner(source):
            new_alt = f"Image_{image_counter}"
            images.append({
                "original_text": source[start:end],
                "url": url,
                "alt_text": original_alt,
                "new_alt_text": new_alt,
                "image_type": "markdown",
            })
            image_counter += 1
            yield start, end, f"![{new_alt}](<{url}>)" if angle else f"![{new_alt}]({url})"

    modified = _rewrite(modified, inline_spans(modified))

    # Only the <img> tags are re-serialised: running the whole document through
    # an HTML parser escapes "&", "<" and ">" and closes anything that looks like
    # a tag, which corrupts code, autolinks and blockquotes.
    def html_spans(source: str) -> Iterator[tuple[int, int, str]]:
        nonlocal image_counter
        for start, end in _iter_html_img_tags(source):
            tag_text = source[start:end]
            fragment = BeautifulSoup(tag_text, "html.parser")
            tags = fragment.find_all(True)
            if len(tags) != 1 or tags[0].name != "img" or fragment.get_text():
                continue
            img_tag = tags[0]
            if source[start + 4 : start + 6] not in ("/>", "") and source[start + 4] == "/" and not img_tag.get("src"):
                continue  # <img/...> is only a tag worth rewriting when it has a src
            new_alt = f"Image_{image_counter}"
            images.append({
                "original_text": str(img_tag),
                "url": img_tag.get("src", ""),
                "alt_text": img_tag.get("alt", ""),
                "new_alt_text": new_alt,
                "image_type": "html",
            })
            img_tag["alt"] = new_alt
            image_counter += 1
            yield start, end, str(img_tag)

    modified = _rewrite(modified, html_spans(modified))
    return modified, images
