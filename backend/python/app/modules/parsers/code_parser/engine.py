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
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parsers.code_parser import languages
from app.modules.parsers.code_parser.lang_config import (
    COMMENT_NODE_TYPES,
    LANGUAGES,
    LanguageConfig,
)
from app.modules.parsers.code_parser.models import (
    DECLARATION,
    FIXTURE_MARK,
    STAR_EXPORTS_MARK,
    ParsedFile,
    ParsedSymbol,
    PendingEdgeFact,
    Relation,
)
from app.modules.parsers.text_decoding import decode_text
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tree_sitter import Node, Parser

__all__ = ["MAX_FILE_SIZE_BYTES", "decode_source", "parse_code"]

DEFAULT_MAX_FILE_SIZE_MB = 5

# One limit for everything read as code or as plain text out of a repository:
# the tree-sitter parse here and the text fallback for files with no grammar.
MAX_FILE_SIZE_BYTES = max(1, env_int("CODE_FILE_MAX_SIZE_MB", DEFAULT_MAX_FILE_SIZE_MB)) * 1024 * 1024

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
_BUILTIN_GLOBALS["go"] = frozenset({
    "append", "cap", "clear", "close", "complex", "copy", "delete", "imag", "len", "make",
    "max", "min", "new", "panic", "print", "println", "real", "recover",
})
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
    # C++ names that are no identifier: an in-class `~Executor()` or `operator==` had no name, so no symbol.
    "destructor_name", "operator_name",
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
        return decode_text(raw).encode("utf-8")


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _field(node: Node, name: str) -> Node | None:
    try:
        return node.child_by_field_name(name)
    except Exception:
        return None


# The fields of a transparent node that are its condition, not its members: `#ifdef NAME`, `#if COND`, `extern "C"`.
_TRANSPARENT_NON_MEMBERS = ("name", "condition", "value")

# Declarators that hold the next one as a plain child, not in a `declarator` field: `T& f()`, `int (*f(int))(int)`.
_UNFIELDED_DECLARATORS = frozenset({"reference_declarator", "parenthesized_declarator"})


def _next_declarator(cur: Node) -> Node | None:
    inner = _field(cur, "declarator")
    if inner is None and cur.type in _UNFIELDED_DECLARATORS and cur.named_children:
        inner = cur.named_children[-1]
    return inner


_OPERATOR_SPACE = re.compile(r"\s+(?=\W)|(?<=\W)\s+")


def _name_text(node: Node, src: bytes) -> str:
    """A name node's text; `operator <` is `operator<` as a compiler index names it (`operator new` keeps its space)."""
    text = _text(node, src)
    return _OPERATOR_SPACE.sub("", text) if node.type == "operator_name" else text


def _qualified_parts(node: Node, src: bytes) -> list[str]:
    """`net::Map<K, V>::at` -> ["net", "Map", "at"]: a template scope is named without its arguments,
    which may hold `::` themselves (`Table<std::map<int, int>>::size`)."""
    parts: list[str] = []
    cur: Node | None = node
    while cur is not None and cur.type == "qualified_identifier":
        scope = _field(cur, "scope")
        if scope is not None:
            head = _field(scope, "name") if scope.type == "template_type" else scope
            parts.append(_text(head if head is not None else scope, src))
        cur = _field(cur, "name")
    if cur is not None:
        parts.append(_name_text(cur, src))
    return parts


def _name_via_declarator(node: Node, src: bytes) -> str | None:
    """Follow the ``declarator`` chain grammars use to bury a name.

    C and C++ wrap a function's name in pointer, reference, array and function
    declarators; a Java or Groovy field hides it one level down in a
    ``variable_declarator``. A reference declarator has no ``declarator`` field,
    so `const T& Foo::name()` was named by its return type, `T`.
    """
    cur = _field(node, "declarator")
    for _ in range(8):
        if cur is None:
            return None
        if cur.type in _NAME_NODE_TYPES:
            return _name_text(cur, src)
        if cur.type == "qualified_identifier":
            return _qualified_parts(cur, src)[-1]
        named = _field(cur, "name")
        if named is not None and named.type in _NAME_NODE_TYPES:
            return _name_text(named, src)
        cur = _next_declarator(cur)
    return None


# A type written with arguments, a reference, a path or `dyn`, and the field that holds what it wraps.
_TYPE_WRAPPERS = {"generic_type": "type", "reference_type": "type", "pointer_type": "type",
                  "scoped_type_identifier": "name", "dynamic_type": "trait", "abstract_type": "trait"}


def _type_head(node: Node, src: bytes) -> str:
    """The type a written type is about: `Foo<T>`, `&'a mut Foo` and `crate::m::Foo` all name `Foo`.

    A Rust impl is named by the type it implements for, the way a compiler index
    names it; the written text (`Renderer<'a>`) named its methods `Renderer<'a>.new`,
    so no edge into them matched the type's own name.
    """
    cur = node
    for _ in range(8):
        inner = _field(cur, _TYPE_WRAPPERS[cur.type]) if cur.type in _TYPE_WRAPPERS else None
        if inner is None:
            break
        cur = inner
    return _text(cur, src)


def _name_of(node: Node, src: bytes, cfg: LanguageConfig) -> str | None:
    override = cfg.name_field_overrides.get(node.type)
    named = _field(node, override or cfg.name_field)
    if named is not None and named.type == "qualified_identifier" and cfg.unwrap_declarator:
        # `struct ApiClient::TypeInterpretation<Int8> {` is TypeInterpretation, a member of ApiClient (_owner_of).
        return _qualified_parts(named, src)[-1].split("<", 1)[0].strip()
    if named is not None and named.type == "template_type":
        # A C++ explicit specialization (`struct hash<ns::Boolean>`) is the template's own class, as indexed.
        named = _field(named, "name") or named
    if named is not None:
        return _type_head(named, src) if override else _text(named, src)
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


def _owner_of(node: Node, src: bytes) -> tuple[str, ...]:
    """The type a definition belongs to when the grammar says so outside its body.

    Go puts a method's type in its receiver (`func (s *Server) Run()`); C++
    defines a member out of line (`int Resizer::run() {}`). Without it the
    qualified name was `run`, not `Server.run`, and every edge into the
    method named the wrong symbol (P-3).
    A template owner is named without its arguments (`Box<T>::get` belongs to
    `Box`), as the class itself is.
    """
    receiver = _field(node, "receiver")
    if receiver is not None:
        names = [c for c in _walk_types(receiver) if c.type == "type_identifier"]
        return (_text(names[0], src),) if names else ()
    named = _field(node, "name") if node.type.endswith("_specifier") else None
    if named is not None and named.type == "qualified_identifier":  # C++ `struct Outer::Inner<T> {`
        return tuple(part for part in _qualified_parts(named, src)[:-1] if part)
    cur = _field(node, "declarator")
    for _ in range(8):
        if cur is None:
            return ()
        if cur.type == "qualified_identifier":
            return tuple(part for part in _qualified_parts(cur, src)[:-1] if part)
        cur = _next_declarator(cur)
    return ()


def _walk_types(node: Node) -> list[Node]:
    stack, out = [node], []
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(reversed(current.named_children))
    return out


_TYPE_PARAMETER_LISTS = frozenset({"type_parameter_list", "type_parameters"})


def _type_param_count(def_node: Node | None) -> int:
    """`class Foo<T, U>` / `Foo<T>()`: 2 / 1, telling `Foo<T>` from a plain `Foo` of the same name."""
    params = next((c for c in def_node.named_children if c.type in _TYPE_PARAMETER_LISTS), None) \
        if def_node is not None else None
    return sum(1 for c in params.named_children if c.type != "comment") if params is not None else 0


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
                alias = _field(child, "alias")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.IMPORTS_FROM, to_name=_text(target, src),
                        line=line, to_kind="record",
                        local_name=_text(alias, src) if alias is not None else None,
                    ))
            elif child.type == "dotted_name":
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS_FROM, to_name=_text(child, src),
                    line=line, to_kind="record",
                ))
        return

    if node.type in ("import_from_statement", "future_import_statement"):
        module = _field(node, "module_name")
        module_name = _text(module, src) if module is not None else ""
        if module_name:
            # Context "from": the statement binds the names it imports, never the module's own name.
            out.append(PendingEdgeFact(
                relation=Relation.IMPORTS_FROM, to_name=module_name,
                line=line, to_kind="record", context="from",
            ))
        for child in node.named_children:
            # py-tree-sitter hands out a fresh wrapper per access, so identity
            # never matches; compare the span.
            if module is not None and child.start_byte == module.start_byte \
                    and child.end_byte == module.end_byte:
                continue
            if child.type == "aliased_import":
                target = _field(child, "name")
                alias = _field(child, "alias")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.IMPORTS, to_name=_text(target, src), line=line,
                        to_kind="block", qualified_prefix=module_name,
                        local_name=_text(alias, src) if alias is not None else None,
                    ))
            elif child.type in ("dotted_name", "identifier"):
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS, to_name=_text(child, src), line=line,
                    to_kind="block", qualified_prefix=module_name,
                ))
            elif child.type == "wildcard_import":
                # `from .fields import *`: every name the module exports (edge_builder `_star_exports`).
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS, to_name="*", line=line, to_kind="block",
                    qualified_prefix=module_name,
                ))


