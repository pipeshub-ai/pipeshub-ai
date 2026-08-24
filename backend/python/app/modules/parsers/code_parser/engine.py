"""Tree-sitter walker: source bytes -> the spans that tile a file, and the
deferred edge facts found inside them.

One generic recursive walk driven by ``LanguageConfig``, with small per-language
hooks where grammars genuinely diverge (imports, class heritage, type
declarations). Every symbol it emits
comes out of the tiling pass, which is what makes the byte-exactness guarantee
structural rather than incidental: a definition found off the tiling path would
overlap the span that already covers it and double-count those bytes.

The walk emits *names*, never IDs. ID minting needs the file's repo-relative
path, which this layer does not have; ``code_file_parser`` does it.
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
from app.modules.parsers.code_parser.models import (
    ParsedFile,
    ParsedSymbol,
    PendingEdgeFact,
    Relation,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Parser

__all__ = ["MAX_FILE_SIZE_BYTES", "decode_source", "parse_code"]

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024

# Names that would otherwise bind a call to a user symbol of the same name.
_BUILTIN_GLOBALS: dict[str, frozenset[str]] = {
    "python": frozenset({
        "print", "len", "range", "str", "int", "float", "bool", "list", "dict", "set",
        "tuple", "type", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
        "super", "open", "sorted", "sum", "min", "max", "abs", "any", "all", "zip",
        "map", "filter", "enumerate", "repr", "format", "hash", "id", "iter", "next",
        "bytes", "bytearray", "frozenset", "object", "property", "staticmethod",
        "classmethod", "callable", "vars", "dir", "round", "divmod", "pow", "input",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "RuntimeError",
    }),
    "javascript": frozenset({
        "require", "console", "parseInt", "parseFloat", "isNaN", "isFinite", "String",
        "Number", "Boolean", "Array", "Object", "JSON", "Math", "Date", "RegExp",
        "Promise", "Symbol", "Map", "Set", "WeakMap", "WeakSet", "Error", "TypeError",
        "encodeURIComponent", "decodeURIComponent", "setTimeout", "setInterval",
        "clearTimeout", "clearInterval", "fetch", "alert", "structuredClone", "BigInt",
    }),
}
_BUILTIN_GLOBALS["typescript"] = _BUILTIN_GLOBALS["javascript"]
_BUILTIN_GLOBALS["tsx"] = _BUILTIN_GLOBALS["javascript"]

# Methods of built-in containers/strings/promises. A member call on one of these
# can never resolve -- resolution needs a known receiver type, and these
# receivers are language primitives, not repo symbols -- so recording the fact
# would only bloat a traversed node.
_BUILTIN_METHODS: dict[str, frozenset[str]] = {
    "python": frozenset({
        "get", "items", "keys", "values", "setdefault", "popitem", "update",
        "append", "extend", "insert", "remove", "pop", "clear", "copy", "sort",
        "reverse", "index", "count", "add", "discard", "union", "intersection",
        "lower", "upper", "title", "strip", "lstrip", "rstrip", "split",
        "rsplit", "splitlines", "join", "replace", "startswith", "endswith",
        "format", "encode", "decode", "isdigit", "isalpha", "isnumeric",
        "read", "readline", "readlines", "write", "close", "seek", "flush",
        "group", "groups", "groupdict", "span",
    }),
    "javascript": frozenset({
        "push", "pop", "shift", "unshift", "slice", "splice", "concat", "join",
        "map", "filter", "reduce", "forEach", "find", "findIndex", "some",
        "every", "includes", "indexOf", "lastIndexOf", "sort", "reverse", "flat",
        "then", "catch", "finally", "toString", "valueOf", "trim", "split",
        "replace", "replaceAll", "match", "test", "toLowerCase", "toUpperCase",
        "padStart", "padEnd", "startsWith", "endsWith", "charAt", "substring",
        "keys", "values", "entries", "has", "set", "get", "delete", "add",
        "json", "text", "bind", "call", "apply",
    }),
}
_BUILTIN_METHODS["typescript"] = _BUILTIN_METHODS["javascript"]
_BUILTIN_METHODS["tsx"] = _BUILTIN_METHODS["javascript"]

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

# Kinds whose body is statements rather than members. Nothing tiles them: they
# stay a single block whose body is its text, with any definition inside hung off
# them as a block of its own.
_CALLABLE_KINDS = frozenset({"function", "method"})


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
# Import handlers
# --------------------------------------------------------------------------

def _imports_python(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    line = node.start_point[0] + 1
    if node.type == "import_statement":
        for child in node.named_children:
            if child.type == "aliased_import":
                target = _field(child, "name")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.IMPORTS_FROM, to_name=_text(target, src),
                        line=line, from_kind="record", to_kind="record",
                    ))
            elif child.type == "dotted_name":
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS_FROM, to_name=_text(child, src),
                    line=line, from_kind="record", to_kind="record",
                ))
        return

    if node.type in ("import_from_statement", "future_import_statement"):
        module = _field(node, "module_name")
        module_name = _text(module, src) if module is not None else ""
        if module_name:
            out.append(PendingEdgeFact(
                relation=Relation.IMPORTS_FROM, to_name=module_name,
                line=line, from_kind="record", to_kind="record",
            ))
        for child in node.named_children:
            if child is module:
                continue
            if child.type == "aliased_import":
                target = _field(child, "name")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.IMPORTS, to_name=_text(target, src), line=line,
                        from_kind="record", to_kind="block", qualified_prefix=module_name,
                    ))
            elif child.type in ("dotted_name", "identifier"):
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS, to_name=_text(child, src), line=line,
                    from_kind="record", to_kind="block", qualified_prefix=module_name,
                ))


def _imports_js(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    line = node.start_point[0] + 1
    source = _field(node, "source")
    specifier = _text(source, src).strip("'\"") if source is not None else ""
    if not specifier:
        return
    out.append(PendingEdgeFact(
        relation=Relation.IMPORTS_FROM, to_name=specifier,
        line=line, from_kind="record", to_kind="record",
    ))
    for clause in node.named_children:
        if clause.type not in ("import_clause", "named_imports"):
            continue
        for spec in clause.named_children:
            if spec.type == "identifier":
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS, to_name=_text(spec, src), line=line,
                    from_kind="record", to_kind="block", qualified_prefix=specifier,
                ))
            elif spec.type == "named_imports":
                for item in spec.named_children:
                    target = _field(item, "name")
                    if target is not None:
                        out.append(PendingEdgeFact(
                            relation=Relation.IMPORTS, to_name=_text(target, src), line=line,
                            from_kind="record", to_kind="block", qualified_prefix=specifier,
                        ))


def _exports_js(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`export … from './m'` re-exports; `export function f` exports a local symbol."""
    line = node.start_point[0] + 1
    source = _field(node, "source")
    if source is not None:
        specifier = _text(source, src).strip("'\"")
        if specifier:
            out.append(PendingEdgeFact(
                relation=Relation.RE_EXPORTS, to_name=specifier,
                line=line, from_kind="record", to_kind="record",
            ))
        return
    for child in node.named_children:
        if child.type == "export_clause":
            for spec in child.named_children:
                target = _field(spec, "name")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.EXPORTS, to_name=_text(target, src), line=line,
                        from_kind="record", to_kind="block", restrict_to_file=True,
                    ))


