"""Data model shared by the tree-sitter engine and the block mapper.

These are parser-layer types only: plain names and line numbers, no graph IDs
and no record context. ID minting happens later, in the block mapper, which is
the only place that knows the file's repo-relative path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CONSTRUCT",
    "DECLARATION",
    "EXPLICIT",
    "FILLER_KINDS",
    "HEADER_KIND",
    "INITIALIZER",
    "ParsedFile",
    "ParsedSymbol",
    "PendingEdgeFact",
    "RETURNS",
    "Relation",
    "STATIC",
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
    IMPLEMENTS = "IMPLEMENTS"


# Spans that carry no definition of their own. They exist so that the blocks of a
# file tile it exactly -- without them, imports, module-level code, comments and
# whitespace are simply dropped.
FILLER_KINDS = frozenset({"imports", "statements", "comment", "header"})
# The modifier of a function or method declared without a body: what runs is elsewhere.
DECLARATION = "declaration"
# The modifier of a C/C++ free function with internal linkage (`static`): callable only from its translation unit.
STATIC = "static"
# The modifier prefix of a C++ function or method returning a class (`returns:Conn` for `Conn* Pool::get()`): what
# `pool.get()->open()` and `auto c = pool.get()` are typed by.
RETURNS = "returns:"
# The context of a CALLS fact a C++ construction makes (`new Conn(..)`, `Conn c(..)`, `: Base(..)`): it names a type.
CONSTRUCT = "construct"
# The context of a CALLS fact from a C++ constructor's `: Name(..)` initializer: a base (constructed) or a member.
INITIALIZER = "initializer"
# The modifier prefix of an explicit interface implementation (`explicit:IHandler` on C#'s
# `Task IHandler.Handle(..)`): it implements IHandler.Handle and is no member of its own type by that name.
EXPLICIT = "explicit:"
# The receiver type the parser gives an untyped parameter of a pytest test or fixture: `=fixture:<name>`, for the
# builder to type from the fixture of that name.
FIXTURE_MARK = "=fixture:"
# A Python module's literal `__all__`, as its type table keeps it under "__all__": `=all:a,b`.
STAR_EXPORTS_MARK = "=all:"

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
    # Words resolution reads, from the language's handler (languages.MODIFIER_HANDLERS; C/C++: STATIC; C#:
    # "partial", "extension", "this:<Type>", EXPLICIT), after DECLARATION for a callable with no body.
    modifiers: tuple[str, ...] = ()
    is_exported: bool = False
    # Index into ParsedFile.symbols of the immediately enclosing container, or
    # None at file level. Drives both block nesting and the tiling invariant.
    parent: int | None = None
    # True when this container has members that tile it.
    is_container: bool = False
    # A `@property`/`cached_property` method or a `get` accessor: `self.x` reads it, nothing is handed over.
    is_accessor: bool = False
    # Type parameters the declaration takes (`class Foo<T, U>`: 2).
    type_param_count: int = 0
    # False for a JS/TS module-level value (`export const X = f()`): its initializer runs as module code, so
    # the facts inside it are the file's, not the value's.
    owns_facts: bool = True

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
    # Set by the walker to the span that lexically encloses the reference, then
    # narrowed to the innermost span containing byte_offset.
    from_symbol: int | None = None
    from_kind: str = "block"
    to_kind: str = "block"
    is_member_call: bool = False
    # C++ `p->f()`: the call goes through `->`, which a class with an `operator->` (optional_ptr<T>) forwards to T.
    arrow: bool = False
    receiver: str | None = None
    receiver_type: str | None = None
    qualified_prefix: str | None = None
    # TS: the function-scoped type (or `=initializer`) of the receiver chain's first name.
    head_type: str | None = None
    # TS: that name's type argument in the same scope (`Foo` of `v: IObservable<Foo>`).
    head_type_arg: str | None = None
    # The name an import binds in the importing file, when it differs from the
    # imported one: `b` in `from m import a as b` and `import { a as b }`, `ns`
    # in `import * as ns`. Calls are written against this name.
    local_name: str | None = None
    # CALLS: "construct" for a C++ construction (`new Conn(..)`, `Conn c(..)`), which names a type, never a
    # function; "initializer" for a constructor's `: Name(..)`, which names a base or a member. IMPORTS:
    # "default" for a JS default import, whose local name is the importer's choice, not the module's.
    # INDIRECT_CALL: the value position ("argument", "collection", "assignment", "return", "prop" for a JSX
    # prop handler `onClick={f}`, "dispatch" for each value of a table called as `TABLE[k]()`).
    context: str | None = None
    # A type reference's type-argument count (`IHandler<int>`: 1), where the language reads it (C# bases).
    type_args: int | None = None
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
        if self.arrow:
            out["arrow"] = True
        if self.receiver:
            out["receiver"] = self.receiver
        if self.receiver_type:
            out["receiverType"] = self.receiver_type
        if self.qualified_prefix:
            out["qualifiedPrefix"] = self.qualified_prefix
        if self.head_type:
            out["headType"] = self.head_type
        if self.head_type_arg:
            out["headTypeArg"] = self.head_type_arg
        if self.local_name:
            out["localName"] = self.local_name
        if self.context:
            out["context"] = self.context
        if self.type_args is not None:
            out["typeArgs"] = self.type_args
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
    # C/C++ function-like macro -> [line, names its body calls], so a wrapper's call reaches what it wraps.
    macro_table: dict[str, list] = field(default_factory=dict)
    macros_dropped: int = 0
    parse_error_line: int | None = None
    skipped_reason: str | None = None