def _imports_js(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    line = node.start_point[0] + 1
    source = _field(node, "source")
    specifier = _text(source, src).strip("'\"") if source is not None else ""
    if not specifier:
        return
    module_fact = PendingEdgeFact(
        relation=Relation.IMPORTS_FROM, to_name=specifier,
        line=line, to_kind="record",
    )
    out.append(module_fact)
    for clause in node.named_children:
        if clause.type not in ("import_clause", "named_imports"):
            continue
        for spec in clause.named_children:
            if spec.type == "identifier":
                out.append(PendingEdgeFact(
                    relation=Relation.IMPORTS, to_name=_text(spec, src), line=line,
                    to_kind="block", qualified_prefix=specifier, context="default",
                ))
            elif spec.type == "namespace_import":
                ns = next((c for c in spec.named_children if c.type == "identifier"), None)
                if ns is not None:
                    module_fact.local_name = _text(ns, src)
            elif spec.type == "named_imports":
                for item in spec.named_children:
                    target = _field(item, "name")
                    alias = _field(item, "alias")
                    if target is not None:
                        out.append(PendingEdgeFact(
                            relation=Relation.IMPORTS, to_name=_text(target, src), line=line,
                            to_kind="block", qualified_prefix=specifier,
                            local_name=_text(alias, src) if alias is not None else None,
                        ))


def _imports_java(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`import a.b.C;` names a class by its file path; `a.b.*` names a package, skipped."""
    if node.type != "import_declaration":
        return
    if any(c.type == "asterisk" for c in node.children):
        return
    target = next((c for c in node.named_children if c.type in ("scoped_identifier", "identifier")), None)
    if target is None:
        return
    line = node.start_point[0] + 1
    dotted = _text(target, src)
    static = any(c.type == "static" for c in node.children)
    # `import static a.b.C.m;` imports a member of class a.b.C.
    owner = dotted.rsplit(".", 1)[0] if static and "." in dotted else dotted
    out.append(PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=owner, line=line,
                               to_kind="record"))
    out.append(PendingEdgeFact(relation=Relation.IMPORTS, to_name=dotted.rsplit(".", 1)[-1],
                               line=line, to_kind="block", qualified_prefix=owner))


def _imports_go(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`import m "k8s.io/x/y"` binds `m` (else the path's last element) to package x/y."""
    if node.type != "import_declaration":
        return
    specs = [c for c in node.named_children if c.type == "import_spec"]
    for group in node.named_children:
        if group.type == "import_spec_list":
            specs.extend(c for c in group.named_children if c.type == "import_spec")
    for spec in specs:
        path = _field(spec, "path")
        specifier = _text(path, src).strip("\"`") if path is not None else ""
        if not specifier:
            continue
        alias = _field(spec, "name")
        local = _text(alias, src) if alias is not None else specifier.rsplit("/", 1)[-1]
        out.append(PendingEdgeFact(
            relation=Relation.IMPORTS_FROM, to_name=specifier, line=spec.start_point[0] + 1,
            to_kind="record",
            # `_` binds nothing; `.` merges the package's names into this file's scope.
            local_name=None if local in ("_", ".") else local,
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
                line=line, to_kind="record",
            ))
        return
    for child in node.named_children:
        if child.type == "export_clause":
            for spec in child.named_children:
                target = _field(spec, "name")
                if target is not None:
                    out.append(PendingEdgeFact(
                        relation=Relation.EXPORTS, to_name=_text(target, src), line=line,
                        to_kind="block", restrict_to_file=True,
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
                    relation=Relation.INHERITS, to_name=raw.rsplit(".", 1)[-1],
                    line=line, from_symbol=idx,
                ))


def _heritage_ts(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type == "extends_type_clause":
            out.extend(
                PendingEdgeFact(
                    relation=Relation.INHERITS,
                    to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                    line=line, from_symbol=idx,
                )
                for item in child.named_children
                if item.type in ("type_identifier", "generic_type", "identifier")
            )
        if child.type != "class_heritage":
            continue
        for clause in child.named_children:
            if clause.type == "extends_clause":
                out.extend(
                    PendingEdgeFact(
                        relation=Relation.INHERITS,
                        to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                        line=line, from_symbol=idx,
                    )
                    for item in clause.named_children
                    if item.type in ("identifier", "member_expression", "generic_type", "type_identifier")
                )
            elif clause.type == "implements_clause":
                out.extend(
                    PendingEdgeFact(
                        relation=Relation.IMPLEMENTS,
                        to_name=_text(item, src).split("<")[0].rsplit(".", 1)[-1],
                        line=line, from_symbol=idx,
                    )
                    for item in clause.named_children
                    if item.type in ("type_identifier", "generic_type", "identifier")
                )


def _java_type_name(node: Node, src: bytes) -> str:
    """`List<Owner>` -> `List`, `a.b.Base` -> `Base`, `Owner[]` -> `Owner`."""
    return _text(node, src).split("<")[0].split("[")[0].strip().rsplit(".", 1)[-1]


def _heritage_go(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`type Engine struct { RouterGroup; *Logger; http.Server }` and an interface's embedded
    `Reader`: their methods are promoted. Context "embeds": method lookup only, never an edge."""
    body = _field(node, "type")
    if body is None or body.type not in ("struct_type", "interface_type"):
        return
    holders = [f for c in body.named_children if c.type == "field_declaration_list" for f in c.named_children]
    holders += [c for c in body.named_children if c.type == "type_elem"]
    line = node.start_point[0] + 1
    for holder in holders:
        if holder.type == "field_declaration":
            if _field(holder, "name") is not None:
                continue  # a named field, not an embed
            typ = _field(holder, "type")
        elif len(holder.named_children) != 1:
            continue  # `A | B`: a constraint's type set, not an embedded interface
        else:
            typ = holder.named_children[0]
        while typ is not None and typ.type == "pointer_type":
            typ = typ.named_children[0] if typ.named_children else None
        if typ is not None and typ.type == "generic_type":  # `Box[int]` embeds Box
            typ = _field(typ, "type")
        if typ is None or typ.type not in ("type_identifier", "qualified_type"):
            continue
        package = _field(typ, "package")
        name = _field(typ, "name") if typ.type == "qualified_type" else typ
        if name is not None:
            out.append(PendingEdgeFact(
                relation=Relation.INHERITS, to_name=_text(name, src), line=line, from_symbol=idx,
                qualified_prefix=_text(package, src) if package is not None else None, context="embeds",
            ))


def _heritage_java(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`extends Base` and `implements A, B` on a class; `extends A, B` on an interface."""
    line = node.start_point[0] + 1
    for child in node.named_children:
        if child.type == "superclass":
            relation = Relation.INHERITS
        elif child.type in ("super_interfaces", "extends_interfaces"):
            relation = Relation.IMPLEMENTS if child.type == "super_interfaces" else Relation.INHERITS
        else:
            continue
        stack = list(child.named_children)
        while stack:
            item = stack.pop(0)
            if item.type == "type_list":
                stack.extend(item.named_children)
            elif item.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
                raw = _text(item, src).split("<")[0]
                out.append(PendingEdgeFact(
                    relation=relation, to_name=raw.rsplit(".", 1)[-1], line=line, from_symbol=idx,
                    qualified_prefix=raw.rsplit(".", 1)[0] if "." in raw else None,
                ))


def _with_languages(core: dict, added: dict, kind: str) -> dict:
    """*core* plus a language module's handlers; a module may add names, never replace a core one."""
    reused = sorted(core.keys() & added.keys())
    if reused:
        raise ValueError(f"languages.{kind} reuses core handler names: {reused}")
    return {**core, **added}


_IMPORT_HANDLERS = _with_languages(
    {"python": _imports_python, "js": _imports_js, "java": _imports_java, "go": _imports_go},
    languages.IMPORT_HANDLERS, "IMPORT_HANDLERS")
_HERITAGE_HANDLERS = _with_languages(
    {"python": _heritage_python, "js": _heritage_js, "ts": _heritage_ts, "java": _heritage_java},
    languages.HERITAGE_HANDLERS, "HERITAGE_HANDLERS")
# Handlers whose call sites are read as references (`a.b.c()` with its qualifier
# and receiver). Every other grammar keeps the generic callee reader, even when
# it has an import handler.
_REFERENCE_CALLEE_HANDLERS = frozenset({"python", "js"})


# --------------------------------------------------------------------------
# Type tables
# --------------------------------------------------------------------------

_TS_NAMED_TYPES = ("type_identifier", "generic_type", "nested_type_identifier")
_TS_OWNER_TYPES = ("class_declaration", "abstract_class_declaration", "interface_declaration")
# `this._register(new Emitter())`, `store.add(new X())`: these return their argument.
_TS_PASSTHROUGH_CALLS = frozenset({"_register", "register", "add"})
_TS_CLASS_ARG_CALLS = frozenset({"createInstance", "get"})
_TS_HEAD = re.compile(r"[A-Za-z_$][\w$]*")
_TS_COLLECTIONS = frozenset({"Array", "ReadonlyArray", "Set", "ReadonlySet", "Iterable", "IterableIterator"})
# `xs.forEach(x => x.run())`: the callback's first parameter is an element of `xs`.
_TS_ELEMENT_CALLBACKS = frozenset({"forEach", "map", "filter", "some", "every", "find", "findIndex", "flatMap"})
_TS_SCOPE_TYPES = ("function_declaration", "method_definition", "arrow_function", "function_expression",
                   "generator_function_declaration", "generator_function")


def _ts_named_type(node: Node | None, src: bytes) -> str | None:
    """`Foo`, `ns.Foo<T>`, `Foo | undefined` -> `Foo`; `Foo[]`, `Array<Foo>` -> `Foo[]`;
    anything else (unions of two types, function types) -> None."""
    if node is not None and node.type == "type_annotation":
        node = node.named_children[0] if node.named_children else None
    if node is not None and node.type == "union_type":
        stack, named = [node], []
        while stack:
            for c in stack.pop().named_children:
                if c.type == "union_type":
                    stack.append(c)
                elif c.type in _TS_NAMED_TYPES:
                    named.append(c)
        node = named[0] if len(named) == 1 else None
    if node is not None and node.type == "readonly_type" and node.named_children:
        node = node.named_children[0]
    if node is not None and node.type == "array_type" and node.named_children:
        element = _ts_named_type(node.named_children[0], src)  # `Foo[]`: what a for-of yields
        return f"{element}[]" if element and not element.endswith("[]") else None
    if node is None or node.type not in _TS_NAMED_TYPES:
        return None
    name = _text(node, src).split("<")[0].rsplit(".", 1)[-1]
    args = _field(node, "type_arguments") if node.type == "generic_type" else None
    if name in _TS_COLLECTIONS and args is not None and len(args.named_children) == 1:
        element = _ts_named_type(args.named_children[0], src)
        return f"{element}[]" if element and not element.endswith("[]") else None
    return name


def _ts_value_type(value: Node | None, src: bytes) -> str | None:
    """What an initializer says about its variable's type: a class for `new Foo()`,
    else `=<expression>` for the builder to evaluate (`=Foo.get()`, `=this._model`)."""
    while value is not None and value.type in ("await_expression", "non_null_expression",
                                               "parenthesized_expression"):
        value = value.named_children[0] if value.named_children else None
    if value is None:
        return None
    if value.type in ("as_expression", "satisfies_expression") and len(value.named_children) == 2:
        return _ts_named_type(value.named_children[1], src)
    if value.type == "new_expression":
        ctor = _field(value, "constructor")
        return _text(ctor, src).rsplit(".", 1)[-1] if ctor is not None else None
    if value.type == "call_expression":
        fn, args = _field(value, "function"), _field(value, "arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        callee = _text(fn, src).rsplit(".", 1)[-1] if fn is not None else ""
        if first is not None and callee in _TS_PASSTHROUGH_CALLS:
            return _ts_value_type(first, src)
        # Dependency injection: `createInstance(Foo, ...)` makes a Foo, `accessor.get(IFoo)`
        # returns the IFoo service.
        if (first is not None and callee in _TS_CLASS_ARG_CALLS and first.type == "identifier"
                and _text(first, src)[:1].isupper()):
            return _text(first, src)
    if value.type in ("call_expression", "member_expression", "identifier", "this"):
        text = _text(value, src)
        if len(text) <= 200 and "\n" not in text:
            return "=" + text
    return None


def _ts_owner(node: Node, src: bytes) -> str | None:
    parent = node.parent
    while parent is not None and parent.type not in _TS_OWNER_TYPES:
        if parent.type in ("function_declaration", "arrow_function", "function_expression"):
            return None
        parent = parent.parent
    name = _field(parent, "name") if parent is not None else None
    return _text(name, src) if name is not None else None


# `(fn)`, `fn as T`, `fn satisfies T`, `fn!`, `<T>fn`: the same value under a wrapper.
_VALUE_WRAPPERS = ("parenthesized_expression", "as_expression", "satisfies_expression",
                   "non_null_expression", "type_assertion")


def _mark_function_field(node: Node, name: str, src: bytes, table: dict[str, str]) -> None:
    """`static make = () => ...` defines a function, so a call to it is not a call
    through a callback slot; the builder reads `Owner.name=>` to tell them apart."""
    value = _field(node, "value")
    while value is not None and value.type in _VALUE_WRAPPERS and value.named_children:
        value = value.named_children[-1 if value.type == "type_assertion" else 0]
    if value is not None and value.type in ("arrow_function", "function_expression", "function") \
            and (owner := _ts_owner(node, src)):
        table.setdefault(f"{owner}.{name}=>", "function")


def _ts_type_arg(node: Node | None, src: bytes) -> str | None:
    """`IObservable<Foo | undefined>` -> `Foo`: a generic's one named type argument,
    which a `read(): T` on it returns."""
    if node is not None and node.type == "type_annotation":
        node = node.named_children[0] if node.named_children else None
    args = _field(node, "type_arguments") if node is not None and node.type == "generic_type" else None
    if args is None or len(args.named_children) != 1 or _ts_named_type(node, src) is None:
        return None
    arg = _ts_named_type(args.named_children[0], src)
    return arg if arg and not arg.endswith("[]") else None


def _ts_owner_type_params(node: Node, src: bytes) -> set[str]:
    """`T` for a member of `interface IObservable<T>`."""
    parent = node.parent
    while parent is not None and parent.type not in _TS_OWNER_TYPES:
        parent = parent.parent
    params = _field(parent, "type_parameters") if parent is not None else None
    return {_text(n, src) for p in (params.named_children if params is not None else ())
            if (n := _field(p, "name")) is not None}


def _ts_union_members(node: Node, src: bytes) -> list[str]:
    """`A | (B | C) | undefined` -> `[A, B, C]`: the named, non-array types it can be."""
    stack, members = [node], []
    while stack:
        n = stack.pop()
        if n.type in ("union_type", "parenthesized_type"):
            stack.extend(reversed(n.named_children))
        elif (member := _ts_named_type(n, src)) and not member.endswith("[]"):
            members.append(member)
    return members


def _collect_type_table_ts(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """Harvest `name: Type` bindings from declarations, constructor params and class fields
    (`this._model.run()` resolves through the field), and initializers.

    Members are also keyed by owner -- `Owner.field`, `Owner.method()` (its return
    type), `fn()` -- so the builder can follow `this._editor.getModel().getValue()`
    through other files. An `=expr` value is an initializer the builder evaluates.
    """
    for node in _nodes_of_types(root, cfg, ("variable_declarator", "required_parameter", "optional_parameter",
                                            "public_field_definition", "property_signature", "method_definition",
                                            "method_signature", "abstract_method_signature",
                                            "function_declaration")):
        name_node = _field(node, "name") or next(
            (c for c in node.named_children if c.type in ("identifier", "property_identifier")), None
        )
        if name_node is None:
            continue
        name = _text(name_node, src)
        if node.type in ("method_definition", "method_signature", "abstract_method_signature",
                         "function_declaration"):
            type_name = _ts_named_type(_field(node, "return_type"), src)
            if type_name is None:
                continue
            owner = _ts_owner(node, src) if node.type != "function_declaration" else None
            if owner and type_name in _ts_owner_type_params(node, src):
                type_name = f"<{type_name}>"  # the receiver's type argument, see `Owner.field<>`
            if any(c.type == "get" for c in node.children):  # a getter reads as a field
                table.setdefault(name, type_name)
                if owner:
                    table.setdefault(f"{owner}.{name}", type_name)
            else:
                table.setdefault(f"{owner}.{name}()" if owner else f"{name}()", type_name)
            continue
        if node.type == "public_field_definition":
            _mark_function_field(node, name, src, table)
        ann = next((c for c in node.named_children if c.type == "type_annotation"), None)
        type_name = _ts_named_type(ann, src) if ann is not None else _ts_value_type(_field(node, "value"), src)
        if type_name is None and ann is not None and ann.named_children:
            # `e: A | B`: the builder resolves the call on each member.
            members = _ts_union_members(ann.named_children[0], src)
            type_name = "|".join(members) if len(members) >= 2 else None
        if type_name is None:
            continue
        table.setdefault(name, type_name)
        type_arg = _ts_type_arg(ann, src)
        if type_arg:
            table.setdefault(f"{name}<>", type_arg)
        # A field, an interface property, or a `private readonly x: T` constructor parameter.
        if node.type in ("public_field_definition", "property_signature") or (
                node.type in ("required_parameter", "optional_parameter")
                and any(c.type in ("accessibility_modifier", "readonly") for c in node.children)):
            owner = _ts_owner(node, src)
            if owner:
                table.setdefault(f"{owner}.{name}", type_name)
                if type_arg:
                    table.setdefault(f"{owner}.{name}<>", type_arg)
    for node in _nodes_of_types(root, cfg, ("type_alias_declaration",)):
        # `type Expr = A | B | C`: a call on an `Expr` may run any member's method.
        name_node, value = _field(node, "name"), _field(node, "value")
        if name_node is None or value is None:
            continue
        # `type Public = Base` is kept too, so the builder can follow it to Base's members.
        if members := _ts_union_members(value, src):
            table.setdefault(f"{_text(name_node, src)}|", "|".join(members))
    _collect_narrowings_ts(root, src, table, cfg)


def _element_source(node: Node | None, src: bytes) -> str | None:
    """`=xs[*]`: an element of the array expression *node*, for the builder to evaluate."""
    if node is None or node.type not in ("identifier", "member_expression", "call_expression"):
        return None
    text = _text(node, src)
    return f"={text}[*]" if len(text) <= 200 and "\n" not in text else None


def _collect_narrowings_ts(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """Types the code implies without declaring: `for (const x of xs)`, `xs.forEach(x => ...)`,
    and `if (x instanceof Foo)`."""
    for node in _nodes_of_types(root, cfg, ("for_in_statement", "call_expression", "binary_expression")):
        if node.type == "for_in_statement":
            left, op = _field(node, "left"), _field(node, "operator")
            if left is not None and left.type == "identifier" and op is not None and op.type == "of":
                element = _element_source(_field(node, "right"), src)
                if element:
                    table.setdefault(_text(left, src), element)
        elif node.type == "call_expression":
            fn, args = _field(node, "function"), _field(node, "arguments")
            prop = _field(fn, "property") if fn is not None and fn.type == "member_expression" else None
            callback = args.named_children[0] if args is not None and args.named_children else None
            if prop is None or _text(prop, src) not in _TS_ELEMENT_CALLBACKS or callback is None \
                    or callback.type != "arrow_function":
                continue
            param = _field(callback, "parameter")
            if param is None:
                params = _field(callback, "parameters")
                first = params.named_children[0] if params is not None and params.named_children else None
                if first is not None and first.type == "required_parameter" and not any(
                        c.type == "type_annotation" for c in first.named_children):
                    param = _field(first, "pattern")
            element = _element_source(_field(fn, "object"), src)
            if param is not None and param.type == "identifier" and element:
                table.setdefault(_text(param, src), element)
        else:
            left, op, right = _field(node, "left"), _field(node, "operator"), _field(node, "right")
            if (op is not None and op.type == "instanceof" and left is not None and left.type == "identifier"
                    and right is not None and right.type == "identifier"):
                table.setdefault(_text(left, src), _text(right, src))


def _collect_type_table_js(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """JS has no annotations; `const x = new Foo()` is the only reliable binding."""
    for node in _nodes_of_types(root, cfg, ("variable_declarator", "field_definition")):
        if node.type == "field_definition":
            if (name_node := _field(node, "property")) is not None:
                _mark_function_field(node, _text(name_node, src), src, table)
        elif node.type == "variable_declarator":
            name_node = _field(node, "name")
            value = _field(node, "value")
            if name_node is not None and value is not None and value.type == "new_expression":
                ctor = _field(value, "constructor")
                if ctor is not None:
                    table.setdefault(_text(name_node, src), _text(ctor, src).rsplit(".", 1)[-1])


def _annotated_type(annotation: str) -> str:
    """The class an annotation names: `Optional[Foo]` and `Foo | None` are `Foo`."""
    text = annotation.strip().strip("'\"")
    if text.startswith("Optional[") and text.endswith("]"):
        text = text[len("Optional["):-1]
    text = next((part.strip() for part in text.split("|") if part.strip() != "None"), text)
    return text.split("[")[0].rsplit(".", 1)[-1]


_QUERY_CACHE: dict[tuple[str, tuple[str, ...]], object] = {}


def _nodes_of_types(root: Node, cfg: LanguageConfig, types: tuple[str, ...]) -> list[Node]:
    """The nodes of *types* under *root* (itself included), in the order a stack walk
    pushing ``named_children`` pops them: preorder with children reversed.

    Found by a tree-sitter query, natively, instead of visiting every node in Python.
    Popped first is the node ending last, a node before its descendants (which end
    no later and start no earlier); a node and its child spanning the same bytes stay
    in the query's preorder, parent first, as the sort is stable.
    """
    from tree_sitter import Query, QueryCursor

    key = (cfg.name, types)
    query = _QUERY_CACHE.get(key)
    if query is None:
        # Not under _PARSER_LOCK: _get_parser takes it, and it is not reentrant. Two
        # threads may both compile; setdefault keeps one.
        pattern = "[" + " ".join(f"({t})" for t in types) + "] @node"
        query = _QUERY_CACHE.setdefault(key, Query(_get_parser(cfg).language, pattern))
    nodes = QueryCursor(query).captures(root).get("node", [])
    nodes.sort(key=lambda n: (-n.end_byte, n.start_byte))
    return nodes


def _collect_type_table_python(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """`self.x = Foo()`, `x: Foo = …` and a parameter's `x: Foo` are the usable bindings.

    Python is mostly untyped, so members are also keyed by owner for the builder to
    evaluate: `Owner.x` from `self.x = …` in a method (or `x = Foo()` in the class
    body), and `Owner.m()` / `f()` from a return annotation or, unannotated, from
    returns that all agree. A value that is not a constructor call is kept as
    `=expr` (`self.admin = model_admin`, `return self._chain()`).
    """
    for node in _nodes_of_types(root, cfg, ("typed_parameter", "typed_default_parameter", "assignment")):
        if node.type in ("typed_parameter", "typed_default_parameter"):
            # A parameter's annotation types every call on it in the body (P-5).
            name = next((c for c in node.named_children if c.type == "identifier"), None)
            ann = _field(node, "type")
            if name is not None and ann is not None:
                table.setdefault(_text(name, src), _annotated_type(_text(ann, src)))
        elif node.type == "assignment":
            left = _field(node, "left")
            right = _field(node, "right")
            ann = _field(node, "type")
            if left is not None and ann is not None:
                table.setdefault(_text(left, src).split(".")[-1], _annotated_type(_text(ann, src)))
            elif left is not None and right is not None and right.type == "call":
                fn = _field(right, "function")
                if fn is not None and fn.type in ("identifier", "attribute"):
                    called = _text(fn, src).rsplit(".", 1)[-1]
                    if called[:1].isupper():
                        table.setdefault(_text(left, src).split(".")[-1], called)
    for cls in _nodes_of_types(root, cfg, ("class_definition",)):
        owner_node, body = _field(cls, "name"), _field(cls, "body")
        if owner_node is None or body is None:
            continue
        owner = _text(owner_node, src)
        for stmt in body.named_children:
            if stmt.type == "expression_statement" and stmt.named_children and stmt.named_children[0].type == "assignment":
                assign = stmt.named_children[0]
                left, right = _field(assign, "left"), _field(assign, "right")
                value = _py_value(right, src)
                if left is not None and left.type == "identifier" and value and not value.startswith("="):
                    table.setdefault(f"{owner}.{_text(left, src)}", value)
            func = stmt.named_children[-1] if stmt.type == "decorated_definition" and stmt.named_children else stmt
            if func.type == "function_definition":
                _py_function_types(func, src, table, cfg, owner)
    for stmt in root.named_children if root.type == "module" else ():
        func = stmt.named_children[-1] if stmt.type == "decorated_definition" and stmt.named_children else stmt
        if func.type == "function_definition":
            _py_function_types(func, src, table, cfg, None)
    if b"fixture" in src:
        # A pytest fixture's type, `fixture:<name>`, read by the builder for a test parameter of that name.
        fixtures = _python_fixture_names(root, src)
        scopes = _python_scoped_bindings(root, src, cfg) if fixtures else {}
        for name, node in fixtures:
            typ = _python_return_type(node, scopes, src)
            if typ:
                table.setdefault(f"{_FIXTURE_KEY}{name}", typ)
    exported = _python_literal_all(root, src)
    if exported is not None:
        table["__all__"] = STAR_EXPORTS_MARK + ",".join(exported)


def _python_literal_all(root: Node, src: bytes) -> list[str] | None:
    """The names a module-level `__all__ = [...]` / `(...)` lists, when every item is a string literal; else None
    (no `__all__`, or one computed: `__all__ += …`, a comprehension, a name).

    shortcut: a computed `__all__` is not read, so `from m import *` falls back to the no-underscore rule;
    evaluate the common forms (`+=` of literals, `[*a.__all__, ...]`) if star imports through one go wrong."""
    if root.type != "module" or any(_extends_all(stmt, src) for stmt in root.named_children):
        return None
    for stmt in root.named_children:
        node = stmt.named_children[0] if stmt.type == "expression_statement" and stmt.named_children else None
        if node is None or node.type != "assignment" or _text(_field(node, "left"), src) != "__all__":
            continue
        value = _field(node, "right")
        items = ([c for c in value.named_children if c.type != "comment"]  # `# models` grouping the names
                 if value is not None and value.type in ("list", "tuple") else None)
        contents = [next((c for c in item.named_children if c.type == "string_content"), None)
                    for item in items] if items is not None else None
        if contents is None or any(item.type != "string" for item in items) or None in contents:
            return None
        return [_text(c, src) for c in contents]
    return None


def _extends_all(stmt: Node, src: bytes) -> bool:
    """A module-level `__all__ += …`, `__all__.extend(…)` or `__all__.append(…)`: the literal is not the whole list."""
    node = stmt.named_children[0] if stmt.type == "expression_statement" and stmt.named_children else None
    if node is None:
        return False
    if node.type == "augmented_assignment":
        left = _field(node, "left")
        return left is not None and _text(left, src) == "__all__"
    if node.type == "call":
        fn = _field(node, "function")
        return fn is not None and _text(fn, src) in ("__all__.extend", "__all__.append")
    return False


_PY_SIMPLE_EXPR = re.compile(r"[A-Za-z_][\w.]*(\(.*\))?(\.[A-Za-z_]\w*(\(.*\))?)*", re.S)


def _py_value(node: Node | None, src: bytes) -> str | None:
    """What a Python expression is worth to the type table: the class a
    constructor call names, `=expr` for a name or call chain, else None."""
    if node is None:
        return None
    if node.type == "call":
        fn = _field(node, "function")
        if fn is not None and fn.type in ("identifier", "attribute"):
            called = _text(fn, src).rsplit(".", 1)[-1]
            if called[:1].isupper():
                return called
    if node.type in ("identifier", "attribute", "call"):
        text = _text(node, src)
        if len(text) <= 200 and _PY_SIMPLE_EXPR.fullmatch(text):
            return "=" + text
    return None


def _py_function_types(func: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig,
                       owner: str | None) -> None:
    """A Python function's return type, and in a method the `self.x` it assigns."""
    name_node = _field(func, "name")
    if name_node is None:
        return
    name = _text(name_node, src)
    returns_key = f"{owner}.{name}()" if owner else f"{name}()"
    ann = _field(func, "return_type")
    if ann is not None:
        table.setdefault(returns_key, _annotated_type(_text(ann, src)))
    else:
        body = _field(func, "body")
        locals_ = _py_local_values(func, src, cfg)
        values = set()
        for ret in _nodes_of_types(body, cfg, ("return_statement",)) if body is not None else ():
            if _py_enclosing_function(ret) != func:
                continue
            expr = ret.named_children[0] if ret.named_children else None
            if expr is None or (expr.type == "none"):
                continue
            value = _py_value(expr, src)
            if value and value.startswith("=") and value[1:] in locals_:  # `return clone` with `clone = self._chain()`
                value = locals_[value[1:]]
            values.add(value)
        if len(values) == 1 and None not in values:
            table.setdefault(returns_key, values.pop())
    if owner is None:
        return
    for node in _nodes_of_types(func, cfg, ("assignment",)):
        left, right = _field(node, "left"), _field(node, "right")
        if left is None or left.type != "attribute" or _text(left, src).count(".") != 1:
            continue
        obj, _, attr = _text(left, src).partition(".")
        value = _py_value(right, src)
        if obj == "self" and value:
            table.setdefault(f"{owner}.{attr}", value)


def _py_enclosing_function(node: Node) -> Node | None:
    scope = node.parent
    while scope is not None and scope.type not in ("function_definition", "lambda"):
        scope = scope.parent
    return scope


def _py_local_values(func: Node, src: bytes, cfg: LanguageConfig) -> dict[str, str]:
    """`x = …` assigned once in *func* itself (not a nested function): x's table value."""
    seen: dict[str, set[str | None]] = {}
    for node in _nodes_of_types(func, cfg, ("assignment",)):
        left = _field(node, "left")
        if left is None or left.type != "identifier" or _py_enclosing_function(node) != func:
            continue
        seen.setdefault(_text(left, src), set()).add(_py_value(_field(node, "right"), src))
    return {name: next(iter(values)) for name, values in seen.items()
            if len(values) == 1 and None not in values}


_PY_BINDING_TYPES = ("typed_parameter", "typed_default_parameter", "assignment")
# Type-table key prefix of a pytest fixture's type (the receiver-type marker of a parameter that may be one is
# models.FIXTURE_MARK).
_FIXTURE_KEY = "fixture:"
_PY_SCOPE_TYPES = frozenset({"function_definition", "lambda"})
# Every node that binds a name, per grammar: a superset of what any one scope binds, read in one query.
_PY_BINDING_SITES = ("parameters", "lambda_parameters", "assignment", "augmented_assignment", "for_statement",
                     "for_in_clause", "named_expression", "as_pattern_target")


def _python_binding(node: Node, src: bytes) -> tuple[str, str] | None:
    """(name, type) a binding gives: a parameter's `x: Foo`, `x: Foo = …`, `x = Foo()`, and
    `self.x = Foo()` as `self.x` (its own key, so a local `x` elsewhere cannot type it)."""
    if node.type in ("typed_parameter", "typed_default_parameter"):
        # A parameter's annotation types every call on it in the body.
        name = next((c for c in node.named_children if c.type == "identifier"), None)
        ann = _field(node, "type")
        return (_text(name, src), _annotated_type(_text(ann, src))) if name is not None and ann is not None \
            else None
    left, right, ann = _field(node, "left"), _field(node, "right"), _field(node, "type")
    if left is None or left.type not in ("identifier", "attribute"):
        return None
    key = _text(left, src)
    if left.type == "attribute" and key.split(".", 1)[0] not in ("self", "cls"):
        return None  # `obj.x = Foo()` says nothing about another `x`
    if ann is not None:
        return key, _annotated_type(_text(ann, src))
    if right is not None and right.type == "call":
        fn = _field(right, "function")
        if fn is not None and fn.type in ("identifier", "attribute"):
            called = _text(fn, src).rsplit(".", 1)[-1]
            if called[:1].isupper():
                return key, called
    return None


def _python_scoped_bindings(root: Node, src: bytes, cfg: LanguageConfig,
                            assigned: dict[tuple[int | None, str], Node | None] | None = None,
                            ) -> dict[int | None, dict[str, str]]:
    """Each function's own bindings, by its start byte; each class's (`self.x = Foo()` in its methods, and its
    class-level `x: Foo`) by the class's start byte; None holds the module's, which every function sees.
    One query for the file: a query per function cost ~13% of a parse. *assigned*, when given, gets each scope's
    untyped `x = <value>` (None for a name given two different values), for the walker to type as a chain."""
    scopes: dict[int | None, dict[str, str]] = {}
    aliases: list[tuple[int | None, int | None, str, str]] = []
    helpers: list[tuple[int | None, str, str]] = []
    params: dict[str, set[str]] = {}
    loops: list[tuple[int | None, Node]] = []
    unpacks: list[tuple[int | None, list[str], str]] = []
    for node in _nodes_of_types(root, cfg, _PY_BINDING_TYPES + _PY_LOOP_TYPES):
        scope = node.parent
        while scope is not None and scope.type not in _PY_SCOPE_TYPES and scope.type != "class_definition":
            scope = scope.parent
        key = scope.start_byte if scope is not None else None
        if node.type in _PY_LOOP_TYPES:
            loops.append((key, node))
            continue
        binding = _python_binding(node, src)
        if binding is None and assigned is not None:
            _note_assigned(assigned, key, node, src)
        if binding is not None:
            owner = _python_owner_class(node) if binding[0].startswith(("self.", "cls.")) else None
            table = scopes.setdefault(owner.start_byte if owner is not None else key, {})
            table.setdefault(*binding)
            for suffix, typ in _python_element_types(node, src).items():
                table.setdefault(binding[0] + suffix, typ)
            if node.type != "assignment":
                params.setdefault(binding[0], set()).add(binding[1])
        elif (alias := _python_self_alias(node, src)) is not None:
            owner = _python_owner_class(node)
            aliases.append((key, owner.start_byte if owner is not None else None, *alias))
        elif (helper := _python_helper_call(node, src)) is not None:
            helpers.append((key, *helper))
        elif (unpacked := _python_unpacked_call(node, src)) is not None:
            unpacks.append((key, *unpacked))
        else:
            for name, value in _python_unpacked(node, src):
                typed = _python_constructed(value, src)
                if typed:
                    scopes.setdefault(key, {}).setdefault(name, typed)
                elif (called := _python_called_name(value, src)) is not None:
                    helpers.append((key, name, called))
    # `conn = _make_connector()`: the variable takes what that same-file function returns.
    functions = _python_module_functions(root, src)
    returns = {name: _python_return_type(fn, scopes, src) for name, fn in functions.items()}
    for key, name, called in helpers:
        if returns.get(called):
            scopes.setdefault(key, {}).setdefault(name, returns[called])
    # `manager, ctx = _build(r)` over `def _build(r) -> tuple[SkillManager, dict]`: each name its item's type.
    for key, names, called in unpacks:
        items = _python_tuple_return(functions[called], src) if called in functions else []
        for name, typ in zip(names, items) if len(items) == len(names) else ():
            if typ:
                scopes.setdefault(key, {}).setdefault(name, typ)
    # `for e in self._emitters` over `self._emitters: list[Emitter]`, `for k, v in d.items()`: the element types.
    for key, node in loops:
        for name, typ in _python_loop_types(node, src, key, scopes):
            scopes.setdefault(key, {}).setdefault(name, typ)
    # `def __init__(self, port: Port): self.port = port`: the attribute takes the parameter's type.
    for key, owner_key, attr, name in aliases:
        typ = scopes.get(key, {}).get(name)
        if typ:
            scopes.setdefault(owner_key, {}).setdefault(attr, typ)
    # An attribute set in a base class elsewhere (`self.processor`, assigned in BaseConnector) takes the
    # type of a typed parameter of its name in this file, the idiom that hands it over; only when every
    # such parameter agrees. A local `x = Foo()` never types `self.x`: another function's local is not this one's.
    module = scopes.setdefault(None, {})
    for name, types in params.items():
        if len(types) == 1:
            module.setdefault(f"self.{name}", next(iter(types)))
    return scopes


_PY_LOOP_TYPES = ("for_statement", "for_in_clause")
_PY_SEQUENCES = frozenset({"list", "List", "Sequence", "MutableSequence", "Iterable", "Iterator", "Collection", "set",
                           "Set", "frozenset", "FrozenSet", "AbstractSet", "MutableSet", "tuple", "Tuple", "deque",
                           "AsyncIterable", "AsyncIterator", "Generator", "AsyncGenerator"})
_PY_MAPPINGS = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "defaultdict", "DefaultDict", "OrderedDict"})
# Type-table key suffixes of a container's element (what iterating it gives) and of a mapping's value.
_ELEMENT, _VALUE = "[]", "{}"


def _python_element_types(node: Node, src: bytes) -> dict[str, str]:
    """`xs: list[Foo]` -> {"[]": "Foo"}, `d: dict[str, Foo]` -> {"[]": "str", "{}": "Foo"}: what iterating an
    annotated container gives, and a mapping's values."""
    ann = _field(node, "type")
    text = _text(ann, src).strip().strip("'\"") if ann is not None else ""
    text = next((part.strip() for part in text.split("|") if part.strip() != "None"), text)
    if text.startswith("Optional[") and text.endswith("]"):
        text = text[len("Optional["):-1]
    head, _, args = text.partition("[")
    head = head.rsplit(".", 1)[-1]
    parts = _split_type_args(args[:-1]) if args.endswith("]") else []
    if head in _PY_SEQUENCES and parts and (head not in ("tuple", "Tuple") or parts[-1:] == ["..."]):
        return {_ELEMENT: _annotated_type(parts[0])}
    if head in _PY_MAPPINGS and len(parts) == 2:
        return {_ELEMENT: _annotated_type(parts[0]), _VALUE: _annotated_type(parts[1])}
    return {}


def _split_type_args(text: str) -> list[str]:
    """`str, list[Foo]` -> ["str", "list[Foo]"]: top-level comma-separated type arguments."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _python_unpacked(node: Node, src: bytes) -> list[tuple[str, Node]]:
    """`a, b = Foo(), make()`: each name with the value it takes, when both sides list as many items."""
    left, right = _field(node, "left"), _field(node, "right")
    if node.type != "assignment" or left is None or right is None \
            or left.type not in ("pattern_list", "tuple_pattern") or right.type not in ("expression_list", "tuple"):
        return []
    names, values = left.named_children, right.named_children
    if len(names) != len(values) or any(n.type != "identifier" for n in names):
        return []
    return [(_text(n, src), v) for n, v in zip(names, values)]


def _note_assigned(assigned: dict[tuple[int | None, str], Node | None], key: int | None, node: Node,
                   src: bytes) -> None:
    """Record the values an untyped assignment gives its names (`x = v`, `a, b = v1, v2`, and `self.x = v` under
    its class, as `self.x`); `None` is no value."""
    left, right = _field(node, "left"), _field(node, "right")
    if node.type != "assignment" or left is None or right is None:
        return
    target = _text(left, src)
    head, _, attr = target.partition(".")
    if left.type == "attribute" and head in ("self", "cls") and attr.isidentifier():
        owner = _python_owner_class(node)
        key, pairs = (owner.start_byte if owner is not None else None), [(f"self.{attr}", right)]
    else:
        pairs = [(target, right)] if left.type == "identifier" else _python_unpacked(node, src)
    for name, value in pairs:
        if value.type == "none":
            continue
        slot = (key, name)
        seen = assigned.get(slot, value)
        assigned[slot] = value if seen is not None and _text(seen, src) == _text(value, src) else None


# Links of a receiver chain the parser spells for the builder: one more each recurses, so a generated `a.b.c...` of
# hundreds of links would exhaust the stack and lose the whole file. A fluent builder runs to ~40 links.
_PY_MAX_CHAIN_LINKS = 128
# A name read through its assignment (`a2 = a1.nxt`) spends this many links of the same budget: a hop recurses ~3x
# as deep as a link, and a generated run of hundreds of hops would otherwise restart the count at each one.
_PY_ASSIGNMENT_HOP_LINKS = 3


def _python_chain(value: Node, src: bytes, name_type: Callable[[Node, str, int], str | None],
                  depth: int = 0) -> str | None:
    """*value* as a receiver chain the edge builder evaluates (`typed_receiver_chains`): a typed name, a class
    constructed (`Foo(..)` is a Foo) or a function called (`make()`, typed by what it returns, in its own file);
    None for anything else, or past `_PY_MAX_CHAIN_LINKS`. *name_type* types a name where it is read, at *depth*."""
    if depth > _PY_MAX_CHAIN_LINKS:
        return None
    while value.type in ("await", "parenthesized_expression") and value.named_children:
        value = value.named_children[0]
    if value.type in ("identifier", "attribute"):
        name = _text(value, src)
        if name in ("self", "cls"):
            return "self"
        typ = name_type(value, name, depth)
        if typ:
            return typ.removeprefix("=") if not typ.startswith(FIXTURE_MARK) else None
    if value.type == "attribute":   # `self.a.b`, `User.objects`: each attribute's type on what precedes it
        head = _python_member_head(_field(value, "object"), src, name_type, depth + 1)
        return f"{head}.{_text(_field(value, 'attribute'), src)}" if head else None
    fn = _field(value, "function") if value.type == "call" else None
    if fn is not None and fn.type == "identifier":
        name = _text(fn, src)
        if name in _BUILTIN_GLOBALS["python"]:
            return None   # `super().f()`, `dict()`: no repo function's return
        return "self" if name == "cls" else name if name[:1].isupper() else f"{name}()"
    if fn is not None and fn.type == "attribute":   # `obj.make()`, `Foo.create()`: the method's return type
        head = _python_member_head(_field(fn, "object"), src, name_type, depth + 1)
        return f"{head}.{_text(_field(fn, 'attribute'), src)}()" if head else None
    return None


def _python_member_head(obj: Node, src: bytes, name_type: Callable[[Node, str, int], str | None],
                        depth: int = 0) -> str | None:
    """The chain `obj.<member>` continues: *obj*'s own, else a class the file names (`Foo.create()`, `User.objects`)."""
    head = _python_chain(obj, src, name_type, depth)
    if head is None and obj.type == "identifier" and _text(obj, src)[:1].isupper():
        return _text(obj, src)
    return head


def _as_type(chain: str | None) -> str | None:
    """A chain as a type-table value: a bare class name as itself, anything longer marked `=`."""
    return chain if not chain or (chain.isidentifier() and chain != "self") else f"={chain}"


def _python_unpacked_call(node: Node, src: bytes) -> tuple[list[str], str] | None:
    """`a, b = make(...)` (or awaited): the names, and the bare function whose returned tuple they unpack."""
    left, right = _field(node, "left"), _field(node, "right")
    if node.type != "assignment" or left is None or right is None or left.type not in ("pattern_list", "tuple_pattern"):
        return None
    called = _python_called_name(right, src)
    names = left.named_children
    if called is None or any(n.type != "identifier" for n in names):
        return None
    return [_text(n, src) for n in names], called


def _python_tuple_return(node: Node, src: bytes) -> list[str]:
    """`def f() -> tuple[Foo, dict]`: the class each item names (`dict` too: no repo type matches it)."""
    fn = _field(node, "definition") if node.type == "decorated_definition" else node
    ann = _field(fn, "return_type")
    head, _, args = _text(ann, src).strip().strip("'\"").partition("[") if ann is not None else ("", "", "")
    if head.rsplit(".", 1)[-1] not in ("tuple", "Tuple") or not args.endswith("]"):
        return []
    items = _split_type_args(args[:-1])
    return [] if "..." in items else [_annotated_type(item) for item in items]


def _python_constructed(value: Node, src: bytes) -> str | None:
    """`Foo(...)`, `mod.Foo(...)`: the class a value constructs (a capitalised callee), else None."""
    fn = _field(value, "function") if value.type == "call" else None
    called = _text(fn, src).rsplit(".", 1)[-1] if fn is not None and fn.type in ("identifier", "attribute") else ""
    return called if called[:1].isupper() else None


def _python_called_name(value: Node, src: bytes) -> str | None:
    """`make(...)`, `await make(...)`: the bare function a value calls, else None."""
    if value.type == "await" and value.named_children:
        value = value.named_children[0]
    fn = _field(value, "function") if value.type == "call" else None
    return _text(fn, src) if fn is not None and fn.type == "identifier" else None


def _python_loop_types(node: Node, src: bytes, key: int | None,
                       scopes: dict[int | None, dict[str, str]]) -> list[tuple[str, str]]:
    """The names a `for` (or a comprehension's `for`) binds, each with its type when the iterable is a typed
    container: `for x in xs`, `for k, v in d.items()`, `.values()`, `.keys()`, `for i, x in enumerate(xs)`."""
    left, right = _field(node, "left"), _field(node, "right")
    if left is None or right is None:
        return []
    iterable, view = right, ""
    fn = _field(right, "function") if right.type == "call" else None
    args = _field(right, "arguments") if fn is not None else None
    if fn is not None and fn.type == "identifier" and _text(fn, src) == "enumerate" and args is not None \
            and args.named_children:
        iterable, view = args.named_children[0], "enumerate"
    elif fn is not None and fn.type == "attribute" and args is not None and not args.named_children:
        view = _text(_field(fn, "attribute"), src)
        iterable = _field(fn, "object")
    if iterable.type not in ("identifier", "attribute"):
        return []
    name = _text(iterable, src)
    if iterable.type == "attribute" and name.split(".", 1)[0] not in ("self", "cls"):
        return []
    lookup = (_python_owner_class(node),) if iterable.type == "attribute" else ()
    tables = [scopes.get(c.start_byte if c is not None else None, {}) for c in lookup] or \
        [scopes.get(k, {}) for k in _python_enclosing_keys(node, key)]
    # A class-level `xs: list[Foo]` is keyed `xs`, a method's `self.xs: list[Foo]` `self.xs`.
    names = (name, name.partition(".")[2]) if iterable.type == "attribute" else (name,)
    element = next((t[n + _ELEMENT] for t in tables for n in names if n + _ELEMENT in t), None)
    value = next((t[n + _VALUE] for t in tables for n in names if n + _VALUE in t), None)
    if view == "items" and left.type in ("pattern_list", "tuple_pattern") and len(left.named_children) == 2:
        pairs = zip(left.named_children, (element, value))
    elif view in ("", "keys", "values") and left.type == "identifier":
        pairs = [(left, value if view == "values" else element)]
    elif view == "enumerate" and left.type in ("pattern_list", "tuple_pattern") and len(left.named_children) == 2:
        pairs = [(left.named_children[1], element)]
    else:
        return []
    return [(_text(n, src), t) for n, t in pairs if t and n.type == "identifier"]


def _python_enclosing_keys(node: Node, key: int | None) -> list[int | None]:
    """*key*, then each enclosing function's scope key outwards, then the module's (None)."""
    keys: list[int | None] = [key]
    scope = node.parent
    while scope is not None:
        if scope.type in _PY_SCOPE_TYPES and scope.start_byte != key:
            keys.append(scope.start_byte)
        scope = scope.parent
    return [*keys, None] if key is not None else keys


def _python_outer_names(fn: Node, src: bytes) -> frozenset[str]:
    """The names *fn* declares `global` / `nonlocal`: the outer scope's even where fn rebinds them, never a shadow
    of the module's binding."""
    names: set[str] = set()
    stack = list(fn.named_children)
    while stack:
        node = stack.pop()
        if node.type in _VALUE_SCOPE_TYPES or node.type == "class_definition":
            continue
        if node.type in ("global_statement", "nonlocal_statement"):
            names.update(_text(c, src) for c in node.named_children if c.type == "identifier")
            continue
        stack.extend(node.named_children)
    return frozenset(names)


def _python_owner_class(node: Node) -> Node | None:
    """The class whose `self` a method's `self.x` is: the nearest enclosing class definition."""
    scope = node.parent
    while scope is not None and scope.type != "class_definition":
        scope = scope.parent
    return scope


def _python_helper_call(node: Node, src: bytes) -> tuple[str, str] | None:
    """`x = make()` (no annotation, a bare function name called): ("x", "make"), typed later by what the
    file's own `make` returns."""
    left, right = _field(node, "left"), _field(node, "right")
    if node.type != "assignment" or _field(node, "type") is not None or left is None or right is None:
        return None
    if right.type == "await":
        right = right.named_children[0] if right.named_children else None
    fn = _field(right, "function") if right is not None and right.type == "call" else None
    if left.type != "identifier" or fn is None or fn.type != "identifier":
        return None
    return _text(left, src), _text(fn, src)


def _python_module_functions(root: Node, src: bytes) -> dict[str, Node]:
    """The file's top-level functions by name; a name defined twice is left out (which one runs is unclear)."""
    found: dict[str, list[Node]] = {}
    for child in root.named_children:
        fn = _field(child, "definition") if child.type == "decorated_definition" else child
        if fn is not None and fn.type == "function_definition" and (name := _field(fn, "name")) is not None:
            found.setdefault(_text(name, src), []).append(child)
    return {name: nodes[0] for name, nodes in found.items() if len(nodes) == 1}


_GENERATOR_TYPES = frozenset({"Generator", "Iterator", "AsyncGenerator", "AsyncIterator", "Iterable"})


def _python_return_type(node: Node, scopes: dict[int | None, dict[str, str]], src: bytes) -> str | None:
    """What a top-level function returns: its annotation, else the one type every `return x` (a name its
    own bindings type, or `Foo(...)`) gives; a pytest fixture's `yield x` counts as a return. Two types, a
    bare `return`, or anything else returned: no type.

    shortcut: one level only, a returned name bound by another helper call (`c = g(); return c`) is not
    followed; follow it if helper chains turn out common in test suites."""
    fn = _field(node, "definition") if node.type == "decorated_definition" else node
    ann = _field(fn, "return_type")
    if ann is not None:
        text = _text(ann, src).strip()
        head = text.split("[", 1)[0].rsplit(".", 1)[-1]
        if head in _GENERATOR_TYPES and "[" in text:
            text = text.split("[", 1)[1].split(",", 1)[0].rstrip("]")
        return _annotated_type(text) or None
    own = scopes.get(fn.start_byte, {})
    types: set[str | None] = set()
    stack = list(fn.named_children)
    while stack:
        cur = stack.pop()
        if cur.type in _PY_SCOPE_TYPES or cur.type == "class_definition":
            continue  # a nested function's or class's returns are its own
        if cur.type in ("return_statement", "yield"):
            value = cur.named_children[0] if cur.named_children else None
            if value is not None and value.type == "identifier":
                types.add(own.get(_text(value, src)))
            elif value is not None and value.type == "call" and (callee := _field(value, "function")) is not None \
                    and callee.type in ("identifier", "attribute") and _text(callee, src).rsplit(".", 1)[-1][:1].isupper():
                types.add(_text(callee, src).rsplit(".", 1)[-1])
            else:
                types.add(None)
            continue
        stack.extend(cur.named_children)
    return next(iter(types)) if len(types) == 1 else None


_FIXTURE_DECORATORS = frozenset({"pytest.fixture", "fixture", "pytest_asyncio.fixture"})
_PY_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "functools.cached_property"})


def _decorators(definition: Node, src: bytes) -> list[str]:
    """`@pytest.fixture(scope="module")` -> "pytest.fixture": the names a decorated definition carries."""
    wrapper = definition.parent if definition.parent is not None and definition.parent.type == "decorated_definition" \
        else definition
    return [_text(d, src).lstrip("@").split("(", 1)[0].strip() for d in wrapper.named_children if d.type == "decorator"]


def _pytest_injected(fn: Node, src: bytes) -> bool:
    """Whether pytest calls *fn* with fixtures: a `test*` function or method, or a fixture itself."""
    name = _field(fn, "name")
    return (name is not None and _text(name, src).startswith("test")) or bool(
        _FIXTURE_DECORATORS & set(_decorators(fn, src)))


def _python_fixture_names(root: Node, src: bytes) -> list[tuple[str, Node]]:
    """The file's top-level `@pytest.fixture` / `@fixture` functions, by the name a test parameter uses (a test
    class's own fixtures: _python_class_fixture).

    shortcut: the function's name is the fixture's name, so `@pytest.fixture(name="svc")` is missed, and a
    `@pytest.mark.parametrize("svc", ...)` that overrides the fixture is not seen; read the decorator's
    arguments if either shows up as a wrong typed edge."""
    return [(name, child) for child in root.named_children if (name := _fixture_name(child, src))]


def _fixture_name(definition: Node, src: bytes) -> str | None:
    """A `@pytest.fixture`-decorated function's name, for a decorated definition node; else None."""
    fn = _field(definition, "definition") if definition.type == "decorated_definition" else None
    if fn is None or fn.type != "function_definition" or not _FIXTURE_DECORATORS & set(_decorators(fn, src)):
        return None
    return _text(_field(fn, "name"), src)


def _python_class_fixture(cls: Node | None, name: str, src: bytes) -> Node | None:
    """The fixture *name* a test class defines itself, which overrides a module or conftest.py one of that name
    for the class's tests (`class TestX: @pytest.fixture def svc(self): return ServiceB()`)."""
    body = _field(cls, "body") if cls is not None else None
    return next((child for child in (body.named_children if body is not None else ())
                 if _fixture_name(child, src) == name), None)


def _python_self_alias(node: Node, src: bytes) -> tuple[str, str] | None:
    """`self.x = y` (no annotation): ("self.x", "y"), typed later by what `y` is in that function."""
    left, right = _field(node, "left"), _field(node, "right")
    if node.type != "assignment" or _field(node, "type") is not None or left is None or right is None:
        return None
    if left.type != "attribute" or right.type != "identifier":
        return None
    key = _text(left, src)
    head, _, attr = key.partition(".")
    return (key, _text(right, src)) if head in ("self", "cls") and attr and "." not in attr else None


# Nodes that bind names of their own (Python comprehensions too: their targets do not leak).
_VALUE_SCOPE_TYPES = frozenset({
    "function_definition", "lambda", "list_comprehension", "dictionary_comprehension",
    "set_comprehension", "generator_expression", *_TS_SCOPE_TYPES,
})
# A binding site, and the field holding what it binds.
_BINDING_FIELDS = {
    "assignment": "left", "augmented_assignment": "left", "for_statement": "left", "for_in_clause": "left",
    "named_expression": "name", "variable_declarator": "name", "for_in_statement": "left",
    "catch_clause": "parameter",
}
_NO_BINDING_TYPES = frozenset({"attribute", "subscript", "member_expression", "subscript_expression"})
# `const run = () => …` is a function block of its own: its name shadows nothing.
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
# Every node that binds a name: a superset of what any one scope binds, read in one query.
_JS_BINDING_SITES = ("formal_parameters", "variable_declarator", "for_in_statement", "catch_clause", "arrow_function")
# Grammars (`import_handler_name`) whose bare calls the shadow guard checks.
_SHADOW_GUARDED = frozenset({"js", "python"})


def _bound_names(target: Node | None, src: bytes) -> Iterator[str]:
    """The names a binding target introduces: `x`, `x, (y, z)`, `{ a, b: c }`, `[d, ...e]`, `*args`, `c=1`."""
    if target is None or target.type in _NO_BINDING_TYPES:
        return
    if target.type in ("identifier", "shorthand_property_identifier_pattern"):
        yield _text(target, src)
    elif target.type == "pair_pattern":
        yield from _bound_names(_field(target, "value"), src)
    elif target.type in ("default_parameter", "typed_default_parameter"):
        yield from _bound_names(_field(target, "name"), src)
    elif target.type in ("assignment_pattern", "object_assignment_pattern"):
        yield from _bound_names(_field(target, "left"), src)
    elif target.type in ("required_parameter", "optional_parameter"):
        yield from _bound_names(_field(target, "pattern"), src)
    elif target.type == "typed_parameter":
        yield from _bound_names(target.named_children[0] if target.named_children else None, src)
    else:
        for child in target.named_children:
            if child.type not in ("type", "type_annotation"):
                yield from _bound_names(child, src)


def _scope_names(fn: Node, src: bytes, blocks: frozenset[str] = frozenset()) -> frozenset[str]:
    """The names *fn* binds itself (the shadow guard): its parameters, locals, loop, `with`,
    `except`/`catch` and comprehension targets, and destructured names. Not among them: a nested
    `def` (naming it is naming a real function), a name declared `global`/`nonlocal` (it is the
    outer scope's; rebinding it still binds it here), a nested class's body names (class
    scope, unseen by the class's own methods), and what a nested block of *blocks* (the grammar's
    `_BLOCK_SCOPES`) binds for itself."""
    params = set(_bound_names(_field(fn, "parameters"), src)) | set(_bound_names(_field(fn, "parameter"), src))
    return frozenset(params | _bound_in(fn.named_children, src, blocks))


def _bound_in(nodes: list[Node], src: bytes, blocks: frozenset[str] = frozenset()) -> set[str]:
    """The names *nodes* bind in the scope that holds them, nested scopes and class bodies left out. In a
    nested block of *blocks* (JS/TS), a `let`/`const`, a `for (const x of …)` variable and a `catch (e)` bind
    only there (`_block_names`, asked on the way up); a `var` there still binds the whole function, and so
    does a function value it holds (only at the function's own level is `const run = () => …` a function
    block of its own, shadowing nothing)."""
    names: set[str] = set()
    top = {(node.start_byte, node.type) for node in nodes}  # a function's own body block is its scope, not nested
    stack = [(node, False) for node in nodes]
    while stack:
        node, in_block = stack.pop()
        if node.type in _VALUE_SCOPE_TYPES or node.type == "class_definition":
            continue  # its names are its own, collected when a value inside it asks
        if in_block and node.type == "lexical_declaration":
            continue
        own = node.type in blocks and _binds_for_itself(node, src)
        if node.type == "as_pattern_target":
            names.update(_bound_names(node, src))
        elif node.type in _BINDING_FIELDS and not own and not (
                not in_block and node.type == "variable_declarator"
                and getattr(_field(node, "value"), "type", None) in _FUNCTION_VALUES):
            names.update(_bound_names(_field(node, _BINDING_FIELDS[node.type]), src))
        nested = in_block or (node.type in blocks and (node.start_byte, node.type) not in top)
        held = _field(node, _BINDING_FIELDS[node.type]) if own else None
        stack.extend((child, nested) for child in node.named_children
                     if held is None or (child.start_byte, child.type) != (held.start_byte, held.type))
    return names

# Per grammar (`import_handler_name`), the nodes a `let`/`const` inside binds only for: a nested `{ }`, a
# `for`/`for…of` header with its body, a `catch` clause (its parameter too) and a `switch` body.
_BLOCK_SCOPES = {"js": frozenset({"statement_block", "for_statement", "for_in_statement", "catch_clause",
                                  "switch_body"})}


def _binds_for_itself(node: Node, src: bytes) -> bool:
    """`for (const x of xs)` / `for (let k in o)` and `catch (e)`: the loop variable or the caught error is the
    block's alone (a `for (var x of xs)` is the function's)."""
    if node.type == "catch_clause":
        return True
    kind = _field(node, "kind") if node.type == "for_in_statement" else None
    return kind is not None and _text(kind, src) in ("let", "const")


def _block_names(block: Node, src: bytes) -> frozenset[str]:
    """The names a JS/TS block binds for itself: `let`/`const` declarations directly in a `{ }` (function values
    too) or a `switch`'s cases, a `for (let i …)` initializer, a `for (const x of …)` variable, a `catch (e)`."""
    if _binds_for_itself(block, src):
        return frozenset(_bound_names(_field(block, _BINDING_FIELDS[block.type]), src))
    if block.type == "for_statement":
        held = [_field(block, "initializer")]
    elif block.type == "switch_body":
        held = [stmt for case in block.named_children for stmt in case.named_children]
    else:
        held = block.named_children
    return frozenset(name for decl in held if decl is not None and decl.type == "lexical_declaration"
                     for d in decl.named_children if d.type == "variable_declarator"
                     for name in _bound_names(_field(d, "name"), src))


def _class_bindings(cls: Node, src: bytes) -> dict[str, int]:
    """Name -> where the first class-body statement binding it ends. A class body runs top to bottom, so
    a use reads the class's name only after that statement: `x = x` and `b = property(b)` read the
    outer `x` and the `def b` above."""
    body = _field(cls, "body")
    first: dict[str, int] = {}
    for stmt in body.named_children if body is not None else ():
        for name in _bound_in([stmt], src):
            first.setdefault(name, stmt.end_byte)
    return first


def _collect_type_table_java(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """`Owner owner`, as a field, a parameter or a local: calls on `owner` are on Owner."""
    for node in _nodes_of_types(root, cfg, ("field_declaration", "local_variable_declaration", "formal_parameter", "spread_parameter")):
        if node.type in ("field_declaration", "local_variable_declaration", "formal_parameter",
                         "spread_parameter"):
            type_node = _field(node, "type")
            if type_node is not None:
                type_name = _java_type_name(type_node, src)
                names = ([_field(node, "name")] if node.type != "field_declaration"
                         and node.type != "local_variable_declaration"
                         else [_field(d, "name") for d in node.named_children
                               if d.type == "variable_declarator"])
                for name in names:
                    if name is not None and type_name[:1].isupper():
                        table.setdefault(_text(name, src), type_name)


_GO_BUILTIN_TYPES = frozenset({
    "any", "bool", "byte", "comparable", "complex64", "complex128", "error", "float32", "map",
    "float64", "int", "int8", "int16", "int32", "int64", "rune", "string", "uint", "uint8",
    "uint16", "uint32", "uint64", "uintptr",
})


def _go_type_name(node: Node, src: bytes) -> str:
    """`*Context` -> `Context`, `[]gin.Engine` -> `gin.Engine`, `Box[T]` -> `Box`.

    The package qualifier is kept: `openapi.Client` and this package's `Client`
    are different types.
    """
    return _text(node, src).lstrip("*&[]").split("[")[0]


def _collect_type_table_go(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """A method's receiver, parameters and typed `var`s: `c.Render()` in
    `func (c *Context) JSON()` is Context.Render. A name bound to two types in
    one file is dropped rather than guessed."""
    seen: dict[str, set[str]] = {}
    for node in _nodes_of_types(root, cfg, ("parameter_declaration", "var_spec")):
        if node.type in ("parameter_declaration", "var_spec"):
            type_node = _field(node, "type")
            if type_node is not None:
                type_name = _go_type_name(type_node, src)
                for child in node.named_children:
                    # A named type, exported or not; `map[..]`, `func(..)` and builtins are skipped.
                    if (child.type == "identifier" and type_name not in _GO_BUILTIN_TYPES
                            and all(part.isidentifier() for part in type_name.split("."))):
                        seen.setdefault(_text(child, src), set()).add(type_name)
    for name, types in seen.items():
        if len(types) == 1:
            table.setdefault(name, next(iter(types)))


_TYPE_TABLE_HANDLERS = _with_languages({
    "python": _collect_type_table_python,
    "js": _collect_type_table_js,
    "ts": _collect_type_table_ts,
    "java": _collect_type_table_java,
    "go": _collect_type_table_go,
}, languages.TYPE_TABLE_HANDLERS, "TYPE_TABLE_HANDLERS")
# Type-table handlers whose receivers are typed by their own function's table before the file's.
_FUNCTION_SCOPES = {"go": ("function_declaration", "method_declaration"), "cpp": ("function_definition",)}


# --------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------

def _callee_info(
    node: Node, src: bytes, cfg: LanguageConfig,
) -> tuple[str | None, bool, str | None, str | None]:
    """Return (name, is_member, receiver, qualified_prefix) for a call node."""
    own = languages.CALLEE_HANDLERS.get(cfg.name)
    if own is not None:
        return own(node, src, cfg)
    if cfg.import_handler_name not in _REFERENCE_CALLEE_HANDLERS:
        return _generic_callee(node, src, cfg)
    target = _field(node, cfg.call_function_field) or _field(node, "constructor")
    if target is None:
        return None, False, None, None
    return _reference_info(target, src, cfg)


_IDENTIFIER_TYPES = frozenset({
    "identifier", "simple_identifier", "name", "type_identifier", "field_identifier",
    "property_identifier", "constant",
})
_SEPARATORS = (".", "->", "::", ":", "?.", "!.")
# C++ `f<T>(x)`, `obj.template get<T>()`, `Box<int>::make()`: the arguments name types, never the callee.
_TEMPLATE_ARGS = "template_argument_list"


def _identifier_leaves(node: Node) -> list[Node]:
    stack, out = [node], []
    while stack:
        current = stack.pop()
        if current.type in _IDENTIFIER_TYPES and not current.named_children:
            out.append(current)
        stack.extend(reversed([c for c in current.named_children if c.type != _TEMPLATE_ARGS]))
    return out


def _untemplated(target: Node, end: int, src: bytes) -> str:
    """*target*'s source up to *end* without its C++ template arguments or a `template` disambiguator:
    `ns::Box<int>::` -> `ns::Box::`, `obj.template ` -> `obj.`."""
    skipped = sorted((n.start_byte, n.end_byte) for n in _walk_types(target) if n.type == _TEMPLATE_ARGS)
    out, at = [], target.start_byte
    for start, stop in skipped:
        if at <= start and stop <= end:
            out.append(src[at:start])
            at = stop
    out.append(src[at:end])
    text = b"".join(out).decode("utf-8", "replace").rstrip()
    return re.sub(r"\btemplate$", "", text).rstrip() if skipped else text


def _arrow_call(node: Node) -> bool:
    """`p->f()`: a C++ call whose callee is a member read through `->`."""
    callee = _field(node, "function")
    operator = _field(callee, "operator") if callee is not None and callee.type == "field_expression" else None
    return operator is not None and operator.type == "->"


# C++ constructions with no call node: `Conn c(args);` and a constructor's `: Base(args)` initializer.
_CONSTRUCTIONS = frozenset({"init_declarator", "field_initializer"})


def _constructed_type(node: Node) -> Node | None:
    """What a C++ construction names: the declared type of `Conn c(args)` (not of `Conn c = x` or `int n(1)`,
    which name no class), or the base or member a `: Base(args)` initializer starts."""
    if node.type == "field_initializer":
        return node.named_children[0] if node.named_children else None
    value, declaration = _field(node, "value"), node.parent
    if value is None or value.type != "argument_list" or declaration is None or declaration.type != "declaration":
        return None
    return _field(declaration, "type")


def _generic_callee(
    node: Node, src: bytes, cfg: LanguageConfig,
) -> tuple[str | None, bool, str | None, str | None]:
    """The callee of a call in a grammar with no import handler (P-3).

    These languages get calls only, resolved by name: the same file first, a
    repo-unique name after, always INFERRED. The callee is whatever the call
    names -- a field, the first child -- reduced to its last identifier, with
    what precedes it as the receiver: `pkg.Run`, `obj->run`, `ns::run`, `m:run`.
    """
    named = _field(node, cfg.call_name_field) if cfg.call_name_field else None
    target = _constructed_type(node) if node.type in _CONSTRUCTIONS else (
        named or _field(node, cfg.call_function_field) or _field(node, "type")
        or _field(node, "constructor")
        or (node.named_children[0] if node.named_children else None))
    if target is None:
        return None, False, None, None
    if target.type in _IDENTIFIER_TYPES:
        receiver = next((r for f in cfg.call_receiver_fields if (r := _field(node, f)) is not None), None)
        if receiver is None:
            return _text(target, src), False, None, None
        qualifier = _text(receiver, src)
        return _text(target, src), True, qualifier.rsplit(".", 1)[-1], qualifier
    leaves = _identifier_leaves(target)
    if not leaves:
        return None, False, None, None
    # `List<Order>` names List, not its argument.
    leaf = leaves[0] if "generic" in target.type else leaves[-1]
    qualifier = _untemplated(target, leaf.start_byte, src)
    for sep in _SEPARATORS:
        if qualifier.endswith(sep):
            qualifier = qualifier[: -len(sep)].rstrip()
            break
    if not qualifier:
        return _text(leaf, src), False, None, None
    return _text(leaf, src), True, re.split(r"\.|->|::|:", qualifier)[-1], qualifier


def _reference_info(
    target: Node, src: bytes, cfg: LanguageConfig,
) -> tuple[str | None, bool, str | None, str | None]:
    """(name, is_member, receiver, qualified_prefix) for what a call, a decorator
    or a JSX tag names: `f`, `obj.f`, `pkg.mod.f`, and the `f` of an object's `{ f }`."""
    if target.type in ("identifier", "type_identifier", "shorthand_property_identifier"):
        return _text(target, src), False, None, None

    if target.type in cfg.accessor_types:
        prop = _field(target, cfg.accessor_property_field)
        obj = _field(target, cfg.accessor_object_field)
        if obj is not None and obj.type == "non_null_expression" and obj.named_children:
            obj = obj.named_children[0]  # `e!.go()`: the assertion keeps e's type
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
# Functions as values (INDIRECT_CALL)
# --------------------------------------------------------------------------

# A node that holds values (Python and JS/TS node types), and the position they are in.
_VALUE_CONTEXTS = {
    "argument_list": "argument", "arguments": "argument",
    "dictionary": "collection", "list": "collection", "tuple": "collection", "set": "collection",
    "object": "collection", "array": "collection",
    "assignment": "assignment", "assignment_expression": "assignment", "variable_declarator": "assignment",
    "return_statement": "return",
    # `<Button onClick={handle} />`: an attribute's value, handed to the component.
    "jsx_expression": "prop",
}
# Where an assignment keeps its one value; any other holder's values are its children.
_VALUE_FIELDS = {"assignment": "right", "assignment_expression": "right", "variable_declarator": "value"}
_TABLE_TYPES = frozenset({"dictionary", "list", "tuple", "set", "object", "array"})
_SUBSCRIPT_OBJECT_FIELDS = {"subscript": "value", "subscript_expression": "object"}
# Only a bound method is a value reference through a receiver; `utils.parse` passed along is not resolvable here.
_SELF_RECEIVERS = frozenset({"self", "this"})
# Decorators (their last segment) that make a method read like an attribute. A setter shares the
# property's name, so `self.x` may resolve to either.
_ACCESSOR_DECORATORS = frozenset({"property", "cached_property", "setter", "getter", "deleter"})


def _values_of(holder: Node) -> list[Node]:
    """The value nodes a holder holds: `f` of `g(f)`, `{"k": f}`, `[f]`, `x = f`, `return f`."""
    field_name = _VALUE_FIELDS.get(holder.type)
    if field_name:
        value = _field(holder, field_name)
        return [value] if value is not None else []
    values = (_field(c, "value") if c.type in ("pair", "keyword_argument") else c for c in holder.named_children)
    return [v for v in values if v is not None]


def _is_accessor(def_node: Node | None, decorators: tuple[str, ...]) -> bool:
    """A method read like an attribute, `@property`/`cached_property` or a `get`/`set` accessor:
    `self.text` runs it there and then, nothing is handed over (a setter shares its getter's name)."""
    if any(d.lstrip("@").strip().rsplit(".", 1)[-1] in _ACCESSOR_DECORATORS for d in decorators):
        return True
    return def_node is not None and def_node.type == "method_definition" \
        and any(c.type in ("get", "set") for c in def_node.children)


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
    # The statement namespace (`namespace X;`) above the span, prefixed to its chain.
    namespace: tuple[str, ...] = ()
    owns_facts: bool = True  # ParsedSymbol.owns_facts


# Filler kinds that absorb an adjacent run of the same kind.
_COALESCING = frozenset({"imports", "statements", "comment"})
# The clauses of a branch statement that carry blocks of their own.
_BRANCH_CLAUSES = frozenset({
    "elif_clause", "else_clause", "except_clause", "except_group_clause", "finally_clause",
})

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
        self._scope_tables: dict[int, dict[str, str]] = {}
        # Macro invocation start byte -> its arguments parsed as code (languages.MACRO_ARGUMENT_HANDLERS).
        self._macro_arguments: dict[int, Node] = {}
        # Go has no heritage, but its embeds promote methods: facts for lookup, never an edge, so its
        # config keeps no heritage handler and coverage still says it has no heritage edges.
        self.heritage_handler = _HERITAGE_HANDLERS.get(cfg.heritage_handler_name) or (
            _heritage_go if cfg.name == "go" else None)
        # Grammars whose bare calls are checked against the names their scopes bind (`_shadowed`).
        self._shadow_guard = cfg.import_handler_name in _SHADOW_GUARDED
        # Python keeps them: an untyped receiver there is as often a repo object (`qs.count()`, `cache.get()`)
        # whose type the builder can still work out, and it guesses no further.
        self.builtin_methods = _BUILTIN_METHODS.get(cfg.name, frozenset()) if cfg.name != "python" else frozenset()
        # Ids of the member calls in `builtin_methods` whose receiver the enclosing scope binds: never an import.
        self.local_receivers: set[int] = set()
        self._names_by_scope: dict[tuple[int, str], frozenset[str]] = {}
        self._block_scopes = _BLOCK_SCOPES.get(cfg.import_handler_name, frozenset())
        self._all_bound: frozenset[str] | None = None
        self._exported_names: frozenset[str] | None = None
        self._root: Node | None = None
        self._class_names: dict[int, dict[str, int]] = {}
        # name -> values of the file's module-level tables (`COMMANDS = {...}`), read by `TABLE[k]()`.
        self._module_tables: dict[str, list[Node]] | None = None
        # Python: each scope's own typed bindings, its untyped `x = <value>` (None: two values), and the chains
        # already read off them.
        self._py_scopes: dict[int | None, dict[str, str]] | None = None
        self._py_assigned: dict[tuple[int | None, str], Node | None] = {}
        self._py_chains: dict[tuple[int | None, str], str | None] = {}
        self._py_function_names: frozenset[str] = frozenset()
        self._py_classes: dict[str, Node] | None = None

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
        self._root = root
        # Built before the walk so a call can resolve its receiver's type as it
        # is emitted: a variable is often declared below its first use.
        handler = _TYPE_TABLE_HANDLERS.get(self.cfg.type_table_handler_name)
        if handler:
            handler(root, self.src, self.out.type_table, self.cfg)
        arguments = languages.MACRO_ARGUMENT_HANDLERS.get(self.cfg.name)
        if arguments:
            self._macro_arguments = arguments(root, self.src, _get_parser(self.cfg).parse, self.cfg)
        macros = languages.MACRO_TABLE_HANDLERS.get(self.cfg.macro_table_handler_name)
        if macros:
            self.out.macros_dropped = macros(root, self.src, self.out.macro_table, self.cfg)
        if self.cfg.type_table_handler_name == "python":
            self._python_return_table(root)
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
            self._emit(span, (*chain, *span.namespace), parent_idx, in_type=in_type)

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
        namespace: tuple[str, ...] = ()
        for child in self._flatten(members_parent.named_children if members_parent is not None else []):
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
            if child.type in cfg.module_types and span.name:
                # `App\Models` is `App.Models`, as the namespace's classes are named.
                span.name = span.name.replace("\\", ".")
            if child.type in cfg.statement_namespace_types and _body_of(child, cfg) is None:
                # `namespace X;` owns no body: it names everything after it, up to the next one.
                namespace = (span.name,) if span.name else ()
            else:
                span.namespace = namespace

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

    def _flatten(self, nodes: list[Node]) -> list[Node]:
        """``nodes`` with each transparent node (``#ifndef``, ``#if``/``#else``, ``extern "C"``)
        replaced by its own members, in source order. Its condition, guard name or
        linkage string is never a member; the branch text between members is tiled
        as before. An include-guarded header had no symbols at all."""
        transparent = self.cfg.transparent_types
        if not transparent:
            return list(nodes)
        out: list[Node] = []
        for node in nodes:
            if node.type not in transparent:
                out.append(node)
                continue
            skip = {(n.start_byte, n.type) for f in _TRANSPARENT_NON_MEMBERS if (n := _field(node, f)) is not None}
            members: list[Node] = []
            for child in node.named_children:
                if (child.start_byte, child.type) in skip:
                    continue
                if node.type == "linkage_specification" and child.type == "declaration_list":
                    members.extend(child.named_children)  # extern "C" { ... }: the braces' contents
                else:
                    members.append(child)
            out.extend(self._flatten(members))
        return out

    def _c_declaration(self, child: Node) -> _Span | None:
        """C/C++ declarations that define something, or that a compiler index never names.

        - a member declared with its type's body (`class Inner {...};` in a class) defines that type;
        - a pure virtual member (`virtual void f() = 0;`) is the method's only definition;
        - any other member prototype (`void f(int);`) is no symbol: its definition holds the body,
          and calls land there, not on a field named like it;
        - `T Cls::member(...)` at namespace scope defines a static data member of `Cls`.
        None for everything else, which is classified as before.
        """
        span = None
        decl = _field(child, "declarator")
        spec = _field(child, "type")
        if spec is not None and _field(spec, "body") is not None and self._kind_for(spec.type, in_type=True):
            span = _Span(child.start_byte, child.end_byte, "definition", nodes=[child],
                         def_node=spec, name=_name_of(spec, self.src, self.cfg))
        elif child.type == "field_declaration" and decl is not None and decl.type == "function_declarator" \
                and _field(decl, "declarator") is not None and _field(decl, "declarator").type in _NAME_NODE_TYPES:
            default = _field(child, "default_value")
            if default is not None and _text(default, self.src).strip() == "0":
                span = _Span(child.start_byte, child.end_byte, "definition", nodes=[child], def_node=child,
                             def_kind="method", name=_name_via_declarator(child, self.src))
            else:
                span = _Span(child.start_byte, child.end_byte, "statements", nodes=[child])
        elif child.type == "declaration" and decl is not None:
            target = _field(decl, "declarator") if decl.type == "init_declarator" else decl
            if target is not None and target.type == "qualified_identifier":
                span = _Span(child.start_byte, child.end_byte, "definition", nodes=[child], def_node=child,
                             def_kind="field", name=_qualified_parts(target, self.src)[-1])
        return span

    def _macro_namespace(self, child: Node) -> _Span | None:
        """`ABSL_NAMESPACE_BEGIN` on the line before `namespace x {`: with the macro unexpanded, tree-sitter reads
        a function named `namespace` (its name an ERROR node), so its definitions lost their namespace."""
        decl = _field(child, "declarator")
        if decl is None or decl.type != "identifier" or _text(decl, self.src) != "namespace":
            return None
        named = next((c for c in child.named_children if c.type == "ERROR"), None)
        name = _text(named, self.src).strip() if named is not None else ""
        return _Span(child.start_byte, child.end_byte, "definition", nodes=[child], def_node=child,
                     def_kind="module", name=name if re.fullmatch(r"\w+", name) else None)

    def _classify(self, child: Node, *, in_container: bool, in_type: bool) -> _Span | None:
        cfg = self.cfg
        ntype = child.type

        if cfg.unwrap_declarator and ntype in ("field_declaration", "declaration"):
            span = self._c_declaration(child)
            if span is not None:
                return span
        if cfg.unwrap_declarator and ntype == "function_definition":
            span = self._macro_namespace(child)
            if span is not None:
                return span

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
                bound = self._as_bound_function(candidate) or self._as_module_value(child, candidate)
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

        value = self._as_module_value(child, child)
        if value is not None and value.name in self._listed_exports():
            return value

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

    def _as_module_value(self, statement: Node, declaration: Node) -> _Span | None:
        """`export const REVISION = '187'`: a JS/TS module-level value another file imports by name, so a
        definition (a field) like a function is. Only an exported one: an internal constant is reached by no
        import, and each node is a vector. *statement* is the top-level statement holding *declaration*
        (itself, or its `export`)."""
        if declaration.type not in self.cfg.binding_types or statement.parent is None \
                or statement.parent.type != "program":
            return None
        declarators = [c for c in declaration.named_children if c.type == "variable_declarator"]
        # shortcut: `export const a = 1, b = 2` and destructuring (`export const { a } = o`) give no node;
        # split the statement into one span per name if a repo exports that way.
        if len(declarators) != 1:
            return None
        name_node = _field(declarators[0], "name")
        if name_node is None or name_node.type != "identifier":
            return None
        return _Span(statement.start_byte, statement.end_byte, "definition", nodes=[statement],
                     def_node=declarators[0], def_kind="field", name=_text(name_node, self.src), owns_facts=False)

    def _listed_exports(self) -> frozenset[str]:
        """The names `export { a, b as c }` and `export default a` export from this module's own bindings."""
        if self._exported_names is None:
            names: set[str] = set()
            for statement in self._root.named_children if self._root is not None else ():
                if statement.type not in self.cfg.export_types or _field(statement, "source") is not None:
                    continue
                for child in statement.named_children:
                    if child.type == "export_clause":
                        names.update(_text(name, self.src) for spec in child.named_children
                                     if (name := _field(spec, "name")) is not None)
                    elif child.type == "identifier":
                        names.add(_text(child, self.src))
            self._exported_names = frozenset(names)
        return self._exported_names

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
            owner = _owner_of(span.def_node, self.src)
            if owner:
                chain = (*chain, *owner)
                if kind == "function":
                    kind = "method"

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
            modifiers=self._modifiers(span, kind) if span.kind == "definition" else (),
            is_exported=span.is_exported,
            parent=parent_idx,
            is_accessor=kind == "method" and _is_accessor(span.def_node, span.decorators),
            type_param_count=_type_param_count(span.def_node) if span.kind == "definition" else 0,
            owns_facts=span.owns_facts,
        ))

        if span.kind != "definition":
            for node in span.nodes:
                if node.type in cfg.import_types or node.type in cfg.export_types:
                    self._run_handler(node, idx)
                elif node.type in cfg.branch_types and parent_idx is None:
                    self._emit_branches(node, idx, chain)
                else:
                    self._facts_of(node, idx)
            return

        if span.nodes and span.nodes[0].type in cfg.decorator_wrapper_types:
            self._emit_decorators(span.nodes[0], idx)

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
            for init in (c for c in span.def_node.named_children if c.type == "field_initializer_list"):
                self._facts_of(init, idx)  # a C++ constructor's `: Base(x), m_(make())` runs before its body
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
        # An expression-bodied arrow (`(id) => fetchUser(id)`) has the call as
        # its body, and the loop below only sees the call's children.
        if body.type in cfg.call_types:
            self._emit_call(body, enclosing)
        for child in self._flatten(body.named_children):
            span = self._classify(child, in_container=in_type, in_type=in_type)
            if span is not None and span.kind == "definition":
                self._emit(span, chain, enclosing, in_type=in_type)
                continue
            if child.type in cfg.import_types or child.type in cfg.export_types:
                self._run_handler(child, enclosing)
            else:
                self._facts_of(child, enclosing)

    def _emit_branches(self, node: Node, enclosing: int, chain: tuple[str, ...]) -> None:
        """Definitions in the blocks of a module-level `if`/`try` (P-3).

        `try: import ujson as json / except ImportError: def loads(...)` and
        `if TYPE_CHECKING:` define module symbols the rest of the repo calls;
        read as plain statements, they were never symbols at all. They hang off
        the statement's block, keeping their module-level names.
        """
        for child in node.named_children:
            if child.type == "block":
                self._emit_nested(child, enclosing, chain, in_type=False)
            elif child.type in _BRANCH_CLAUSES:
                self._emit_branches(child, enclosing, chain)
            else:
                if child.type in self.cfg.call_types:
                    self._emit_call(child, enclosing)
                self._walk_facts(child, enclosing)

    def _emit_decorators(self, wrapper: Node, enclosing: int) -> None:
        """A decorator calls its function: `@login_required` as much as `@app.route(...)` (P-5)."""
        cfg = self.cfg
        for child in wrapper.named_children:
            if child.type not in cfg.attached_types or child.type in COMMENT_NODE_TYPES:
                continue
            expr = child.named_children[0] if child.named_children else None
            if expr is None:
                continue
            if expr.type in cfg.call_types:
                self._emit_call(expr, enclosing)
                self._walk_facts(expr, enclosing)
            else:
                self._emit_reference(expr, enclosing)

    def _run_handler(self, node: Node, idx: int) -> None:
        cfg = self.cfg
        before = len(self.out.pending)
        if node.type in cfg.export_types:
            _exports_js(node, self.src, self.out.pending)
        elif self.import_handler:
            self.import_handler(node, self.src, self.out.pending)
        for fact in self.out.pending[before:]:
            fact.byte_offset = node.start_byte
            fact.from_symbol = idx
        # `export const store = createStore(reducer)` carries calls; only a
        # re-export (`export ... from`) has none.
        if node.type in cfg.export_types and _field(node, "source") is None:
            self._walk_facts(node, idx)

    def _facts_of(self, node: Node, enclosing: int) -> None:
        """Facts of *node* itself and everything inside it.

        A statement is usually wrapped (`expression_statement`), so its call is
        a descendant; in Ruby, Lua and Scala a body holds the call directly,
        and walking only the descendants missed it.
        """
        if node.type in self.cfg.call_types:
            self._emit_call(node, enclosing)
        self._walk_facts(node, enclosing)

    def _walk_facts(self, node: Node, enclosing: int) -> None:
        """Collect references inside *node*, all owned by *enclosing*.

        Nested definitions are not emitted here. Anything tiling did not reach
        sits inside a span that already covers its bytes, so emitting it would
        double-count them; its references belong to the block that holds it.

        Explicit stack, not recursion: a long `a + b + c + ...` or `f(f(f(...)))`
        chain nests one AST level per term, deeper than the interpreter's frame
        limit, and a RecursionError here fails the whole record. Children are
        pushed reversed so facts are still emitted in source order.
        """
        cfg = self.cfg
        # *node* itself can hold values: a function body's `return on_done` is walked from the statement.
        self._emit_value_refs(node, enclosing)
        stack = list(reversed(node.named_children))
        while stack:
            child = stack.pop()
            ntype = child.type
            if ntype in cfg.import_types or ntype in cfg.export_types:
                self._run_handler(child, enclosing)
                continue
            self._emit_value_refs(child, enclosing)
            if (arguments := self._macro_arguments.get(child.start_byte)) is not None and ntype == "macro_invocation":
                stack.extend(reversed(arguments.named_children))
                continue
            if ntype in cfg.call_types:
                self._emit_call(child, enclosing)
            elif ntype in cfg.jsx_element_types:
                tag = _field(child, "name") or (child.named_children[0] if child.named_children else None)
                # Lower-case tags are DOM elements; a component is capitalised.
                if tag is not None and _text(tag, self.src)[:1].isupper():
                    self._emit_reference(tag, enclosing)
            stack.extend(reversed(child.named_children))

    def _emit_call(self, node: Node, enclosing: int) -> None:
        if self.cfg.import_handler_name == "js" and self._emit_js_dynamic_import(node, enclosing):
            return
        name, is_member, receiver, qualifier = _callee_info(node, self.src, self.cfg)
        if not name:
            self._emit_dispatch(node, enclosing)
            return
        # Builtin globals only shadow bare calls; `this.fetch()` is a method.
        if not is_member and name in self.builtins:
            return
        # `task(ref)` on a parameter or local calls that value, never a module function named `task`.
        if not is_member and self._shadow_guard and self._shadowed(node, name):
            return
        fact = PendingEdgeFact(
            relation=Relation.CALLS,
            to_name=name,
            line=self._line_at(node.start_byte),
            byte_offset=node.start_byte,
            from_symbol=enclosing,
            to_kind="block",
            is_member_call=is_member,
            receiver=receiver,
            receiver_type=self._receiver_type(node, receiver, qualifier),
            qualified_prefix=qualifier,
            head_type=self._head_type(node, qualifier),
            head_type_arg=self._head_type(node, qualifier, suffix="<>"),
            context=self.cfg.construction_contexts.get(node.type),
            arrow=is_member and self.cfg.type_table_handler_name == "cpp" and _arrow_call(node),
        )
        self.out.pending.append(fact)
        # `function show(lib) { lib.get() }` calls the argument, not the imported module `lib`.
        if is_member and receiver and name in self.builtin_methods and self._shadow_guard \
                and self._shadowed(node, (qualifier or receiver).split(".")[0]):
            self.local_receivers.add(id(fact))

    def _head_type(self, node: Node, qualifier: str | None, suffix: str = "") -> str | None:
        """The function-scoped type of the variable a TS or C++ receiver chain starts from;
        *suffix* `<>` asks for its type argument instead (TS, and C++ `operator->`)."""
        if qualifier and self.cfg.type_table_handler_name == "python" and not suffix:
            head = _TS_HEAD.match(qualifier)
            if not head or head.group() in ("self", "cls"):
                return None
            func = _py_enclosing_function(node)
            if func is None or func.type != "function_definition":
                return None
            table = self._scope_tables.get(func.start_byte)
            if table is None:
                table = self._scope_tables[func.start_byte] = _py_local_values(func, self.src, self.cfg)
            return table.get(head.group())
        if qualifier and self.cfg.type_table_handler_name == "cpp":
            # `cfg.root()->child()`: `cfg`'s type (or `=initializer`) in its own function, for the builder; with
            # *suffix*, `x->open()` on `optional_ptr<Conn> x`: Conn, reached through optional_ptr's operator->.
            head = _TS_HEAD.match(qualifier)
            if suffix:
                return (self._function_scoped(node, qualifier + suffix) or self.out.type_table.get(qualifier + suffix)) \
                    if head and head.group() == qualifier and _arrow_call(node) else None
            return self._function_scoped(node, head.group()) \
                if head and head.group() not in ("this", qualifier) else None
        if not qualifier or self.cfg.type_table_handler_name != "ts":
            return None
        head = _TS_HEAD.match(qualifier)
        if not head or head.group() == "this" or self._ts_scoped_type(node, head.group()) is None:
            return None
        return self._ts_scoped_type(node, head.group() + suffix)

    def _function_scoped(self, node: Node, name: str) -> str | None:
        """*name*'s type in the function holding *node* (Go, C++), from that function's own type table."""
        function_types = _FUNCTION_SCOPES.get(self.cfg.type_table_handler_name)
        scope = node.parent if function_types else None
        while scope is not None and scope.type not in function_types:
            scope = scope.parent
        if scope is None:
            return None
        table = self._scope_tables.get(scope.start_byte)
        if table is None:
            table = self._scope_tables[scope.start_byte] = {}
            _TYPE_TABLE_HANDLERS[self.cfg.type_table_handler_name](scope, self.src, table, self.cfg)
        return table.get(name)

    def _receiver_type(self, node: Node, receiver: str | None, qualifier: str | None = None) -> str | None:
        """The receiver's type, from its own function first in Go and C++: one file's
        methods reuse `c` for `*Client` and `*Config`, which the file-wide table
        has to drop."""
        if not receiver:
            return None
        own = languages.RECEIVER_TYPE_HANDLERS.get(self.cfg.name)
        if own is not None:   # the file-wide guess as below: none for a chained receiver's `f()`
            chained = receiver.endswith(")") and qualifier != receiver
            return own(node, self.src, self._scope_tables, self.cfg,
                       None if chained else self.out.type_table.get(receiver))
        if self.cfg.type_table_handler_name == "python":  # its own chains too (`a.f().g()`)
            return self._python_receiver_type(node, receiver, qualifier)
        if receiver.endswith(")") and qualifier != receiver:
            # `a.f().g()` keeps only `f()`, the key of a bare `function f()`'s return type;
            # the builder evaluates the whole chain.
            return None
        scoped = self._function_scoped(node, receiver)
        if scoped is not None:
            return scoped
        if self.cfg.type_table_handler_name == "ts":
            scoped = self._ts_scoped_type(node, receiver)
            if scoped is not None:
                return scoped if not scoped.startswith("=") and not scoped.endswith("[]") else None
        found = self.out.type_table.get(receiver)
        # `=expr` initializers are evaluated by the builder, which can see other files.
        return found if found and not found.startswith("=") and not found.endswith("[]") else None

    def _python_receiver_type(self, node: Node, receiver: str, qualifier: str | None, depth: int = 0) -> str | None:
        """`self.x` from its own class's `self.x = Foo()` or class-level `x: Foo` (then a same-file base's); a
        bare `x` from its own functions' bindings, then the module's, unless a function on the way binds `x`
        untyped (a parameter or local shadows the module's `x`). A longer chain is not typed: its last name
        says nothing about it."""
        scopes = self._python_scopes(node)
        if receiver.endswith(")"):  # `make().run()`, `Foo(x).run()`: what the call gives
            return self._python_receiver_chain(node, depth)
        if qualifier and qualifier != receiver:
            head, _, attr = qualifier.partition(".")
            if head not in ("self", "cls") or "." in attr:
                return self._python_receiver_chain(node, depth)   # `self.a.b.run()`, `x.a.run()`, `User.objects.get()`
            for cls in self._python_class_chain(_python_owner_class(node)):
                own = scopes.get(cls.start_byte, {})
                if own.get(qualifier) or own.get(attr):
                    return own.get(qualifier) or own.get(attr)
            # A base elsewhere: by a typed parameter of its name, else the builder walks the bases (`=self.x`).
            return scopes.get(None, {}).get(qualifier) or self._python_receiver_chain(node, depth)
        module = scopes.get(None, {}).get(receiver) or self._python_assigned_type(None, receiver, depth)
        shadowed = False
        if receiver in self._py_function_names or module:  # else nothing binds it: skip the ancestor walk
            scope = node.parent
            while scope is not None and not shadowed:
                if scope.type in _PY_SCOPE_TYPES:
                    if receiver in scopes.get(scope.start_byte, {}):
                        return scopes[scope.start_byte][receiver]
                    assigned = self._python_assigned_type(scope.start_byte, receiver, depth)
                    if assigned:   # `conn = make_conn()`: what make_conn returns, wherever it is defined
                        return assigned
                    # `def f(client): client.get()` beside a module-level `client = Client()`: the argument's.
                    shadowed = (bool(module) and receiver in self._names_bound_by(scope)
                                and receiver not in _python_outer_names(scope, self.src))  # `global x`
                scope = scope.parent
        if module and not shadowed:
            return module
        # An untyped parameter of a test or a fixture: pytest hands it the fixture of its name, which may
        # live in a conftest.py, so only the builder can type it.
        fn = node.parent
        while fn is not None and fn.type != "function_definition":
            fn = fn.parent
        if fn is None or not _pytest_injected(fn, self.src):
            return None
        params = _field(fn, "parameters")
        named = {_text(c, self.src) for c in params.named_children if c.type == "identifier"} if params else set()
        if receiver not in named:
            return None
        # A fixture of that name in the test's own class overrides the module's and every conftest.py's.
        own = _python_class_fixture(_python_owner_class(fn), receiver, self.src)
        if own is not None:
            return _python_return_type(own, scopes, self.src)
        return f"{FIXTURE_MARK}{receiver}"

    def _python_name_type(self, node: Node, name: str, depth: int = 0) -> str | None:
        """*name*'s type (`x`, `self.x`) where *node* reads it, as a receiver so written would be typed."""
        return self._python_receiver_type(node, name.rpartition(".")[2], name, depth)

    def _python_receiver_chain(self, node: Node, depth: int = 0) -> str | None:
        """The type (or `=chain`) of a call's receiver expression, when *node* is a call `<receiver>.f()`."""
        fn = _field(node, "function") if node.type == "call" else None
        obj = _field(fn, "object") if fn is not None and fn.type == "attribute" else None
        return _as_type(_python_chain(obj, self.src, self._python_name_type, depth + 1)) if obj is not None else None

    def _python_assigned_type(self, key: int | None, name: str, depth: int = 0) -> str | None:
        """The type (or `=chain`) of *name*'s one untyped value in scope *key*; None for none or a cycle."""
        slot = (key, name)
        if slot not in self._py_chains:
            value = self._py_assigned.get(slot)
            self._py_chains[slot] = None   # `x = x.next()` reads itself: no type
            if value is not None:
                self._py_chains[slot] = _as_type(
                    _python_chain(value, self.src, self._python_name_type, depth + _PY_ASSIGNMENT_HOP_LINKS))
        return self._py_chains[slot]

    def _python_return_table(self, root: Node) -> None:
        """`f()` and `Cls.m()` keys: what each top-level function and each method returns, for a chain `=f()` or
        `=Cls.m()` the builder evaluates in a file calling it (two levels and more: a returned `g()` is itself
        such a chain)."""
        for name, fn in _python_module_functions(root, self.src).items():
            typ = self._python_returned(_field(fn, "definition") if fn.type == "decorated_definition" else fn)
            if typ:
                self.out.type_table.setdefault(f"{name}()", typ)
        scopes = self._python_scopes(root)
        for cls in _nodes_of_types(root, self.cfg, ("class_definition",)):
            owner, body = _field(cls, "name"), _field(cls, "body")
            if owner is None or body is None:
                continue
            owner_name = _text(owner, self.src)
            for child in body.named_children:
                fn = _field(child, "definition") if child.type == "decorated_definition" else child
                name = _field(fn, "name") if fn is not None and fn.type == "function_definition" else None
                typ = self._python_returned(fn) if name is not None else None
                if typ:
                    self.out.type_table.setdefault(f"{owner_name}.{_text(name, self.src)}()", typ)
                    if _PY_PROPERTY_DECORATORS & set(_decorators(fn, self.src)):   # `obj.prop` reads what it returns
                        self.out.type_table.setdefault(f"{owner_name}.{_text(name, self.src)}", typ)
            # `Cls.attr` keys: `self.attr = Foo()` / `attr: Foo` in its body, else the chain one value spells.
            assigned = (name for key, name in self._py_assigned if key == cls.start_byte)
            for name in (*scopes.get(cls.start_byte, {}), *assigned):
                attr = name.removeprefix("self.")
                typ = scopes.get(cls.start_byte, {}).get(name) or self._python_assigned_type(cls.start_byte, name)
                if attr.isidentifier() and typ:
                    self.out.type_table.setdefault(f"{owner_name}.{attr}", typ)

    def _python_returned(self, fn: Node) -> str | None:
        """What calling *fn* gives: its annotation's class, else the one type every `return v` agrees on (a `None`
        returned is no disagreement); none for a generator or a value the walker cannot type."""
        ann = _field(fn, "return_type")
        if ann is not None:
            text = _text(ann, self.src).strip()
            if text.split("[", 1)[0].rsplit(".", 1)[-1] in _GENERATOR_TYPES:
                return None
            typ = _annotated_type(text)
            return "=self" if typ == "Self" else typ or None
        found: set[str | None] = set()
        stack = list(fn.named_children)
        while stack:
            cur = stack.pop()
            if cur.type in _PY_SCOPE_TYPES or cur.type == "class_definition":
                continue
            if cur.type == "yield":
                return None
            if cur.type == "return_statement":
                value = cur.named_children[0] if cur.named_children else None
                if value is not None and value.type != "none":
                    found.add(_as_type(_python_chain(value, self.src, self._python_name_type)))
                continue
            stack.extend(cur.named_children)
        return found.pop() if len(found) == 1 else None

    def _names_bound_by(self, scope: Node) -> frozenset[str]:
        """*scope*'s own names, cached: a function's (`_scope_names`) or a block's (`_block_names`)."""
        key = (scope.start_byte, scope.type)
        names = self._names_by_scope.get(key)
        if names is None:
            names = self._names_by_scope[key] = (_scope_names(scope, self.src, self._block_scopes)
                                                 if scope.type in _VALUE_SCOPE_TYPES else _block_names(scope, self.src))
        return names

    def _python_class_chain(self, cls: Node | None) -> list[Node]:
        """*cls*, then the same-file classes it names as bases, nearest first (each once)."""
        if cls is None:
            return []
        if self._py_classes is None:
            root = cls
            while root.parent is not None:
                root = root.parent
            self._py_classes = {_text(_field(c, "name"), self.src): c
                                for c in _nodes_of_types(root, self.cfg, ("class_definition",))
                                if _field(c, "name") is not None}
        chain, queue = [], [cls]
        while queue:
            current = queue.pop(0)
            if any(current.start_byte == seen.start_byte for seen in chain):
                continue
            chain.append(current)
            bases = _field(current, "superclasses")
            queue += [self._py_classes[name] for b in (bases.named_children if bases is not None else ())
                      if (name := _text(b, self.src)) in self._py_classes]
        return chain

    def _python_scopes(self, node: Node) -> dict[int | None, dict[str, str]]:
        if self._py_scopes is None:
            root = node
            while root.parent is not None:
                root = root.parent
            self._py_scopes = _python_scoped_bindings(root, self.src, self.cfg, self._py_assigned)
            self._py_function_names = frozenset().union(
                *(table for key, table in self._py_scopes.items() if key is not None),
                (name for key, name in self._py_assigned if key is not None))
        return self._py_scopes

    def _bound_anywhere(self, node: Node) -> frozenset[str]:
        """Every name some function of the file binds: a name outside it is shadowed nowhere, which
        spares the ancestor walk (quadratic on `f(f(f(...)))` chains thousands deep)."""
        if self._all_bound is None:
            root = node
            while root.parent is not None:
                root = root.parent
            types = _PY_BINDING_SITES if self.cfg.import_handler_name == "python" else _JS_BINDING_SITES
            names: set[str] = set()
            for site in _nodes_of_types(root, self.cfg, types):
                if site.type in _BINDING_FIELDS:
                    # A function value too: in a nested block it shadows there (a superset only costs the walk).
                    names.update(_bound_names(_field(site, _BINDING_FIELDS[site.type]), self.src))
                elif site.type == "arrow_function":
                    names.update(_bound_names(_field(site, "parameter"), self.src))
                else:
                    names.update(_bound_names(site, self.src))
            self._all_bound = frozenset(names)
        return self._all_bound

    def _shadowed(self, node: Node, name: str) -> bool:
        """Whether a scope around *node* binds *name* itself, so it names no module function."""
        if name not in self._bound_anywhere(node):
            return False
        scope, in_function = node.parent, False
        while scope is not None:
            if scope.type == "class_definition":
                # A class body's names are seen from the body itself, never from a function inside it.
                if not in_function:
                    bindings = self._class_names.get(scope.start_byte)
                    if bindings is None:
                        bindings = self._class_names[scope.start_byte] = _class_bindings(scope, self.src)
                    if bindings.get(name, node.start_byte + 1) <= node.start_byte:
                        return True
            elif scope.type in _VALUE_SCOPE_TYPES or (
                    scope.type in self._block_scopes and scope.parent is not None
                    and scope.parent.type not in _VALUE_SCOPE_TYPES):  # a function's own body is the function's
                in_function = in_function or scope.type in _VALUE_SCOPE_TYPES
                if name in self._names_bound_by(scope):
                    return True
            scope = scope.parent
        return False

    def _ts_scoped_type(self, node: Node, name: str) -> str | None:
        """*name*'s type in the innermost function that declares it: one file reuses
        `provider` or `e` for different types, which a file-wide table cannot hold."""
        scope = node.parent
        while scope is not None:
            if scope.type in _TS_SCOPE_TYPES:
                table = self._scope_tables.get(scope.start_byte)
                if table is None:
                    table = self._scope_tables[scope.start_byte] = {}
                    _collect_type_table_ts(scope, self.src, table, self.cfg)
                if name in table:
                    return table[name]
            scope = scope.parent
        return None

    def _emit_reference(self, target: Node, enclosing: int, *, relation: str = Relation.CALLS,
                        context: str | None = None, at: Node | None = None) -> None:
        """A fact for something referenced without a call node: CALLS for a decorator or a JSX tag,
        INDIRECT_CALL for a function named as a value. *at* is where the reference happens when that
        is not *target*: `COMMANDS[k]()` reaches a table value written elsewhere."""
        name, is_member, receiver, qualifier = _reference_info(target, self.src, self.cfg)
        if not name or (not is_member and name in self.builtins):
            return
        at = at or target
        self.out.pending.append(PendingEdgeFact(
            relation=relation, to_name=name, line=self._line_at(at.start_byte),
            byte_offset=at.start_byte, from_symbol=enclosing, to_kind="block",
            is_member_call=is_member, receiver=receiver,
            receiver_type=self._receiver_type(target, receiver, qualifier),
            qualified_prefix=qualifier, context=context,
        ))

    def _emit_value_refs(self, holder: Node, enclosing: int) -> None:
        """INDIRECT_CALL facts for the functions *holder* holds as values: arguments, table
        entries, an assignment's right-hand side, a return value, a JSX prop. Only where the
        shadow guard runs: without it a local would read as a module function."""
        context = _VALUE_CONTEXTS.get(holder.type) if self._shadow_guard else None
        if context is None:
            return
        # `class A(Base)` has an argument list too; only a call's arguments are passed along.
        if context == "argument" and (holder.parent is None or holder.parent.type not in self.cfg.call_types):
            return
        # `{renderBody}` between tags is rendered, not handed over: only an attribute's value is a prop.
        if context == "prop" and (holder.parent is None or holder.parent.type != "jsx_attribute"):
            return
        for value in _values_of(holder):
            self._emit_value(value, enclosing, context)

    def _emit_value(self, value: Node, enclosing: int, context: str, at: Node | None = None) -> None:
        name, is_member, receiver, _ = _reference_info(value, self.src, self.cfg)
        if not name or (receiver not in _SELF_RECEIVERS if is_member else self._shadowed(value, name)):
            return
        self._emit_reference(value, enclosing, relation=Relation.INDIRECT_CALL, context=context, at=at)

    def _emit_dispatch(self, call: Node, enclosing: int) -> None:
        """`COMMANDS[name](ref)` runs one of a module table's values: an INDIRECT_CALL from the caller
        to each of them. Only a table this file defines at module level, under a name not rebound here."""
        target = _field(call, self.cfg.call_function_field) if self._shadow_guard else None
        object_field = _SUBSCRIPT_OBJECT_FIELDS.get(target.type) if target is not None else None
        table = _field(target, object_field) if object_field else None
        if table is None or table.type != "identifier":
            return
        name = _text(table, self.src)
        if self._shadowed(call, name):
            return
        for value in self._tables_of(call).get(name, ()):
            self._emit_value(value, enclosing, "dispatch", at=call)

    def _tables_of(self, node: Node) -> dict[str, list[Node]]:
        """The file's module-level tables, `NAME = {...}` / `const NAME = [...]`: name -> values."""
        if self._module_tables is None:
            root = node
            while root.parent is not None:
                root = root.parent
            self._module_tables = {}
            for top in root.named_children:
                statement = _field(top, "declaration") or top  # `export const`
                for binding in statement.named_children:
                    if binding.type not in _VALUE_FIELDS:
                        continue
                    name = _field(binding, _BINDING_FIELDS.get(binding.type, "left"))
                    value = _field(binding, _VALUE_FIELDS[binding.type])
                    if name is not None and name.type == "identifier" and value is not None \
                            and value.type in _TABLE_TYPES:
                        self._module_tables[_text(name, self.src)] = _values_of(value)
        return self._module_tables

    def _emit_js_dynamic_import(self, node: Node, enclosing: int) -> bool:
        """`require('./m')` and `import('./m')` as imports (P-5). True if it was one.

        `const x = require(...)` binds the module, `const {a, b: c} = require(...)`
        binds names from it -- the same facts an `import` statement records.
        """
        target = _field(node, self.cfg.call_function_field)
        if target is None or not (target.type == "import" or (
                target.type == "identifier" and _text(target, self.src) == "require")):
            return False
        args = _field(node, "arguments")
        spec = next((a for a in (args.named_children if args is not None else ())
                     if a.type == "string"), None)
        if spec is None:
            return True
        specifier = _text(spec, self.src).strip("'\"`")
        line = self._line_at(node.start_byte)
        module = PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=specifier, line=line,
                                 to_kind="record")
        facts = [module]
        parent = node.parent
        bound = _field(parent, "name") if parent is not None and parent.type == "variable_declarator" else None
        if bound is not None and bound.type == "identifier":
            module.local_name = _text(bound, self.src)
        elif bound is not None and bound.type == "object_pattern":
            for prop in bound.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    facts.append(PendingEdgeFact(
                        relation=Relation.IMPORTS, to_name=_text(prop, self.src), line=line,
                        to_kind="block", qualified_prefix=specifier))
                elif prop.type == "pair_pattern":
                    key, value = _field(prop, "key"), _field(prop, "value")
                    if key is not None:
                        facts.append(PendingEdgeFact(
                            relation=Relation.IMPORTS, to_name=_text(key, self.src), line=line,
                            to_kind="block", qualified_prefix=specifier,
                            local_name=_text(value, self.src) if value is not None else None))
        for fact in facts:
            fact.byte_offset = node.start_byte
            fact.from_symbol = enclosing
            # A file's dependency, wherever in it the call sits: an import
            # statement's facts are the file's too.
            fact.from_kind = "record"
            fact.pinned = True
        self.out.pending.extend(facts)
        return True

    def _modifiers(self, span: _Span, kind: str) -> tuple[str, ...]:
        """DECLARATION for a function or method with no body, in every language (an interface or abstract method,
        a C++ pure virtual, a TS overload signature): what runs is elsewhere. A body the span claimed as a sibling
        (Dart's `function_body`) is a body. Then the words of a language with a modifier handler (C/C++, C#); a
        language without one has no others."""
        def_node = span.def_node
        if def_node is None:
            return ()
        handler = languages.MODIFIER_HANDLERS.get(self.cfg.name)
        words = handler(def_node, self.src) if handler else ()
        trailing_body = any(node.type in self.cfg.trailing_body_types for node in span.nodes)
        if kind in _CALLABLE_KINDS and _body_of(def_node, self.cfg) is None and not trailing_body:
            words = (DECLARATION, *words)
        return words

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
    try:
        parser = _get_parser(cfg)
    except ImportError:
        return ParsedFile(language=cfg.name, skipped_reason="grammar_unavailable")
    tree = parser.parse(src)

    walker = _Walker(src, cfg)
    parsed = walker.walk(tree.root_node)

    # A partially-broken file still yields usable definitions above the break;
    # record where parsing degraded so callers can judge the result.
    if tree.root_node.has_error:
        parsed.parse_error_line = _first_error_line(tree.root_node)

    # A member call on a built-in container can never resolve -- resolution
    # needs a known receiver type and these receivers are language primitives
    # -- so keeping the fact would only bloat a traversed node (Python keeps them, `_Walker.builtin_methods`).
    # A receiver an import binds is no primitive: `axios.get(url)` is the library's own `get`.
    builtin_methods = walker.builtin_methods
    imported = {f.local_name or f.to_name for f in parsed.pending
                if f.relation in (Relation.IMPORTS, Relation.IMPORTS_FROM)}
    parsed.pending = [
        fact for fact in parsed.pending
        if not (
            fact.is_member_call
            and not fact.receiver_type
            and fact.receiver not in ("self", "this")
            and not (fact.receiver or "")[:1].isupper()  # `Controller.get(editor)`: a class
            and fact.receiver not in parsed.type_table
            and (fact.receiver not in imported or id(fact) in walker.local_receivers)
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
    symbols = parsed.symbols
    if not symbols:
        return
    # Spans nest properly, so the innermost span holding an offset is the last
    # one starting at or before it that still covers it, or an ancestor of that
    # one. Ordered outermost-first at a shared start (and, for identical spans,
    # lowest index last) so `enclosing` climbs parent-ward and ties resolve to
    # the lowest index, as the exhaustive scan did.
    order = sorted(
        range(len(symbols)),
        key=lambda i: (
            symbols[i].start_byte, symbols[i].start_byte - symbols[i].end_byte, -i,
        ),
    )
    starts = [symbols[i].start_byte for i in order]
    enclosing: list[int | None] = []
    stack: list[int] = []
    for pos, i in enumerate(order):
        start = symbols[i].start_byte
        while stack and symbols[order[stack[-1]]].end_byte <= start:
            stack.pop()
        enclosing.append(stack[-1] if stack else None)
        stack.append(pos)

    for fact in parsed.pending:
        if fact.pinned:
            continue
        pos: int | None = bisect.bisect_right(starts, fact.byte_offset) - 1
        if pos < 0:
            continue
        while pos is not None:
            sym = symbols[order[pos]]
            if sym.start_byte <= fact.byte_offset < sym.end_byte:
                # Only a top-level span owns no facts, so nothing encloses it: they are the file's.
                fact.from_symbol = order[pos] if sym.owns_facts else None
                fact.from_kind = "block"
                break
            pos = enclosing[pos]


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
