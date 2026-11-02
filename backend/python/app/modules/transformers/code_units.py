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
    from collections.abc import Callable

__all__ = ["CodeUnit", "code_units"]

# An unnamed span with fewer identifier characters than this says nothing a
# search could match (`;`, `}`, `'use client';`).
_MIN_IDENTIFIER_CHARS = 16
_IDENTIFIER_CHARS = re.compile(r"[A-Za-z0-9_]")
# The parser's kinds for spans between definitions (engine._COALESCING, "header").
_FILLER_KINDS = frozenset({"statements", "imports", "comment", "header"})
_ELIDED = "    …"


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
        if getattr(meta, "kind", None) in _FILLER_KINDS and \
                len(_IDENTIFIER_CHARS.findall(text)) < _MIN_IDENTIFIER_CHARS:
            continue
        header = header_of(block)
        sparse = data.get("subtokens") or ""
        whole = f"{header}\n{text}" if header else text
        if not exceeds(whole, budget):
            units.append(CodeUnit(block, whole, whole=True, sparse_extra=sparse))
            continue
        lead = "\n".join(p for p in (header, _signature_line(meta)) if p)
        units.extend(
            CodeUnit(block, f"{lead}\n{piece}" if lead else piece, whole=False, sparse_extra=sparse)
            for piece in _pieces(text, budget - token_len(lead) - 1, token_len, split_long_line)
        )
    return units


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
            split_long_line: Callable[[str, int], list[str]]) -> list[str]:
    """Consecutive lines packed up to ``budget`` tokens; a longer line is cut."""
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
            pieces.extend(split_long_line(line, budget))
            continue
        if used + cost > budget and current:
            pieces.append("\n".join(current))
            current, used = [], 0
        current.append(line)
        used += cost
    if current and "\n".join(current).strip():
        pieces.append("\n".join(current))
    return [p for p in pieces if p.strip()]
