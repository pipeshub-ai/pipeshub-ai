"""Tree-sitter walker: source bytes -> the spans that tile a file.

One generic recursive walk driven by ``LanguageConfig``. Every symbol it emits
comes out of the tiling pass, which is what makes the byte-exactness guarantee
structural rather than incidental: a definition found off the tiling path would
overlap the span that already covers it and double-count those bytes.
"""
from __future__ import annotations

import bisect
import importlib
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parsers.code_parser.lang_config import (
    COMMENT_NODE_TYPES,
    LANGUAGES,
    LanguageConfig,
)
from app.modules.parsers.code_parser.models import ParsedFile, ParsedSymbol

if TYPE_CHECKING:
    from tree_sitter import Node, Parser

__all__ = ["MAX_FILE_SIZE_BYTES", "decode_source", "parse_code"]

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024

_PARSER_CACHE: dict[str, Any] = {}
_PARSER_LOCK = threading.Lock()

# Node types that can stand as a bare name once a declarator chain is unwrapped.
_NAME_NODE_TYPES = frozenset({
    "identifier", "type_identifier", "field_identifier", "property_identifier",
    "simple_identifier", "name", "constant",
})

# Checked in order, so `method_types` wins over `function_types` for a grammar
# that lists a node in both.
_KIND_SOURCES = (
    ("class", "class_types"),
    ("interface", "interface_types"),
    ("enum", "enum_types"),
    ("struct", "struct_types"),
    ("trait", "trait_types"),
    ("impl", "impl_types"),
    ("module", "module_types"),
    ("type_alias", "type_alias_types"),
    ("method", "method_types"),
)


def _get_parser(cfg: LanguageConfig) -> Parser:
    """Parsers are cheap to reuse and not thread-safe to share mid-parse.

    tree_sitter.Parser holds mutable state during parse(), so a module-level
    cache handed to concurrent indexing tasks would interleave. Build under a
    lock and hand out a fresh Parser bound to the cached Language.
    """
    from tree_sitter import Language, Parser

    with _PARSER_LOCK:
        language = _PARSER_CACHE.get(cfg.name)
        if language is None:
            module = importlib.import_module(cfg.ts_module)
            language = Language(getattr(module, cfg.ts_language_fn)())
            _PARSER_CACHE[cfg.name] = language
    return Parser(language)


def decode_source(raw: bytes) -> bytes:
    """Normalise to valid UTF-8 bytes; tree-sitter indexes by byte offset."""
    try:
        raw.decode("utf-8")
        return raw
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace").encode("utf-8")


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _field(node: Node, name: str) -> Node | None:
    try:
        return node.child_by_field_name(name)
    except Exception:
        return None


def _name_via_declarator(node: Node, src: bytes) -> str | None:
    """Follow the ``declarator`` chain grammars use to bury a name.

    C and C++ wrap a function's name in pointer, array and function declarators;
    a Java or Groovy field hides it one level down in a ``variable_declarator``.
    """
    cur = _field(node, "declarator")
    for _ in range(8):
        if cur is None:
            return None
        if cur.type in _NAME_NODE_TYPES:
            return _text(cur, src)
        if cur.type == "qualified_identifier":
            return _text(cur, src).rsplit("::", 1)[-1]
        named = _field(cur, "name")
        if named is not None and named.type in _NAME_NODE_TYPES:
            return _text(named, src)
        cur = _field(cur, "declarator")
    return None


def _name_of(node: Node, src: bytes, cfg: LanguageConfig) -> str | None:
    named = _field(node, cfg.name_field_overrides.get(node.type, cfg.name_field))
    if named is not None:
        return _text(named, src)
    if cfg.unwrap_declarator:
        name = _name_via_declarator(node, src)
        if name:
            return name
    for child in node.named_children:
        if child.type in cfg.name_fallback_child_types:
            return _text(child, src)
    # One level down, for grammars that wrap a signature in another node: Dart's
    # `method_signature` holds a `function_signature` that carries the name.
    for child in node.named_children:
        inner = _field(child, cfg.name_field)
        if inner is not None:
            return _text(inner, src)
    return None


def _body_of(node: Node, cfg: LanguageConfig) -> Node | None:
    body = _field(node, cfg.body_field)
    if body is not None:
        return body
    for child in node.named_children:
        if child.type in cfg.body_fallback_child_types:
            return child
    return None


# --------------------------------------------------------------------------
# Spans
# --------------------------------------------------------------------------

@dataclass
class _Span:
    """One contiguous slice of a scope.

    Spans tile their scope exactly, so every byte of a file ends up in one -- a
    definition, an import run, a comment, or plain statements.
    """

    start: int
    end: int
    kind: str
    nodes: list = field(default_factory=list)
    def_node: Any = None
    # Set when the node type alone does not determine the kind (a class-body
    # assignment is a field, but its node type is just `assignment`).
    def_kind: str | None = None
    name: str | None = None
    decorators: tuple[str, ...] = ()


