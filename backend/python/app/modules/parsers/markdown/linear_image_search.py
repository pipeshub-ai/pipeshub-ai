"""Linear-time search for Markdown and HTML image references.

Why this exists
---------------
The image patterns in this package (``![alt](url)``
and ``<img ... alt="...">``) use ``[^\\]]*`` or ``[^>]*`` for the
part between the opener and its closer. ``re.search`` tries every opener in
turn, and an opener that fails has already scanned forward to the closer (or
to the end of the text when there is none). A document with *n* openers and no
usable closer, such as ``<![CDATA[`` repeated (it contains ``![``), therefore
costs O(n^2): a 256 KB file kept a parse worker busy for several seconds.

How it works
------------
``LinearImageSearch`` finds the same match the regex would find. It walks the
openers itself, and before it gives the regex an opener it answers "can this
opener match at all?" from cached "next occurrence" lookups. The regex itself
only runs for an opener that is known to match (or that is cheap to reject),
and a successful match is consumed, so the search moves on. Every lookup is
cached over the interval it proved empty, so openers that share a closer share
one scan. The regex still produces the ``re.Match`` object, so groups, spans
and callers are unchanged.

The "can this opener match" tests are exact, not heuristics. They are derived
from each pattern in the comments next to the accept functions, and
``tests/unit/parsers/test_markdown_linear_search.py`` compares every search with
the plain regex on tracked Markdown files and on seeded random input.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Final

__all__ = [
    "LinearImageSearch",
    "MD_IMAGE_NESTED",
    "HTML_IMG_ALT",
]


class _Next:
    """First index >= ``pos`` of a pattern, remembering the gap it just proved empty."""

    __slots__ = ("_hit", "_lo", "_rx")

    def __init__(self, rx: re.Pattern[str]) -> None:
        self._rx = rx
        self._lo = -1
        self._hit = -2  # -2: nothing cached

    def __call__(self, text: str, pos: int) -> int:
        if self._hit != -2 and pos >= self._lo and (self._hit == -1 or pos <= self._hit):
            return self._hit
        m = self._rx.search(text, pos)
        self._lo = pos
        self._hit = m.start() if m else -1
        return self._hit


_BRACKET = re.compile(r"[\[\]]")
_CLOSE = re.compile(r"\]")
_STOP = re.compile(r"[\s)]")
_PAREN = re.compile(r"\)")
_TITLE_TAIL = re.compile(r'\s+"[^"]*"\)')
_ALT = re.compile(r"alt=[\"']", re.IGNORECASE)
_GT = re.compile(r">")
_QUOTE = re.compile(r"[\"']")
_DATA_STOP = re.compile(r'["\s)]')
_DATA_QUOTE_STOP = re.compile(r'[")]')
_NON_SPACE = re.compile(r"\S")
_TITLE_LOOKAHEAD = re.compile(r'"[^"]*"\s*\)')
_TITLE_CLOSED = re.compile(r'"[^"]*"\)')


class _Scan:
    """Lookup caches for one search over one text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.close = _Next(_CLOSE)
        self.stop = _Next(_STOP)
        self.paren = _Next(_PAREN)
        self.alt = _Next(_ALT)
        self.gt = _Next(_GT)
        self.quote = _Next(_QUOTE)
        self.data_stop = _Next(_DATA_STOP)
        self.data_quote_stop = _Next(_DATA_QUOTE_STOP)
        self.non_space = _Next(_NON_SPACE)
        self._data_ok: dict[int, bool] = {}
        self._bracket = _Next(_BRACKET)
        self._alt_end: dict[int, int] = {}
        self._last_gt = -2

    def last_gt(self) -> int:
        if self._last_gt == -2:
            self._last_gt = self.text.rfind(">")
        return self._last_gt

    def nested_alt_end(self, start: int) -> int:
        """Index of the ``]`` that ends link text starting at *start*, or -1.

        The text is a run of characters other than brackets and of ``[...]``
        groups (a ``[`` runs to the first ``]`` after it). That reading is
        unique, so a left-to-right walk finds the same ``]`` the regex does.
        The walk resumes after each group, and every resume point is cached,
        so openers whose walks meet share the rest of it.
        """
        text = self.text
        memo = self._alt_end
        seen: list[int] = []
        pos = start
        while True:
            known = memo.get(pos)
            if known is not None:
                end = known
                break
            seen.append(pos)
            b = self._bracket(text, pos)
            if b < 0:
                end = -1
                break
            if text[b] == "]":
                end = b
                break
            close = self.close(text, b + 1)
            if close < 0:
                end = -1
                break
            pos = close + 1
        for p in seen:
            memo[p] = end
        return end


    def data_destination_ends(self, start: int) -> bool:
        """Whether a ``data:`` destination whose body starts at *start* can match.

        The regex reads the body left to right with no choice that changes the
        outcome: a run of characters other than quote, whitespace and ``)``; a
        quote with everything up to the next quote or ``)``; or a whitespace
        run. The first ``)`` outside a quote ends it with a match. A whitespace
        run followed by a quoted title and ``)`` ends it too, but it matches
        only when the ``)`` comes right after the title (a space before ``)``
        does not match). Running out of text is a failure. The outcome depends
        only on where a step starts, so it is cached for every step visited.
        """
        text = self.text
        memo = self._data_ok
        seen: list[int] = []
        pos = start
        while True:
            known = memo.get(pos)
            if known is not None:
                ok = known
                break
            seen.append(pos)
            stop = self.data_stop(text, pos)
            if stop < 0:
                ok = False
                break
            ch = text[stop]
            if ch == ")":
                ok = True
                break
            if ch == '"':
                q = self.data_quote_stop(text, stop + 1)
                if q < 0:
                    ok = False
                    break
                if text[q] == ")":
                    ok = True
                    break
                pos = q
                continue
            nxt = self.non_space(text, stop)
            if nxt < 0:
                ok = False
                break
            if _TITLE_LOOKAHEAD.match(text, nxt):
                ok = _TITLE_CLOSED.match(text, nxt) is not None
                break
            pos = nxt
        for p in seen:
            memo[p] = ok
        return ok


