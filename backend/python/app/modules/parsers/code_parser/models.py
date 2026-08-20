"""Data model shared by the tree-sitter engine and the block mapper.

These are parser-layer types only: plain names and line numbers, no graph IDs
and no record context. ID minting happens later, in the block mapper, which is
the only place that knows the file's repo-relative path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "FILLER_KINDS",
    "HEADER_KIND",
    "ParsedFile",
    "ParsedSymbol",
]


# Spans that carry no definition of their own. They exist so that the blocks of a
# file tile it exactly -- without them, imports, module-level code, comments and
# whitespace are simply dropped.
FILLER_KINDS = frozenset({"imports", "statements", "comment", "header"})

# A container's members are what its children tile around; `header` covers the
# span from the container's start to its first member.
HEADER_KIND = "header"


@dataclass
class ParsedSymbol:
    """One span of a file.

    Every line of a scope belongs to exactly one symbol at that level, so this
    covers both real definitions and the filler spans between them.
    """

    kind: str
    name: str | None
    start_line: int
    end_line: int
    # Full chain of enclosing container names, outermost first. Truncating this
    # to one level collapses Outer.Inner.run onto Outer.run and silently
    # collides symbol IDs.
    parent_chain: tuple[str, ...] = ()
    text: str = ""
    decorators: tuple[str, ...] = ()
    # Index into ParsedFile.symbols of the immediately enclosing container, or
    # None at file level. Drives both block nesting and the tiling invariant.
    parent: int | None = None
    # True when this container has members that tile it.
    is_container: bool = False

    @property
    def is_filler(self) -> bool:
        return self.kind in FILLER_KINDS


@dataclass
class ParsedFile:
    language: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    parse_error_line: int | None = None
    skipped_reason: str | None = None
