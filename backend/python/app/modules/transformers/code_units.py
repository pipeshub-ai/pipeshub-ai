"""What of a code block gets embedded (SC-9).

A symbol was embedded whole: a 3,000-line function ran past the model's window
and lost everything after it, a separator `;` or `'use client';` became its own
vector, and an outer function embedded its nested functions a second time. The
camelCase / snake_case subtokens the parser computes for keyword search were
never fed to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = ["RESIDUE_UNIT_KIND", "CodeUnit", "class_header_unit", "code_units", "group_members", "residue_pieces"]

# The vector payload's unitKind of a file's residue unit (``residue_pieces``).
RESIDUE_UNIT_KIND = "residue"

# An unnamed span with fewer identifier characters than this says nothing a
# search could match (`;`, `}`, `'use client';`).
_MIN_IDENTIFIER_CHARS = 16
_IDENTIFIER_CHARS = re.compile(r"[A-Za-z0-9_]")
# The parser's kinds for spans between definitions (engine._COALESCING, "header").
_FILLER_KINDS = frozenset({"statements", "imports", "comment", "header"})
_ELIDED = "    …"
# The parser already cuts a docstring to 500 characters (code_file_parser._extract_docstring); this line
# cap binds only a Python docstring of very short lines.
_DOCSTRING_LINES = 40


# Tokens per code unit: the window of the default local embedding model. Not a
# setting, since changing it mixes unit sizes in the vector index until a re-embed.
UNIT_TOKENS = 512


@dataclass(frozen=True)
class CodeUnit:
    block: Any
    text: str
    whole: bool
    # Extra keyword-search text (the block's subtokens); never shown or embedded densely.
    sparse_extra: str = ""
    # Words a line longer than a unit was cut through: whole in no piece, so the file's residue holds them.
    cut_words: tuple[str, ...] = ()


def code_units(blocks: list, header_of: Callable[[Any], str], *,
               exceeds: Callable[[str, int], bool], token_len: Callable[[str], int],
               split_long_line: Callable[[str, int], list[str]],
               budget: int | None = None) -> list[CodeUnit]:
    """``exceeds(text, n)`` is the cheap over-n-tokens test; ``token_len`` and
    ``split_long_line`` are only used on a symbol that has to be split."""
    budget = budget or UNIT_TOKENS
    nested = _nested_line_ranges(blocks)
    units: list[CodeUnit] = []
    for block in blocks:
        data = block.data if isinstance(block.data, dict) else {}
        text = _without_nested(data, nested.get(block.index, ()))
        if not text.strip():
            continue
        meta = getattr(block, "code_metadata", None)
        if _small_filler(meta, text):
            continue
        header = header_of(block)
        sparse = data.get("subtokens") or ""
        whole = f"{header}\n{text}" if header else text
        if not exceeds(whole, budget):
            units.append(CodeUnit(block, whole, whole=True, sparse_extra=sparse))
            continue
        lead = "\n".join(p for p in (header, _signature_line(meta)) if p)
        cuts: list[str] = []
        pieces = _pieces(text, budget - token_len(lead) - 1, token_len, split_long_line, cuts.extend)
        # On the first piece only: the block's cut words, carried by every piece, would grow as pieces x cuts.
        units.extend(
            CodeUnit(block, f"{lead}\n{piece}" if lead else piece, whole=False, sparse_extra=sparse,
                     cut_words=tuple(cuts) if i == 0 else ())
            for i, piece in enumerate(pieces)
        )
    return units


def _small_filler(meta: object, text: str) -> bool:
    return (getattr(meta, "kind", None) in _FILLER_KINDS
            and len(_IDENTIFIER_CHARS.findall(text)) < _MIN_IDENTIFIER_CHARS)


def residue_pieces(blocks: list, header: str, *, token_len: Callable[[str], int],
                   split_long_line: Callable[[str, int], list[str]], budget: int | None = None,
                   cut_words: Iterable[str] = ()) -> list[str]:
    """The short filler spans ``code_units`` leaves out (``import os``, ``RATE = 0.07``, ``'use client';``),
    joined in file order under ``header`` and packed to the unit budget: one unit for a file, unless its
    residue is longer than a unit. With it every line holding a word is in some unit, so a keyword lookup
    that finds nothing has proved the text absent. Spans with no identifier character (``}``) hold no word
    to look up and are left out."""
    budget = budget or UNIT_TOKENS
    nested = _nested_line_ranges(blocks)
    spans: list[str] = []
    for block in blocks:
        data = block.data if isinstance(block.data, dict) else {}
        text = _without_nested(data, nested.get(block.index, ()))
        if _IDENTIFIER_CHARS.search(text) and _small_filler(getattr(block, "code_metadata", None), text):
            spans.append(text.strip("\n"))
    room = budget - (token_len(header) + 1 if header else 0)
    # ``CodeUnit.cut_words`` of the file's block units; a word over the room would be cut again here, whole in none.
    if cut := sorted(w for w in set(cut_words) if token_len(w) <= room):
        spans.append(" ".join(cut))
    if not spans:
        return []

    def at_spaces(line: str, limit: int) -> list[str]:
        # A word cut across two pieces is in neither as a whole word, which a keyword lookup needs.
        out: list[str] = []
        current = ""
        for word in re.split(r"(?<=\s)", line):
            if current and token_len(current + word) > limit:
                out.append(current)
                current = ""
            current += word
            if token_len(current) > limit:  # one word over a whole unit: nothing to cut at
                out.extend(split_long_line(current, limit))
                current = ""
        if current.strip():
            out.append(current)
        return out

    pieces = _pieces("\n".join(spans), room, token_len, at_spaces)
    return [f"{header}\n{piece}" if header else piece for piece in pieces]


def group_members(group: object, blocks: list) -> list:
    """The blocks a group lists as its children, in index order."""
    by_index = {block.index: block for block in blocks}
    ranges = getattr(getattr(group, "children", None), "block_ranges", None) or ()
    return [by_index[i] for r in ranges for i in range(r.start, r.end + 1) if i in by_index]


def class_header_unit(group: object, members: list, header: str, *,
                      token_len: Callable[[str], int],
                      split_long_line: Callable[[str, int], list[str]],
                      budget: int | None = None) -> CodeUnit | None:
    """A class as one unit: its signature, its docstring (at most 500 characters, as the parser keeps
    it, and 40 lines) and each member's signature, without bodies, cut to ``budget`` tokens. The members' code is embedded by their
    own units; this one is what a search for the class itself matches."""
    budget = budget or UNIT_TOKENS
    meta = getattr(group, "code_metadata", None)
    doc = (getattr(meta, "docstring", None) or "").strip()
    lines = [(getattr(meta, "signature", None) or "").strip(), *doc.split("\n")[:_DOCSTRING_LINES]]
    for block in members:
        member = getattr(block, "code_metadata", None)
        if getattr(member, "kind", None) not in _FILLER_KINDS:
            lines.append((getattr(member, "signature", None) or "").strip())
    body = [line for line in lines if line.strip()]
    if not body:
        return None
    kept: list[str] = []
    used = 0
    for line in ([header] if header else []) + body:
        cost = token_len(line) + 1
        if used + cost > budget:
            if not kept:  # a header or signature longer than the whole budget
                kept.append(split_long_line(line, budget)[0])
            break
        kept.append(line)
        used += cost
    return CodeUnit(group, "\n".join(kept), whole=True)


def _nested_line_ranges(blocks: list) -> dict[int, list[tuple[int, int]]]:
    """Outer block index -> line ranges of the blocks nested in it."""
    out: dict[int, list[tuple[int, int]]] = {}
    for block in blocks:
        parent = getattr(block, "parent_block_index", None)
        data = block.data if isinstance(block.data, dict) else {}
        start, end = data.get("start_line"), data.get("end_line")
        if parent is not None and isinstance(start, int) and isinstance(end, int):
            out.setdefault(parent, []).append((start, end))
    return out


def _without_nested(data: dict, ranges: list[tuple[int, int]] | tuple) -> str:
    """The block's text with nested blocks' lines folded to one marker line,
    since those blocks are embedded on their own."""
    text = data.get("text") or ""
    start = data.get("start_line")
    if not ranges or not isinstance(start, int):
        return text
    lines = text.split("\n")
    keep: list[str] = []
    skipping = False
    for offset, line in enumerate(lines):
        number = start + offset
        if any(lo <= number <= hi for lo, hi in ranges):
            if not skipping:
                keep.append(_ELIDED)
            skipping = True
            continue
        skipping = False
        keep.append(line)
    return "\n".join(keep)


def _signature_line(meta: object) -> str:
    """Signature and the docstring's first line: what every piece of a split
    symbol repeats so it still says which symbol it belongs to."""
    signature = (getattr(meta, "signature", None) or "").strip()
    doc = (getattr(meta, "docstring", None) or "").strip().split("\n", 1)[0]
    return " — ".join(p for p in (signature, doc) if p)


def _pieces(text: str, budget: int, token_len: Callable[[str], int],
            split_long_line: Callable[[str, int], list[str]],
            on_cut: Callable[[list[str]], None] | None = None) -> list[str]:
    """Consecutive lines packed up to ``budget`` tokens; a longer line is cut, and ``on_cut`` is told the
    words a cut went through."""
    budget = max(16, budget)
    pieces: list[str] = []
    current: list[str] = []
    used = 0
    for line in text.split("\n"):
        cost = token_len(line) + 1
        if cost > budget:
            if current:
                pieces.append("\n".join(current))
                current, used = [], 0
            parts = split_long_line(line, budget)
            if on_cut is not None:
                on_cut(_straddling(parts))
            pieces.extend(parts)
            continue
        if used + cost > budget and current:
            pieces.append("\n".join(current))
            current, used = [], 0
        current.append(line)
        used += cost
    if current and "\n".join(current).strip():
        pieces.append("\n".join(current))
    return [p for p in pieces if p.strip()]


_WORD_END, _WORD_START, _ALL_WORD = re.compile(r"\w+$"), re.compile(r"^\w+"), re.compile(r"\w+")


def _straddling(parts: list[str]) -> list[str]:
    """The words a cut line's parts split, each rebuilt whole: a word runs on through every part that is
    entirely word characters, so one can span any number of parts."""
    out = []
    i = 0
    while i < len(parts) - 1:
        head = _WORD_END.search(parts[i])
        if not head or not _WORD_START.match(parts[i + 1]):
            i += 1
            continue
        word, j = head.group(), i + 1
        while j < len(parts) - 1 and _ALL_WORD.fullmatch(parts[j]) and _WORD_START.match(parts[j + 1]):
            word += parts[j]
            j += 1
        out.append(word + _WORD_START.match(parts[j]).group())
        i = j
    return out