# --------------------------------------------------------------------------
# Class heritage handlers
# --------------------------------------------------------------------------

def _heritage_python(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    supers = _field(node, "superclasses")
    if supers is None:
        return
    line = node.start_point[0] + 1
    for child in supers.named_children:
        if child.type in ("identifier", "attribute"):
            raw = _text(child, src)
            name = raw.rsplit(".", 1)[-1]
            prefix = raw.rsplit(".", 1)[0] if "." in raw else None
            out.append(PendingEdgeFact(
                relation=Relation.INHERITS, to_name=name, line=line,
                from_symbol=idx, qualified_prefix=prefix,
            ))


def _heritage_js(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type != "class_heritage":
            continue
        for item in child.named_children:
            if item.type in ("identifier", "member_expression", "type_identifier"):
                raw = _text(item, src)
                out.append(PendingEdgeFact(
                    relation=Relation.EXTENDS, to_name=raw.rsplit(".", 1)[-1],
                    line=line, from_symbol=idx,
                ))


def _heritage_ts(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type == "extends_type_clause":
            for item in child.named_children:
                if item.type in ("type_identifier", "generic_type", "identifier"):
                    out.append(PendingEdgeFact(
                        relation=Relation.EXTENDS,
                        to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                        line=line, from_symbol=idx,
                    ))
        if child.type != "class_heritage":
            continue
        for clause in child.named_children:
            if clause.type == "extends_clause":
                for item in clause.named_children:
                    if item.type in ("identifier", "member_expression", "generic_type", "type_identifier"):
                        out.append(PendingEdgeFact(
                            relation=Relation.EXTENDS,
                            to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                            line=line, from_symbol=idx,
                        ))
            elif clause.type == "implements_clause":
                for item in clause.named_children:
                    if item.type in ("type_identifier", "generic_type", "identifier"):
                        out.append(PendingEdgeFact(
                            relation=Relation.IMPLEMENTS,
                            to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                            line=line, from_symbol=idx,
                        ))


_IMPORT_HANDLERS = {"python": _imports_python, "js": _imports_js}
_HERITAGE_HANDLERS = {"python": _heritage_python, "js": _heritage_js, "ts": _heritage_ts}


# --------------------------------------------------------------------------
# Type tables
# --------------------------------------------------------------------------

def _collect_type_table_ts(root: Node, src: bytes, table: dict[str, str]) -> None:
    """Harvest `name: Type` bindings from declarations and constructor params."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("variable_declarator", "required_parameter", "optional_parameter"):
            name_node = _field(node, "name") or next(
                (c for c in node.named_children if c.type == "identifier"), None
            )
            ann = next((c for c in node.named_children if c.type == "type_annotation"), None)
            if name_node is not None and ann is not None:
                type_node = next(
                    (c for c in ann.named_children if c.type in ("type_identifier", "generic_type")), None
                )
                if type_node is not None:
                    table.setdefault(
                        _text(name_node, src), _text(type_node, src).split("<")[0]
                    )
        stack.extend(node.named_children)


def _collect_type_table_js(root: Node, src: bytes, table: dict[str, str]) -> None:
    """JS has no annotations; `const x = new Foo()` is the only reliable binding."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "variable_declarator":
            name_node = _field(node, "name")
            value = _field(node, "value")
            if name_node is not None and value is not None and value.type == "new_expression":
                ctor = _field(value, "constructor")
                if ctor is not None:
                    table.setdefault(_text(name_node, src), _text(ctor, src).rsplit(".", 1)[-1])
        stack.extend(node.named_children)


def _collect_type_table_python(root: Node, src: bytes, table: dict[str, str]) -> None:
    """`self.x = Foo()` and `x: Foo = …` are the two usable bindings."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "assignment":
            left = _field(node, "left")
            right = _field(node, "right")
            ann = _field(node, "type")
            if left is not None and ann is not None:
                table.setdefault(_text(left, src).split(".")[-1], _text(ann, src).split("[")[0])
            elif left is not None and right is not None and right.type == "call":
                fn = _field(right, "function")
                if fn is not None and fn.type in ("identifier", "attribute"):
                    called = _text(fn, src).rsplit(".", 1)[-1]
                    if called[:1].isupper():
                        table.setdefault(_text(left, src).split(".")[-1], called)
        stack.extend(node.named_children)


_TYPE_TABLE_HANDLERS = {
    "python": _collect_type_table_python,
    "js": _collect_type_table_js,
    "ts": _collect_type_table_ts,
}


# --------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------

def _callee_info(
    node: Node, src: bytes, cfg: LanguageConfig,
) -> tuple[str | None, bool, str | None, str | None]:
    """Return (name, is_member, receiver, qualified_prefix) for a call node."""
    target = _field(node, cfg.call_function_field) or _field(node, "constructor")
    if target is None:
        return None, False, None, None

    if target.type in ("identifier", "type_identifier"):
        return _text(target, src), False, None, None

    if target.type in cfg.accessor_types:
        prop = _field(target, cfg.accessor_property_field)
        obj = _field(target, cfg.accessor_object_field)
        if prop is None:
            return None, False, None, None
        name = _text(prop, src)
        receiver_text = _text(obj, src) if obj is not None else None
        # os.path.join(...) -- the receiver chain is the qualifier, not a variable
        if receiver_text and ("." in receiver_text):
            return name, True, receiver_text.rsplit(".", 1)[-1], receiver_text
        return name, True, receiver_text, receiver_text
    return None, False, None, None

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
    is_exported: bool = False


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
        self.builtins = _BUILTIN_GLOBALS.get(cfg.name, frozenset())
        self.import_handler = _IMPORT_HANDLERS.get(cfg.import_handler_name)
        self.heritage_handler = _HERITAGE_HANDLERS.get(cfg.heritage_handler_name)

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
        # Built before the walk so a call can resolve its receiver's type as it
        # is emitted: a variable is often declared below its first use.
        handler = _TYPE_TABLE_HANDLERS.get(self.cfg.type_table_handler_name)
        if handler:
            handler(root, self.src, self.out.type_table)
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
                    is_exported=True,
                )
            for candidate in child.named_children:
                bound = self._as_bound_function(candidate)
                if bound is not None:
                    bound.start, bound.end, bound.nodes = child.start_byte, child.end_byte, [child]
                    bound.is_exported = True
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
            start_byte=span.start,
            end_byte=span.end,
            parent_chain=chain,
            text=self.src[span.start:span.end].decode("utf-8", errors="replace"),
            decorators=span.decorators,
            is_exported=span.is_exported,
            parent=parent_idx,
        ))

        if span.kind != "definition":
            for node in span.nodes:
                if node.type in cfg.import_types or node.type in cfg.export_types:
                    self._run_handler(node, idx)
                else:
                    self._walk_facts(node, idx)
            return

        if self.heritage_handler and kind in cfg.method_container_kinds:
            before = len(self.out.pending)
            self.heritage_handler(span.def_node, self.src, self.out.pending, idx)
            for fact in self.out.pending[before:]:
                fact.byte_offset = span.start
                fact.from_symbol = idx
                fact.pinned = True

        body = _body_of(span.def_node, cfg)
        child_chain = (*chain, span.name) if span.name else chain

        # Only a top-level type is a group, and it is one whether or not it has
        # members, so an empty class still owns its header. Nested inside anything
        # it is a block like a method: one span, with its own definitions hung off
        # it rather than tiling it.
        if kind in cfg.container_kinds and parent_idx is None:
            self.out.symbols[idx].is_container = True
            self._scope(body, span.start, span.end, child_chain, idx, container_kind=kind)
            return

        # Everything else stays one block -- its body is its text -- and the
        # definitions inside it become blocks parented to it. Their bytes appear
        # twice by design: `outer` is still retrievable whole, `outer.inner` alone.
        if body is not None and (kind in _CALLABLE_KINDS or kind in cfg.container_kinds):
            self._emit_nested(body, idx, child_chain,
                              in_type=kind in cfg.method_container_kinds)
            return

        self._walk_facts(span.def_node, idx)

    def _emit_nested(self, body: Node, enclosing: int, chain: tuple[str, ...],
                     *, in_type: bool) -> None:
        """Emit definitions nested in an untiled body; collect facts for the rest.

        ``in_type`` says whether *body* belongs to a type, which decides both
        whether a function in it is a method and whether an assignment in it is a
        field -- a nested class keeps those, a callable's body has neither.

        Only direct children are considered. A function buried inside an
        expression -- a callback handed to `map` -- has no name worth addressing,
        and hoisting it would attribute its calls away from the statement that
        actually runs them.
        """
        cfg = self.cfg
        for child in body.named_children:
            span = self._classify(child, in_container=in_type, in_type=in_type)
            if span is not None and span.kind == "definition":
                self._emit(span, chain, enclosing, in_type=in_type)
                continue
            if child.type in cfg.import_types or child.type in cfg.export_types:
                self._run_handler(child, enclosing)
            else:
                self._walk_facts(child, enclosing)

    def _run_handler(self, node, idx: int) -> None:
        cfg = self.cfg
        before = len(self.out.pending)
        if node.type in cfg.export_types:
            _exports_js(node, self.src, self.out.pending)
        elif self.import_handler:
            self.import_handler(node, self.src, self.out.pending)
        for fact in self.out.pending[before:]:
            fact.byte_offset = node.start_byte
            fact.from_symbol = idx

    def _walk_facts(self, node: Node, enclosing: int) -> None:
        """Collect references inside *node*, all owned by *enclosing*.

        Nested definitions are not emitted here. Anything tiling did not reach
        sits inside a span that already covers its bytes, so emitting it would
        double-count them; its references belong to the block that holds it.
        """
        cfg = self.cfg
        for child in node.named_children:
            ntype = child.type
            if ntype in cfg.import_types or ntype in cfg.export_types:
                self._run_handler(child, enclosing)
                continue
            if ntype in cfg.call_types:
                self._emit_call(child, enclosing)
            self._walk_facts(child, enclosing)

    def _emit_call(self, node, enclosing: int) -> None:
        name, is_member, receiver, qualifier = _callee_info(node, self.src, self.cfg)
        if not name or name in self.builtins:
            return
        self.out.pending.append(PendingEdgeFact(
            relation=Relation.CALLS,
            to_name=name,
            line=self._line_at(node.start_byte),
            byte_offset=node.start_byte,
            from_symbol=enclosing,
            to_kind="block",
            is_member_call=is_member,
            receiver=receiver,
            receiver_type=self.out.type_table.get(receiver) if receiver else None,
            qualified_prefix=qualifier,
        ))

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

    # A member call on a built-in container can never resolve -- resolution
    # needs a known receiver type and these receivers are language primitives
    # -- so keeping the fact would only bloat a traversed node.
    builtin_methods = _BUILTIN_METHODS.get(cfg.name, frozenset())
    parsed.pending = [
        fact for fact in parsed.pending
        if not (
            fact.is_member_call
            and not fact.receiver_type
            and fact.receiver not in ("self", "this")
            and fact.to_name in builtin_methods
        )
    ]
    _attribute_facts(parsed)
    return parsed


def _attribute_facts(parsed: ParsedFile) -> None:
    """Give each reference to the innermost span that contains it.

    Without this a module-level call has no owning symbol and its edge falls back
    to the file record, so `analysis.analyze_game(...)` at the top level of a
    script would be sourced from the file rather than from the statement holding
    it.
    """
    ranked = sorted(
        range(len(parsed.symbols)),
        key=lambda i: parsed.symbols[i].end_byte - parsed.symbols[i].start_byte,
    )
    for fact in parsed.pending:
        if fact.pinned:
            continue
        for i in ranked:
            sym = parsed.symbols[i]
            if sym.start_byte <= fact.byte_offset < sym.end_byte:
                fact.from_symbol = i
                fact.from_kind = "block"
                break


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
