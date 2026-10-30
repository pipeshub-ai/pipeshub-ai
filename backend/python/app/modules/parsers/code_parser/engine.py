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
from app.modules.parsers.text_decoding import decode_text
from app.utils.env_config import env_int

if TYPE_CHECKING:
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


def _owner_of(node: Node, src: bytes) -> tuple[str, ...]:
    """The type a definition belongs to when the grammar says so outside its body.

    Go puts a method's type in its receiver (`func (s *Server) Run()`); C++
    defines a member out of line (`int Resizer::run() {}`). Without it the
    qualified name was `run`, not `Server.run`, and every edge into the
    method named the wrong symbol (P-3).
    """
    receiver = _field(node, "receiver")
    if receiver is not None:
        names = [c for c in _walk_types(receiver) if c.type == "type_identifier"]
        return (_text(names[0], src),) if names else ()
    cur = _field(node, "declarator")
    for _ in range(8):
        if cur is None:
            return ()
        if cur.type == "qualified_identifier":
            scope = _text(cur, src).rsplit("::", 1)[0]
            return tuple(part for part in scope.split("::") if part)
        cur = _field(cur, "declarator")
    return ()


def _walk_types(node: Node) -> list[Node]:
    stack, out = [node], []
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(reversed(current.named_children))
    return out


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
            out.append(PendingEdgeFact(
                relation=Relation.IMPORTS_FROM, to_name=module_name,
                line=line, to_kind="record",
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
                    to_kind="block", qualified_prefix=specifier,
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


_IMPORT_HANDLERS = {"python": _imports_python, "js": _imports_js, "java": _imports_java, "go": _imports_go}
_HERITAGE_HANDLERS = {"python": _heritage_python, "js": _heritage_js, "ts": _heritage_ts,
                      "java": _heritage_java}
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


_TYPE_TABLE_HANDLERS = {
    "python": _collect_type_table_python,
    "js": _collect_type_table_js,
    "ts": _collect_type_table_ts,
    "java": _collect_type_table_java,
    "go": _collect_type_table_go,
}


# --------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------

def _callee_info(
    node: Node, src: bytes, cfg: LanguageConfig,
) -> tuple[str | None, bool, str | None, str | None]:
    """Return (name, is_member, receiver, qualified_prefix) for a call node."""
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


def _identifier_leaves(node: Node) -> list[Node]:
    stack, out = [node], []
    while stack:
        current = stack.pop()
        if current.type in _IDENTIFIER_TYPES and not current.named_children:
            out.append(current)
        stack.extend(reversed(current.named_children))
    return out


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
    target = (named or _field(node, cfg.call_function_field) or _field(node, "type")
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
    qualifier = src[target.start_byte:leaf.start_byte].decode("utf-8", "replace").rstrip()
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
    or a JSX tag names: `f`, `obj.f`, `pkg.mod.f`."""
    if target.type in ("identifier", "type_identifier"):
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
            handler(root, self.src, self.out.type_table, self.cfg)
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
            is_exported=span.is_exported,
            parent=parent_idx,
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
        for child in body.named_children:
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
        stack = list(reversed(node.named_children))
        while stack:
            child = stack.pop()
            ntype = child.type
            if ntype in cfg.import_types or ntype in cfg.export_types:
                self._run_handler(child, enclosing)
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
        # Builtin globals only shadow bare calls; `this.fetch()` is a method.
        if not name or (not is_member and name in self.builtins):
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
            receiver_type=self._receiver_type(node, receiver, qualifier),
            qualified_prefix=qualifier,
            head_type=self._head_type(node, qualifier),
            head_type_arg=self._head_type(node, qualifier, suffix="<>"),
        ))

    def _head_type(self, node: Node, qualifier: str | None, suffix: str = "") -> str | None:
        """The function-scoped type of the variable a TS receiver chain starts from;
        *suffix* `<>` asks for its type argument instead."""
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
        if not qualifier or self.cfg.type_table_handler_name != "ts":
            return None
        head = _TS_HEAD.match(qualifier)
        if not head or head.group() == "this" or self._ts_scoped_type(node, head.group()) is None:
            return None
        return self._ts_scoped_type(node, head.group() + suffix)

    def _receiver_type(self, node: Node, receiver: str | None, qualifier: str | None = None) -> str | None:
        """The receiver's type, from its own function first in Go: one file's
        methods reuse `c` for `*Client` and `*Config`, which the file-wide table
        has to drop."""
        if not receiver:
            return None
        if receiver.endswith(")") and qualifier != receiver:
            # `a.f().g()` keeps only `f()`, the key of a bare `function f()`'s return type;
            # the builder evaluates the whole chain.
            return None
        if self.cfg.type_table_handler_name == "go":
            scope = node.parent
            while scope is not None and scope.type not in ("function_declaration", "method_declaration"):
                scope = scope.parent
            if scope is not None:
                table = self._scope_tables.get(scope.start_byte)
                if table is None:
                    table = self._scope_tables[scope.start_byte] = {}
                    _collect_type_table_go(scope, self.src, table, self.cfg)
                if receiver in table:
                    return table[receiver]
        if self.cfg.type_table_handler_name == "ts":
            scoped = self._ts_scoped_type(node, receiver)
            if scoped is not None:
                return scoped if not scoped.startswith("=") and not scoped.endswith("[]") else None
        found = self.out.type_table.get(receiver)
        # `=expr` initializers are evaluated by the builder, which can see other files.
        return found if found and not found.startswith("=") and not found.endswith("[]") else None

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

    def _emit_reference(self, target: Node, enclosing: int) -> None:
        """A CALLS fact for something referenced as a callee without a call node."""
        name, is_member, receiver, qualifier = _reference_info(target, self.src, self.cfg)
        if not name or (not is_member and name in self.builtins):
            return
        self.out.pending.append(PendingEdgeFact(
            relation=Relation.CALLS, to_name=name, line=self._line_at(target.start_byte),
            byte_offset=target.start_byte, from_symbol=enclosing, to_kind="block",
            is_member_call=is_member, receiver=receiver,
            receiver_type=self._receiver_type(target, receiver, qualifier),
            qualified_prefix=qualifier,
        ))

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

    parsed = _Walker(src, cfg).walk(tree.root_node)

    # A partially-broken file still yields usable definitions above the break;
    # record where parsing degraded so callers can judge the result.
    if tree.root_node.has_error:
        parsed.parse_error_line = _first_error_line(tree.root_node)

    # A member call on a built-in container can never resolve -- resolution
    # needs a known receiver type and these receivers are language primitives
    # -- so keeping the fact would only bloat a traversed node. Python keeps them:
    # an untyped receiver there is as often a repo object (`qs.count()`, `cache.get()`)
    # whose type the builder can still work out, and it guesses no further.
    builtin_methods = _BUILTIN_METHODS.get(cfg.name, frozenset()) if cfg.name != "python" else frozenset()
    parsed.pending = [
        fact for fact in parsed.pending
        if not (
            fact.is_member_call
            and not fact.receiver_type
            and fact.receiver not in ("self", "this")
            and not (fact.receiver or "")[:1].isupper()  # `Controller.get(editor)`: a class
            and fact.receiver not in parsed.type_table
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
                fact.from_symbol = order[pos]
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