Accept = Callable[[_Scan, int], bool]


def _md_image_accept() -> Accept:
    # ``![alt](dest "title")``. The alt text ends at one fixed ``]`` (see
    # ``nested_alt_end``). The
    # destination is either ``data:`` followed by a body that
    # ``data_destination_ends`` reads, or a run of characters other than whitespace and ``)``. A run that
    # stops at ``)`` matches; one that stops at whitespace matches only when a
    # quoted title and ``)`` follow.
    def accept(scan: _Scan, i: int) -> bool:
        text = scan.text
        end = scan.nested_alt_end(i + 2)
        if end < 0 or not text.startswith("(", end + 1):
            return False
        pos = end + 2
        if text.startswith("data:", pos):
            return scan.data_destination_ends(pos + 5)
        stop = scan.stop(text, pos)
        if stop <= pos:
            return False
        if text[stop] == ")":
            return True
        return _TITLE_TAIL.match(text, stop) is not None

    return accept


def _html_img_accept(scan: _Scan, i: int) -> bool:
    # ``<img ... alt="...">``: some ``alt=`` quote opener before the first ``>``,
    # a closing quote after it, and a ``>`` after that. The earliest ``alt=``
    # has the earliest closing quote, so it is enough to test that one.
    text = scan.text
    first_alt = scan.alt(text, i + 5)
    if first_alt < 0:
        return False
    gt = scan.gt(text, i + 5)
    if 0 <= gt < first_alt:
        return False
    closing = scan.quote(text, first_alt + 5)
    return closing >= 0 and scan.last_gt() > closing


class LinearImageSearch:
    """``re.Pattern`` stand-in (``search``, ``finditer``, ``sub``) that cannot go quadratic."""

    def __init__(self, pattern: re.Pattern[str], opener: re.Pattern[str], accept: Accept) -> None:
        self._pattern = pattern
        self._opener = opener
        self._accept = accept

    @property
    def pattern(self) -> str:
        return self._pattern.pattern

    def scan(self, text: str) -> _Scan:
        """Caches for searching *text*. Pass it to ``search`` when calling it
        once per match, so work done for one match is reused by the next.
        Keep it local to one text and one caller: it is not thread-safe."""
        return _Scan(text)

    def search(self, text: str, pos: int = 0, scan: _Scan | None = None) -> re.Match[str] | None:
        return self._search(text, pos, scan or _Scan(text))

    def _search(self, text: str, pos: int, scan: _Scan) -> re.Match[str] | None:
        opener = self._opener
        while True:
            m = opener.search(text, pos)
            if m is None:
                return None
            i = m.start()
            if self._accept(scan, i):
                found = self._pattern.match(text, i)
                if found is not None:
                    return found
            pos = i + 1

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        pos = 0
        scan = _Scan(text)
        while True:
            found = self._search(text, pos, scan)
            if found is None:
                return
            yield found
            pos = found.end()

    def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
        out: list[str] = []
        pos = 0
        for found in self.finditer(text):
            out.append(text[pos : found.start()])
            out.append(repl(found))
            pos = found.end()
        out.append(text[pos:])
        return "".join(out)


_BANG_BRACKET: Final = re.compile(r"!\[")

# Converter patterns: link text may hold one level of ``[...]``.
# The quantifiers in the destination are possessive so that no two of them can
# split the same characters between them: with plain ``\s+`` inside ``(...)*``
# a run of k spaces had 2**k splits and ``![a](data:`` + 30 spaces + ``"t" )``
# ran for minutes. A possessive run gives the same matches: where the old
# pattern backtracked into a run, the first split that could succeed was "all
# but the last space" (the last one is left for the title), which the second
# alternative reproduces, so even the captured destination is unchanged.
MD_IMAGE_NESTED: Final = LinearImageSearch(
    re.compile(
        r'!\[((?:[^\[\]]|\[[^\]]*\])*)\]\('
        r'(data:(?:[^"\s)]|(?:\s++(?!"[^"]*"\s*\))|(?:\s(?=\s))++)|"[^")]*+)*|[^\s)]++)'
        r'(?:\s+"[^"]*")?\)'
    ),
    _BANG_BRACKET,
    _md_image_accept(),
)
HTML_IMG_ALT: Final = LinearImageSearch(
    re.compile(r'<img\s[^>]*alt=["\']([^"\']*)["\'][^>]*>', re.IGNORECASE),
    re.compile(r"<img\s", re.IGNORECASE),
    _html_img_accept,
)
