"""C and C++: `#include` directives, C++ base classes, a C++ type table, the function-like macros and `static`.

`#include "x.h"` and `#include <dir/x.h>` name a file. Both are kept as an
IMPORTS_FROM fact with no local name; the edge builder's C resolver
(`code_graph.resolvers.c_family`) finds the file in the repo, and a system
header (`<stdio.h>`) resolves to nothing.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.models import (
    DECLARATION,
    RETURNS,
    STATIC,
    PendingEdgeFact,
    Relation,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from app.modules.parsers.code_parser.lang_config import LanguageConfig

__all__ = ["cpp_modifiers", "dotted", "heritage", "in_std", "imports", "macro_table", "modifiers", "type_table"]

_BASE_TYPES = frozenset({"type_identifier", "qualified_identifier", "template_type"})
_SPECIFIERS = frozenset({"class_specifier", "struct_specifier", "union_specifier", "enum_specifier"})
# A smart pointer is used like what it points to: `std::shared_ptr<Conn> c; c->open()` calls Conn::open.
_POINTERS = frozenset({"shared_ptr", "unique_ptr", "weak_ptr"})
_TYPED = ("declaration", "field_declaration", "parameter_declaration", "optional_parameter_declaration")
_NAMES = frozenset({"identifier", "field_identifier"})
# A name called in a `#define` body, not a member (`p->f(`, `s.f(`) or a qualified (`ns::f(`) one.
_MACRO_CALL = re.compile(r"(?<![\w.$])(?<!->)(?<!::)([A-Za-z_]\w*)\s*\(")
# String and char literals and comments, blanked before calls are read: `printf("debug(): ")` calls no `debug`. A
# `//` comment runs on past a backslash-newline, as lines are spliced first; a `/* */` may span lines.
_LITERALS = re.compile(rb'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|/\*.*?\*/|//(?:\\\r?\n|[^\n])*', re.S)
_LINE_END = re.compile(rb"(?<!\\)(?<!\\\r)\n")   # a newline no backslash continues (`\` LF or `\` CR LF)
# shortcut: assumes a Linux build, revisit for Windows/macOS repos. A macro only a non-Linux platform compiles
# (`#ifdef _WIN32`, the `#else` of `#ifndef _WIN32`) is left out: `connect` -> `win32_connect` is wrong on Linux.
_PLATFORM = re.compile(r"\b(?:_WIN32|_WIN64|_MSC_VER|__MINGW\w*|__CYGWIN__|__APPLE__|__sun)\b")
_CONDITIONALS = frozenset({"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_elifdef"})
_NOT_CALLS = frozenset({
    "if", "while", "for", "switch", "return", "sizeof", "do", "else", "case", "defined", "typeof", "__typeof__",
    "alignof", "_Alignof", "alignas", "_Alignas", "decltype", "noexcept", "static_assert", "_Static_assert",
    "_Generic", "__attribute__", "__declspec", "__VA_ARGS__", "__VA_OPT__",
})
# The table rides on the file-summary node, read on every build (`_NODE_SIZE_WARN_BYTES` is 10 KB).
# shortcut: a file's macros are kept in file order up to 4 KB of JSON, and one calling more than 8 names is kept
# without them (no edge); rank by "calls a repo function" at edge-build time if a dropped macro is a wrapper.
_MAX_TABLE_BYTES = 4096
_MAX_MACRO_CALLS = 8


def imports(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`#include "x.h"` / `#include <a/x.h>`: the file it names, an angle include keeping its brackets (the
    resolver searches only include paths for it, never beside the includer). A macro-named include
    (`#include HDR`) names none."""
    if node.type != "preproc_include":
        return
    path = node.child_by_field_name("path")
    if path is None or path.type not in ("string_literal", "system_lib_string"):
        return
    written = src[path.start_byte:path.end_byte].decode("utf-8", errors="replace").strip()
    inner = written.strip('"<>').strip()
    specifier = f"<{inner}>" if path.type == "system_lib_string" and inner else inner
    if specifier:
        out.append(PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=specifier,
                                   line=node.start_point[0] + 1, to_kind="record"))


