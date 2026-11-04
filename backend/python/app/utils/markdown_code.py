"""Put text into markdown as fenced code, so the parser keeps it as code.

Text that is not markdown, such as a diff or a source file, is read as markdown
when it is pasted in bare: a diff's removed lines become list items, a context
line with four leading spaces opens an indented code block, ``#`` starts a
heading, and underscores turn into emphasis. In a fence the parser makes one
code block of it and keeps every character.

A fenced block becomes a single block however long it is (the markdown walker
splits only prose), so long text is fenced in pieces of at most
``MAX_CODE_FENCE_CHARS``: at hunk starts for a diff, else at line ends.

Kept free of parser imports: the connectors call it, and the parser modules pull
in sentence segmentation and language detection.
"""

from __future__ import annotations

import re

# Mirrors app.modules.parsers.text_splitting.MAX_TEXT_BLOCK_CHARS, the largest
# block the parsers make from prose (tests keep the two equal).
MAX_CODE_FENCE_CHARS = 50_000

_BACKTICK_RUN_RE = re.compile(r"`+")
_HUNK_START_RE = re.compile(r"^@@", re.MULTILINE)


def _fence_for(text: str) -> str:
    """A backtick fence longer than any backtick run in *text*, so none closes it."""
    longest = max((len(m.group()) for m in _BACKTICK_RUN_RE.finditer(text)), default=0)
    return "`" * max(3, longest + 1)


def _cut_lines(text: str, max_chars: int) -> list[str]:
    """*text* in pieces of at most *max_chars*, cut after a line end where one
    falls inside the piece; a single line longer than that is cut where it must."""
    # An offset, not a shrinking copy: copying the rest on every cut is
    # quadratic on a long diff.
    pieces: list[str] = []
    offset = 0
    while len(text) - offset > max_chars:
        limit = offset + max_chars
        cut = text.rfind("\n", offset, limit)
        cut = cut + 1 if cut > offset else limit
        pieces.append(text[offset:cut])
        offset = cut
    if offset < len(text):
        pieces.append(text[offset:])
    return pieces


def _units(text: str, info: str) -> list[str]:
    """The pieces a split may not cross unless one is too long by itself: a
    diff's hunks (with whatever precedes the first), else the whole text."""
    if info != "diff":
        return [text]
    starts = [m.start() for m in _HUNK_START_RE.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)]) if text[a:b]]


def fenced_code(text: str, info: str = "", *, max_chars: int = MAX_CODE_FENCE_CHARS) -> str:
    """Markdown holding *text* as one or more fenced code blocks.

    *info* is the fence's info string (a language such as ``diff`` or
    ``python``, or empty). Each block holds at most *max_chars* of *text*;
    hunks of a ``diff`` are kept whole unless one alone is longer.
    """
    info = info.strip()
    pieces: list[str] = []
    current = ""
    for unit in _units(text, info):
        for part in _cut_lines(unit, max_chars):
            if current and len(current) + len(part) > max_chars:
                pieces.append(current)
                current = ""
            current += part
    if current:
        pieces.append(current)
    # The parser drops a code block's trailing newlines and a block boundary
    # puts back one, so each piece keeps only its last line's newline. Blank
    # lines after it go to the start of the next piece, where the parser keeps
    # them, and a piece of blank lines alone joins the next one.
    blocks = []
    carry = ""

    def emit(body: str) -> None:
        fence = _fence_for(body)
        blocks.append(f"{fence}{info}\n{body}\n{fence}")

    for piece in pieces:
        text_here = carry + piece
        body = text_here.rstrip("\n")
        if not body.strip():
            carry = text_here
            continue
        extra = len(text_here) - len(body)
        carry = "\n" * (extra - 1) if extra > 1 else ""
        emit(body)
    # A tail that never reached a piece with content. Lines of only spaces are
    # content here — a diff's empty context lines look exactly like that, and
    # dropping them would lose the last line of a diff. A carry of newlines
    # alone goes, because the parser strips those anyway, and so does text that
    # is only blank from the start: `fenced_code("   \n")` is nothing to fence,
    # not a block holding three spaces.
    if blocks and carry.rstrip("\n"):
        emit(carry.rstrip("\n"))
    return "\n\n".join(blocks)