# Filler kinds that absorb an adjacent run of the same kind.
_COALESCING = frozenset({"imports", "statements", "comment"})

_COMMENT_PREFIXES = ("#", "//", "/*", "*", "--")

# A comment run separated from what follows by more than this many blank lines
# is documenting nothing, so it becomes its own block instead of attaching.
_COMMENT_ATTACH_MAX_BLANK_LINES = 1


def _classify_gap(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "statements"
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if lines and all(ln.startswith(_COMMENT_PREFIXES) for ln in lines):
        return "comment"
    return "statements"


class _Walker:
    def __init__(self, src: bytes, cfg: LanguageConfig) -> None:
        self.src = src
        self.cfg = cfg
        self.out = ParsedFile(language=cfg.name)
        # Line lookup is O(offset) if done by slicing, and it runs once per
        # span, so precompute the newline offsets and binary-search instead.
        self._line_starts = [0]
        start = src.find(b"\n")
        while start != -1:
            self._line_starts.append(start + 1)
            start = src.find(b"\n", start + 1)

    # -- symbols --------------------------------------------------------

    def _add_symbol(self, symbol: ParsedSymbol) -> int:
        self.out.symbols.append(symbol)
        return len(self.out.symbols) - 1

    def _kind_for(self, node_type: str, *, in_type: bool) -> str | None:
        cfg = self.cfg
        for kind, attr in _KIND_SOURCES:
            if node_type in getattr(cfg, attr):
                return kind
        if node_type in cfg.function_types:
            return "method" if in_type else "function"
        return None

    # -- entry ----------------------------------------------------------

    def walk(self, root: Node) -> ParsedFile:
        self._scope(root, 0, len(self.src), (), None, container_kind=None)
        return self.out

    # -- scope ----------------------------------------------------------

    def _scope(self, members_parent: Node | None, scope_start: int, scope_end: int,
               chain: tuple[str, ...], parent_idx: int | None, *,
               container_kind: str | None) -> None:
        """Emit the spans that tile ``[scope_start, scope_end)``."""
        is_container = container_kind is not None
        # Only a type promotes its functions to methods. A function inside a C++
        # namespace, a Rust `mod` or a C# `namespace` is still a function.
        in_type = container_kind in self.cfg.method_container_kinds
        spans = self._collect(members_parent, in_container=is_container, in_type=in_type)
        spans = self._tile(spans, scope_start, scope_end, is_container=is_container)
        for span in spans:
            self._emit(span, chain, parent_idx, in_type=in_type)

    def _collect(self, members_parent: Node | None, *,
                 in_container: bool, in_type: bool) -> list[_Span]:
        """Turn a scope's named children into spans, attaching comments forward."""
        cfg = self.cfg
        spans: list[_Span] = []
        attached: list = []

        def flush_unattached() -> None:
            nonlocal attached
            for node in attached:
                # A trailing inline comment belongs to the preceding code, not
                # to what follows. Absorb it when on the same line.
                if (
                    spans
                    and self._line_at(node.start_byte)
                    == self._line_at(max(0, spans[-1].end - 1))
                ):
                    spans[-1].end = node.end_byte
                    spans[-1].nodes.append(node)
                else:
                    spans.append(
                        _Span(node.start_byte, node.end_byte, "comment", nodes=[node])
                    )
            attached = []

        seen_member = False
        for child in (members_parent.named_children if members_parent is not None else []):
            if child.type in cfg.attached_types:
                attached.append(child)
                continue

            # A definition whose body is a sibling rather than a child claims it
            # here, before the body can become a statements span of its own.
            if (
                child.type in cfg.trailing_body_types
                and spans
                and spans[-1].kind == "definition"
                and spans[-1].end <= child.start_byte
            ):
                spans[-1].end = child.end_byte
                spans[-1].nodes.append(child)
                attached = []
                continue

            # A container's leading docstring is part of its header, not a span
            # of its own -- leaving it out here lets the header gap absorb it.
            if in_container and not seen_member and self._is_docstring(child):
                continue
            seen_member = True

            span = self._classify(child, in_container=in_container, in_type=in_type)
            if span is None:
                continue

            if attached:
                if self._blank_lines_between(attached[-1].end_byte, span.start) <= _COMMENT_ATTACH_MAX_BLANK_LINES:
                    # Comments, decorators and attributes belong to what they
                    # document.
                    span.start = attached[0].start_byte
                    span.decorators = span.decorators + tuple(
                        _text(n, self.src) for n in attached
                        if n.type not in COMMENT_NODE_TYPES
                    )
                    attached = []
                else:
                    flush_unattached()
            spans.append(span)

        flush_unattached()
        return self._coalesce(spans)

    def _classify(self, child: Node, *, in_container: bool, in_type: bool) -> _Span | None:
        cfg = self.cfg
        ntype = child.type

        if ntype in cfg.decorator_wrapper_types:
            inner = next(
                (c for c in child.named_children
                 if c.type not in cfg.attached_types and self._kind_for(c.type, in_type=in_type)),
                None,
            )
            if inner is None:
                return _Span(child.start_byte, child.end_byte, "statements", nodes=[child])
            decorators = tuple(
                _text(c, self.src) for c in child.named_children
                if c.type in cfg.attached_types and c.type not in COMMENT_NODE_TYPES
            )
            # The wrapper's range is used, so the decorator source stays in the
            # block instead of being discarded with the wrapper.
            return _Span(
                child.start_byte, child.end_byte, "definition", nodes=[child],
                def_node=inner, name=_name_of(inner, self.src, cfg), decorators=decorators,
            )

        if ntype in cfg.export_types:
            if _field(child, "source") is not None:
                return _Span(child.start_byte, child.end_byte, "imports", nodes=[child])
            inner = next(
                (c for c in child.named_children if self._kind_for(c.type, in_type=in_type)), None
            )
            if inner is not None:
                return _Span(
                    child.start_byte, child.end_byte, "definition", nodes=[child],
                    def_node=inner, name=_name_of(inner, self.src, cfg),
                )
            for candidate in child.named_children:
                bound = self._as_bound_function(candidate)
                if bound is not None:
                    bound.start, bound.end, bound.nodes = child.start_byte, child.end_byte, [child]
                    return bound
            return _Span(child.start_byte, child.end_byte, "statements", nodes=[child])

        if ntype in cfg.import_types:
            return _Span(child.start_byte, child.end_byte, "imports", nodes=[child])

        if self._kind_for(ntype, in_type=in_type):
            end = child.end_byte
            # C/C++: a struct/enum at file scope is a bare specifier whose
            # declaration-terminating `;` is an unnamed sibling.
            if cfg.unwrap_declarator:
                nxt = child.next_sibling
                if nxt is not None and not nxt.is_named and nxt.type == ";":
                    end = nxt.end_byte
            return _Span(
                child.start_byte, end, "definition", nodes=[child],
                def_node=child, name=_name_of(child, self.src, cfg),
            )

        bound = self._as_bound_function(child)
        if bound is not None:
            return bound

        if in_container:
            field_span = self._as_field(child)
            if field_span is not None:
                return field_span

        return _Span(child.start_byte, child.end_byte, "statements", nodes=[child])

    def _as_bound_function(self, child: Node) -> _Span | None:
        """``const handler = () => {}`` is a definition in everything but node type.

        Without this a module of arrow functions is one undifferentiated
        statements block, which is most of a modern TS codebase.
        """
        cfg = self.cfg
        if child.type not in cfg.binding_types:
            return None
        declarators = [c for c in child.named_children if _field(c, "value") is not None]
        if len(declarators) != 1:
            return None
        value = _field(declarators[0], "value")
        if value.type not in cfg.function_value_types:
            return None
        name_node = _field(declarators[0], "name")
        name = _text(name_node, self.src) if name_node is not None else None
        if not name or not name.isidentifier():
            return None
        return _Span(
            child.start_byte, child.end_byte, "definition", nodes=[child],
            def_node=value, def_kind="function", name=name,
        )

    def _as_field(self, child: Node) -> _Span | None:
        """A class-body assignment is a field, and needs to stay addressable.

        Folding it into a statements span would leave a class of constants with
        nothing retrievable below the class itself.
        """
        cfg = self.cfg
        node = child
        if child.type == "expression_statement":
            inner = child.named_children
            if len(inner) != 1:
                return None
            node = inner[0]
        if node.type not in cfg.field_types:
            return None

        name_node = _field(node, "left") or _field(node, "name")
        if name_node is not None:
            name = _text(name_node, self.src).split(".")[-1]
        else:
            name = _name_of(node, self.src, cfg)
        if not name or not name.isidentifier():
            return None
        return _Span(
            child.start_byte, child.end_byte, "definition", nodes=[child],
            def_node=node, def_kind="field", name=name,
        )

    def _is_docstring(self, child: Node) -> bool:
        if child.type != "expression_statement":
            return False
        inner = child.named_children
        return len(inner) == 1 and inner[0].type == "string"

    @staticmethod
    def _coalesce(spans: list[_Span]) -> list[_Span]:
        out: list[_Span] = []
        for span in spans:
            if out and span.kind == out[-1].kind and span.kind in _COALESCING:
                out[-1].end = span.end
                out[-1].nodes.extend(span.nodes)
                continue
            out.append(span)
        return out

    def _blank_lines_between(self, end: int, start: int) -> int:
        if start <= end:
            return 0
        return self.src[end:start].decode("utf-8", errors="replace").count("\n") - 1

    def _tile(self, spans: list[_Span], scope_start: int, scope_end: int,
              *, is_container: bool) -> list[_Span]:
        """Make the spans cover the scope exactly, with no gaps or overlaps."""
        spans = [s for s in spans if s.end > s.start]
        spans.sort(key=lambda s: s.start)

        tiled: list[_Span] = []
        cursor = scope_start
        for span in spans:
            if span.start > cursor:
                gap = self.src[cursor:span.start]
                # UTF-8 BOM is invisible and tree-sitter skips it; treat as ws.
                if cursor == 0:
                    gap = gap.lstrip(b"\xef\xbb\xbf")
                if gap.strip():
                    # A container's leading gap is its decorators, signature and
                    # docstring -- the one part of a class that no member covers.
                    kind = "header" if (is_container and not tiled) else _classify_gap(
                        self.src[cursor:span.start].decode("utf-8", errors="replace")
                    )
                    tiled.append(_Span(cursor, span.start, kind))
            if span.start < cursor:
                span.start = cursor
                if span.end <= span.start:
                    continue
            tiled.append(span)
            cursor = span.end

        if cursor < scope_end and self.src[cursor:scope_end].strip():
            kind = "header" if (is_container and not tiled) else _classify_gap(
                self.src[cursor:scope_end].decode("utf-8", errors="replace")
            )
            tiled.append(_Span(cursor, scope_end, kind))

        if not tiled:
            if scope_end > scope_start:
                tiled.append(_Span(scope_start, scope_end,
                                   "header" if is_container else "statements"))
            return tiled

        # Whitespace attaches backward: each span runs to the start of the next,
        # so blank lines never become blocks of their own.
        for i in range(len(tiled) - 1):
            tiled[i].end = tiled[i + 1].start
        tiled[0].start = scope_start
        tiled[-1].end = scope_end
        return tiled

    # -- emit -----------------------------------------------------------

    def _emit(self, span: _Span, chain: tuple[str, ...], parent_idx: int | None,
              *, in_type: bool) -> None:
        cfg = self.cfg
        kind = span.kind
        if kind == "definition":
            kind = span.def_kind or self._kind_for(span.def_node.type, in_type=in_type) or "statements"

        idx = self._add_symbol(ParsedSymbol(
            kind=kind,
            name=span.name,
            start_line=self._line_at(span.start),
            end_line=self._line_at(max(span.start, span.end - 1)),
            parent_chain=chain,
            text=self.src[span.start:span.end].decode("utf-8", errors="replace"),
            decorators=span.decorators,
            parent=parent_idx,
        ))

        if span.kind != "definition" or kind not in cfg.container_kinds:
            return

        body = _body_of(span.def_node, cfg)
        if not self._has_members(body):
            return

        self.out.symbols[idx].is_container = True
        child_chain = (*chain, span.name) if span.name else chain
        self._scope(body, span.start, span.end, child_chain, idx, container_kind=kind)

    def _has_members(self, body: Node | None) -> bool:
        """A container is only a group when something inside it is a definition.

        Field detection has to go through ``_as_field``: a Python class attribute
        is an ``assignment`` wrapped in an ``expression_statement``, so matching
        the child's own type would miss every attribute-only class.
        """
        if body is None:
            return False
        for child in body.named_children:
            if self._kind_for(child.type, in_type=True) or self._as_field(child) is not None:
                return True
        return False

    def _line_at(self, offset: int) -> int:
        return bisect.bisect_right(self._line_starts, offset)


def parse_code(source: bytes, language: str) -> ParsedFile:
    """Parse *source* for *language* into spans that tile it exactly."""
    cfg = LANGUAGES.get(language)
    if cfg is None:
        return ParsedFile(language=language or "unknown")
    if len(source) > MAX_FILE_SIZE_BYTES:
        return ParsedFile(language=cfg.name, skipped_reason="oversized")

    src = decode_source(source)
    parser = _get_parser(cfg)
    tree = parser.parse(src)

    parsed = _Walker(src, cfg).walk(tree.root_node)

    # A partially-broken file still yields usable definitions above the break;
    # record where parsing degraded so callers can judge the result.
    if tree.root_node.has_error:
        parsed.parse_error_line = _first_error_line(tree.root_node)
    return parsed


def _first_error_line(root: Node) -> int | None:
    """Walk *all* children, not just named ones.

    A syntax error frequently surfaces as an unnamed missing token (a dropped
    closing paren), which `named_children` does not expose.
    """
    best: int | None = None
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            line = node.start_point[0] + 1
            best = line if best is None else min(best, line)
            continue
        if node.has_error:
            stack.extend(node.children)
    return best