def _class_of(node: Node | None, src: bytes) -> str | None:
    """The class a declared C++ type is about: `Conn`, `ns::Conn`, `Holder<int>` and `std::shared_ptr<Conn>`
    (what it points to); a primitive or `auto` is none."""
    for _ in range(8):
        if node is None:
            return None
        if node.type == "type_identifier":
            return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if node.type == "qualified_identifier":
            node = node.child_by_field_name("name")
        elif node.type == "template_type":
            head = _class_of(node.child_by_field_name("name"), src)
            args = node.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            if head not in _POINTERS or first is None:
                return head
            node = first.child_by_field_name("type") if first.type == "type_descriptor" else first
        else:
            return None
    return None


def _template_arg(node: Node | None, src: bytes) -> str | None:
    """The class `optional_ptr<Conn>` (or `ns::ref<Conn>`) takes first; None for a std smart pointer, which
    `_class_of` already reads as what it points to, or a type with no class argument."""
    while node is not None and node.type == "qualified_identifier":
        node = node.child_by_field_name("name")
    if node is None or node.type != "template_type" or _class_of(node.child_by_field_name("name"), src) in _POINTERS:
        return None
    args = node.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    return _class_of(first.child_by_field_name("type") if first is not None and first.type == "type_descriptor"
                     else first, src)


def _constructed(value: Node | None, src: bytes) -> str | None:
    """What an `auto` variable holds: `new T(..)`, `std::make_shared<T>(..)`, `T::createShared(..)`."""
    if value is None:
        return None
    if value.type == "new_expression":
        return _class_of(value.child_by_field_name("type"), src)
    if value.type != "call_expression":
        return None
    fn = value.child_by_field_name("function")
    if fn is not None and fn.type == "qualified_identifier":
        name = fn.child_by_field_name("name")
        if name is not None and name.type == "template_function":   # std::make_shared<T>(...)
            args = name.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            return _class_of(first.child_by_field_name("type") if first is not None and first.type == "type_descriptor"
                             else first, src)
        return _class_of(fn.child_by_field_name("scope"), src)            # T::createShared(...)
    return None


