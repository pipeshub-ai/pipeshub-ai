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
    "PendingEdgeFact",
    "Relation",
]


class Relation:
    """The relation set implemented in this build.

    Values match ``RecordRelations`` in ``app/config/constants/arangodb.py``.
    """

    # Local -- both endpoints known while parsing one file.
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    METHOD = "METHOD"

    # Cross-file -- target is a bare name, deferred to the resolver.
    CALLS = "CALLS"
    INDIRECT_CALL = "INDIRECT_CALL"
    IMPORTS = "IMPORTS"
    IMPORTS_FROM = "IMPORTS_FROM"
    RE_EXPORTS = "RE_EXPORTS"
    EXPORTS = "EXPORTS"
    INHERITS = "INHERITS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"


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

    Every byte of a scope belongs to exactly one symbol at that level, so this
    covers both real definitions and the filler spans between them.
    """

    kind: str
    name: str | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    # Full chain of enclosing container names, outermost first. Truncating this
    # to one level collapses Outer.Inner.run onto Outer.run and silently
    # collides symbol IDs.
    parent_chain: tuple[str, ...] = ()
    text: str = ""
    decorators: tuple[str, ...] = ()
    is_exported: bool = False
    # Index into ParsedFile.symbols of the immediately enclosing container, or
    # None at file level. Drives both block nesting and the tiling invariant.
    parent: int | None = None
    # True when this container has members that tile it.
    is_container: bool = False

    @property
    def is_filler(self) -> bool:
        return self.kind in FILLER_KINDS


@dataclass
class PendingEdgeFact:
    """A cross-file edge whose source is known and whose target is a bare name."""

    relation: str
    to_name: str
    line: int
    # Byte offset of the reference in the source. The owning block is whichever
    # block's range most tightly contains this, resolved once every span exists
    # -- so a call at module level belongs to its statements block rather than
    # falling back to the file record.
    byte_offset: int = 0
    # Filled in by the block mapper from byte_offset. Left None for facts that
    # belong to the file itself rather than to any span (EXPORTS).
    from_symbol: int | None = None
    from_kind: str = "block"
    to_kind: str = "block"
    is_member_call: bool = False
    receiver: str | None = None
    receiver_type: str | None = None
    qualified_prefix: str | None = None
    indirect: bool = False
    context: str | None = None
    # EXPORTS names a symbol in *this* file; resolving it against the whole repo
    # would happily bind to a same-named symbol elsewhere.
    restrict_to_file: bool = False
    # Exempt from byte-offset re-attribution. A base class is named inside the
    # header span, but it is the *class* that inherits, so heritage edges stay on
    # the container rather than sliding onto its header block.
    pinned: bool = False

    def to_dict(self) -> dict:
        out: dict = {
            "relation": self.relation,
            "toName": self.to_name,
            "line": self.line,
            "fromKind": self.from_kind,
            "toKind": self.to_kind,
        }
        if self.is_member_call:
            out["isMemberCall"] = True
        if self.receiver:
            out["receiver"] = self.receiver
        if self.receiver_type:
            out["receiverType"] = self.receiver_type
        if self.qualified_prefix:
            out["qualifiedPrefix"] = self.qualified_prefix
        if self.indirect:
            out["indirect"] = True
        if self.context:
            out["context"] = self.context
        if self.restrict_to_file:
            out["restrictToFile"] = True
        return out


@dataclass
class ParsedFile:
    language: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    pending: list[PendingEdgeFact] = field(default_factory=list)
    # Receiver-variable -> declared type, harvested from local declarations.
    # Cannot be reconstructed later without re-parsing, so it is captured here
    # and carried to the resolver on the file-summary block.
    type_table: dict[str, str] = field(default_factory=dict)
    parse_error_line: int | None = None
    skipped_reason: str | None = None