def type_table(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """Variables, fields and parameters with their class (`Conn* c`, `const Conn& c`, `std::shared_ptr<Conn> c`,
    `auto c = Conn::createShared()`): calls on `c` are on Conn. A name declared with two classes in the file
    is left out, as the receiver is then unknown. A field is also kept as `Owner.field`, and an `auto` initialized
    from another expression as `=expr` (`.` for `->` and `::`), which the edge builder evaluates through field types and
    the RETURNS modifier of what it calls (`auto& c = DBConfig::GetConfig(ctx)`)."""
    from app.modules.parsers.code_parser.engine import _next_declarator, _nodes_of_types

    seen: dict[str, set[str]] = {}
    for node in _nodes_of_types(root, cfg, _TYPED):
        type_node = node.child_by_field_name("type")
        declared = _class_of(type_node, src)
        for declarator in node.children_by_field_name("declarator"):
            value = declarator.child_by_field_name("value") if declarator.type == "init_declarator" else None
            cur: Node | None = declarator
            function = False
            for _ in range(8):
                if cur is None or cur.type in _NAMES:
                    break
                function = function or cur.type == "function_declarator"
                cur = _next_declarator(cur)
            cls = declared or _constructed(value, src) or (
                _initializer(value, src) if type_node is not None and type_node.type == "placeholder_type_specifier"
                else None)
            if cur is not None and cur.type in _NAMES and cls:
                name = src[cur.start_byte:cur.end_byte].decode("utf-8", errors="replace")
                owner = _member_owner(node, src) if node.type == "field_declaration" and not function else None
                arg = _template_arg(type_node, src)
                # `Owner.field`, read for `m_list.f()` in an out-of-line method of Owner; `x<>`, the argument
                # of `optional_ptr<Conn> x`, which `x->open()` reaches through optional_ptr's operator->.
                for key in (name, f"{owner}.{name}" if owner else None):
                    if key:
                        seen.setdefault(key, set()).add(cls)
                        if arg:
                            seen.setdefault(f"{key}<>", set()).add(arg)
    table.update({name: kept for name, types in seen.items() if (kept := _one_type(types))})


def _one_type(types: set[str]) -> str | None:
    """A name's type when its declarations agree: its one class, which an `auto` initializer elsewhere does not
    unsettle (`Conn c;` here, `auto c = make()` there), else its one readable initializer, else `=` (a local of
    no readable type)."""
    classes = [t for t in types if not t.startswith("=")]
    if classes:
        return classes[0] if len(classes) == 1 else None
    readable = types - {"="}
    return next(iter(readable)) if len(readable) == 1 else "=" if not readable else None


# An `auto` initializer the builder can evaluate: a call or a member read (`Cls::get(x)`, `a.b().c`, `x`).
_EVALUATED = frozenset({"call_expression", "field_expression", "identifier", "qualified_identifier"})


def _initializer(value: Node | None, src: bytes) -> str | None:
    """`auto x = a->b(y).c` -> `=a.b(y).c`: what the edge builder evaluates for x's type; `=` alone for an initializer
    it cannot (`auto& c = *children[0]`), which still marks x a local that no field of the class types."""
    if value is None:
        return None
    return "=" + dotted(_text(value, src)) if value.type in _EVALUATED else "="


def in_std(qualifier: str | None) -> bool:
    """Whether a written qualifier is a path into the C++ standard library (`std::`, `::std::chrono::`)."""
    return bool(qualifier) and dotted(qualifier).lstrip(".").split(".", 1)[0] == "std"


def dotted(expression: str) -> str:
    """A C++ expression in the dotted form the edge builder walks: `a->template b<T>( x ).c` -> `a.b<T>(x).c`,
    `ns::Cls::get()` -> `ns.Cls.get()`."""
    text = re.sub(r"\btemplate\s+", "", expression)
    return re.sub(r"\s+", "", text).replace("->", ".").replace("::", ".")


def _member_owner(field: Node, src: bytes) -> str | None:
    """The class or struct whose body declares *field*, by its name without template arguments."""
    body = field.parent
    spec = body.parent if body is not None and body.type == "field_declaration_list" else None
    if spec is None or spec.type not in _SPECIFIERS:
        return None
    name = spec.child_by_field_name("name")
    if name is not None and name.type == "template_type":
        name = name.child_by_field_name("name")
    return _text(name, src) if name is not None and name.type == "type_identifier" else None


def macro_table(root: Node, src: bytes, table: dict[str, list], cfg: LanguageConfig) -> int:
    """Function-like `#define`s whose body calls a name: name -> [first line, the names it calls], a parameter
    called (`f(x)` with `f` a parameter) and member calls (`p->f(x)`) left out. A macro the file defines
    differently in two preprocessor branches (`memrev64(p)` on big-endian, `((void)0)` otherwise) is left out:
    which one compiles is not known; so is one only a non-Linux platform compiles. Returns how many macros the caps
    dropped or kept without their callees."""
    from app.modules.parsers.code_parser.engine import _nodes_of_types

    defined: dict[str, tuple[int, set[tuple[str, ...]]]] = {}
    blanked: bytes | None = None   # the source with literals and comments as spaces, offsets unchanged
    for node in sorted(_nodes_of_types(root, cfg, ("preproc_function_def",)), key=lambda n: n.start_byte):
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None or _platform_only(node, src):
            continue
        if blanked is None:
            blanked = _LITERALS.sub(lambda m: b" " * len(m.group()), src)
        params = node.child_by_field_name("parameters")
        skip = _NOT_CALLS | {_text(p, src) for p in (params.named_children if params is not None else ())}
        # Read to the end of the logical line: tree-sitter ends the value at a `/* */` inside it.
        start = value.start_byte if value is not None else (params or name).end_byte
        end = _LINE_END.search(blanked, start)
        body = blanked[start:end.start() if end else len(blanked)].decode("utf-8", errors="replace")
        calls = tuple(dict.fromkeys(c for c in _MACRO_CALL.findall(body) if c not in skip))
        defined.setdefault(_text(name, src), (node.start_point[0] + 1, set()))[1].add(calls)
    dropped, size = 0, 0
    for key, (line, bodies) in defined.items():
        calls = next(iter(bodies))
        if len(bodies) != 1 or not calls:
            continue
        # Over the name cap: kept without its callees, so a call through it is a macro reaching no one function
        # rather than an edge to whichever callee a cut list happened to keep.
        entry = [line, list(calls) if len(calls) <= _MAX_MACRO_CALLS else []]
        # A one-entry object's JSON is as long as the entry with its separator inside the table.
        cost = len(json.dumps({key: entry}))
        if size + cost > _MAX_TABLE_BYTES:
            dropped += 1
            continue
        size += cost
        dropped += not entry[1]
        table[key] = entry
    return dropped


def _platform_only(node: Node, src: bytes) -> bool:
    """Whether *node* sits in a preprocessor branch taken only when a non-Linux platform macro is defined: the
    body of `#ifdef _WIN32` / `#if defined(__APPLE__)`, or the `#else` of `#ifndef _WIN32` / `#if !defined(..)`."""
    child, parent = node, node.parent
    while parent is not None:
        if parent.type in _CONDITIONALS:
            test = parent.child_by_field_name("name") or parent.child_by_field_name("condition")
            text = _text(test, src) if test is not None else ""
            if _PLATFORM.search(text):
                negated = _text(parent.children[0], src) in ("#ifndef", "#elifndef") or text.lstrip().startswith("!")
                if negated == (parent.child_by_field_name("alternative") == child):
                    return True
        child, parent = parent, parent.parent
    return False


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def modifiers(def_node: Node, src: bytes) -> tuple[str, ...]:
    """`static` on a free function: internal linkage, callable only from its own translation unit. A static
    class member has external linkage like any member, so a definition inside a class body stays unmarked.
    A forward declaration (`class Conn;`) is a DECLARATION: the type is defined where its body is."""
    if def_node.type in _SPECIFIERS:
        return () if def_node.child_by_field_name("body") is not None else (DECLARATION,)
    parent = def_node.parent
    while parent is not None:
        if parent.type == "field_declaration_list":
            return ()
        parent = parent.parent
    static = any(c.type == "storage_class_specifier" and _text(c, src) == "static" for c in def_node.children)
    return (STATIC,) if static else ()


def cpp_modifiers(def_node: Node, src: bytes) -> tuple[str, ...]:
    """C's words, then RETURNS with the class a C++ function or method (or a method's in-class prototype) returns
    (`Conn* Pool::get()`: `returns:Conn`, a std smart pointer read as what it points to). A template parameter
    (`T& Holder<T>::get()`) is no class."""
    member = def_node.type == "field_declaration" and _declares_function(def_node.child_by_field_name("declarator"))
    function = def_node.type == "function_definition" and _declares_function(def_node.child_by_field_name("declarator"))
    returned = _class_of(def_node.child_by_field_name("type"), src) if function or member else None
    if returned and returned in _template_params(def_node, src):
        returned = None
    return (*modifiers(def_node, src), *((f"{RETURNS}{returned}",) if returned else ()))


def _declares_function(declarator: Node | None) -> bool:
    """Whether a member declarator declares a method (`Conn* get();`, `virtual A& f() = 0;`), not a field."""
    from app.modules.parsers.code_parser.engine import _next_declarator

    for _ in range(8):
        if declarator is None or declarator.type in _NAMES:
            return False
        if declarator.type == "function_declarator":
            return True
        declarator = _next_declarator(declarator)
    return False


def _template_params(node: Node, src: bytes) -> set[str]:
    """The type parameters of every template *node* is in (its own `template <class T>` and its class's)."""
    names: set[str] = set()
    cur: Node | None = node.parent
    while cur is not None:
        params = cur.child_by_field_name("parameters") if cur.type == "template_declaration" else None
        for param in params.named_children if params is not None else ():
            names.update(_text(n, src) for n in param.named_children if n.type == "type_identifier")
        cur = cur.parent
    return names


def heritage(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`class Sq : public Shape, private ns::Base<int>`: every base as INHERITS, named without its template
    arguments; a namespace qualifier is kept as the dotted prefix (`ns`)."""
    clause = next((c for c in node.named_children if c.type == "base_class_clause"), None)
    for base in clause.named_children if clause is not None else ():
        if base.type not in _BASE_TYPES:
            continue
        path = src[base.start_byte:base.end_byte].decode("utf-8", errors="replace").split("<")[0].strip()
        parts = [p.strip() for p in path.split("::") if p.strip()]
        if parts:
            out.append(PendingEdgeFact(relation=Relation.INHERITS, to_name=parts[-1], line=node.start_point[0] + 1,
                                       from_symbol=idx, qualified_prefix=".".join(parts[:-1]) or None))
