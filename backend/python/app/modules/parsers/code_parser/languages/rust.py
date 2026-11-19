"""Rust: `use` items, trait implementations and supertraits, and a type table.

`use a::b::{c, d as e};` imports the items c and d: each an IMPORTS_FROM to its
full path (the module file that defines it) and an IMPORTS of the item
qualified by that path, bound under its alias. Only items are imports (the
answer key's rule): a glob, a bare crate and a `mod x;` declaration give none.
"""
from __future__ import annotations

import bisect
import re
from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.models import PendingEdgeFact, Relation

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node, Tree

    from app.modules.parsers.code_parser.lang_config import LanguageConfig

__all__ = ["callee", "heritage", "imports", "macro_arguments", "receiver_type", "type_table"]

_PATHS = frozenset({"identifier", "scoped_identifier", "crate", "self", "super"})
# What a method call on one of these reaches, through deref or the `?` / `unwrap()` a chain takes: the wrapped type.
# Their methods are their own, the value inside reached through `unwrap()`, `?`, `borrow()`: marked `?T`.
_HOLDERS = frozenset({"Option", "Result", "RefCell", "Cell", "Mutex", "RwLock"})
# They deref to the value inside, so its methods are called on them directly, all but their own: marked `*T`.
_POINTERS = frozenset({"Box", "Rc", "Arc", "Cow", "Pin", "Ref", "RefMut", "MutexGuard", "RwLockReadGuard",
                       "RwLockWriteGuard"})
_WRAPPERS = _HOLDERS | _POINTERS
HELD, POINTED = "?", "*"   # type-table value prefixes: what wraps the named type (the edge builder reads them)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _items(node: Node, src: bytes, prefix: str) -> list[tuple[str, str | None]]:
    """(full path, alias) of every item a use tree imports."""
    def joined(tail: str) -> str:
        return f"{prefix}::{tail}" if prefix else tail

    if node.type in _PATHS:
        return [(joined(_text(node, src)), None)]
    if node.type == "use_as_clause":
        path, alias = node.child_by_field_name("path"), node.child_by_field_name("alias")
        if path is None:
            return []
        # `{self as ast}` binds the module itself under the alias.
        return [(prefix if path.type == "self" else joined(_text(path, src)),
                 _text(alias, src) if alias is not None else None)]
    if node.type == "scoped_use_list":
        path, items = node.child_by_field_name("path"), node.child_by_field_name("list")
        base = joined(_text(path, src)) if path is not None else prefix
        return _items(items, src, base) if items is not None else []
    if node.type == "use_list":
        out: list[tuple[str, str | None]] = []
        for child in node.named_children:
            # `{self, x}` imports the module itself
            out += [(prefix, None)] if child.type == "self" else _items(child, src, prefix)
        return out
    return []   # `a::*` and anything else name no one item


def _inline_modules(node: Node) -> int:
    """How many inline `mod x { .. }` blocks enclose *node*."""
    count, parent = 0, node.parent
    while parent is not None:
        count += parent.type == "mod_item"
        parent = parent.parent
    return count


def _from_file_module(path: str, inline: int) -> str:
    """A path written inside *inline* inline modules, rewritten relative to the file's own module:
    in `mod tests { use super::X; }` super is the file's module, `self::X`."""
    # shortcut: the inline modules' own names are dropped, so `mod a { mod b { use super::Y; } }` reads
    # the file's Y, not a::Y, and `self::` inside `mod a` is the file's; name them if nested modules show up.
    segs = path.split("::")
    supers = next((i for i, s in enumerate(segs) if s != "super"), len(segs))
    peeled = min(supers, inline)
    if not peeled:
        return path
    rest = segs[peeled:]
    return "::".join(rest if rest and rest[0] == "super" else ["self", *rest])


def imports(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    if node.type != "use_declaration":
        return
    inline = _inline_modules(node)
    argument = node.child_by_field_name("argument")
    line = node.start_point[0] + 1
    if argument is not None and argument.type == "use_wildcard" \
            and any(c.type == "visibility_modifier" for c in node.named_children):
        # `pub use model::*;` re-exports a whole module: no import, but a hop to follow to a definition.
        target = _from_file_module(_text(argument, src).removesuffix("::*"), inline)
        out.append(PendingEdgeFact(relation=Relation.RE_EXPORTS, to_name=target,
                                   line=line, to_kind="record", from_kind="record", pinned=True))
        return
    for written, alias in (_items(argument, src, "") if argument is not None else []):
        path = _in_file_module(written, node, src)
        name = path.rsplit("::", 1)[-1]
        if "::" not in path and alias and alias != "_":
            # `use ruff_python_ast as ast;` imports no item, but `ast::Expr` paths start from it: the binding,
            # which places no import (the resolver gives a crate's path no file).
            out.append(PendingEdgeFact(relation=Relation.IMPORTS, to_name=path, line=line, to_kind="block",
                                       qualified_prefix=path, local_name=alias, from_kind="record", pinned=True))
            continue
        if "::" not in path or name in ("crate", "self", "super"):
            continue   # `use serde;`: a crate, not an item
        # A `use` inside a function still imports for the file (the key's rule): the file is the source,
        # pinned so the walker does not narrow it to the function.
        out.append(PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=path, line=line, to_kind="record",
                                   from_kind="record", pinned=True))
        out.append(PendingEdgeFact(relation=Relation.IMPORTS, to_name=name, line=line, to_kind="block",
                                   qualified_prefix=path, local_name=alias, from_kind="record", pinned=True))


def callee(node: Node, src: bytes, _cfg: LanguageConfig) -> tuple[str | None, bool, str | None, str | None]:
    """(name, is_member, receiver, qualifier) of a call: `f()`, `x.f()`, `T::f()`, `a::b::f()`, each also with a
    turbofish (`x.collect::<V>()`, which names `collect`, not `x`). A path whose last segment is lowercase
    (`helpers::f`, `crate::a::f`, `self::f`) names a module's function, so it is a qualified call, not a method
    of a value; an uppercase one (`Type::f`, `Self::f`) is the type's associated function."""
    target = node.child_by_field_name("function")
    if target is not None and target.type == "generic_function":
        target = target.child_by_field_name("function")
    if target is None:
        return None, False, None, None
    if target.type == "identifier":
        return _text(target, src), False, None, None
    leaf = target.child_by_field_name("field" if target.type == "field_expression" else "name")
    if target.type not in ("field_expression", "scoped_identifier") or leaf is None:
        return None, False, None, None
    qualifier = src[target.start_byte:leaf.start_byte].decode("utf-8", "replace").rstrip()
    qualifier = qualifier.removesuffix("." if target.type == "field_expression" else "::").rstrip()
    if not qualifier:
        return _text(leaf, src), False, None, None
    if target.type == "field_expression":
        return _text(leaf, src), True, re.split(r"\.|->|::|:", qualifier)[-1], qualifier
    owner = _without_generics(qualifier).rsplit("::", 1)[-1]   # `Foo::<T>` is Foo
    if not owner[:1].isupper():   # as `use` paths are read: `super::` in `mod tests` is the file's module
        return _text(leaf, src), False, None, _in_file_module(qualifier, node, src)
    return _text(leaf, src), True, owner, qualifier


# Macros whose arguments are expressions a compiler index sees through the expansion: std's (assert, format, vec,
# pin), log/tracing, anyhow/eyre, serde_json's `json!`, futures' `join!`, and the test crates' (insta, insta-cmd,
# snapbox, proptest). Any other macro's arguments may be a DSL (`html!`, `sqlx::query!`, a repo's own
# `macro_rules!`) and stay tokens.
_EXPRESSION_MACROS = frozenset({
    "assert", "assert_eq", "assert_ne", "debug_assert", "debug_assert_eq", "debug_assert_ne", "assert_matches",
    "debug_assert_matches", "matches", "format", "format_args", "print", "println", "eprint", "eprintln", "write",
    "writeln", "panic", "todo", "unimplemented", "unreachable", "dbg", "vec", "pin", "trace", "debug", "info",
    "warn", "error", "event", "anyhow", "bail", "ensure", "eyre", "json", "join", "try_join",
    "assert_snapshot", "assert_debug_snapshot", "assert_display_snapshot", "assert_json_snapshot",
    "assert_yaml_snapshot", "assert_compact_debug_snapshot", "assert_compact_json_snapshot", "assert_cmd_snapshot",
    "assert_data_eq", "prop_assert", "prop_assert_eq", "prop_assert_ne", "prop_assume",
})
_ITEM_SCOPES = frozenset({"source_file", "declaration_list"})


def macro_arguments(root: Node, src: bytes, parse: Callable[[bytes], Tree], cfg: LanguageConfig) -> dict[int, Node]:
    """The parsed arguments of each expression macro, keyed by the invocation's start byte: `assert_eq!(f(x), 1)`
    and `format!("{}", a.b())` call what they spell, which a compiler index sees through the expansion. The source
    is read again with each such macro's `name!` blanked, nested ones too, so its arguments parse at their own
    offsets as the tuple, array or block they spell. Only `_EXPRESSION_MACROS`, and none in item position
    (`thread_local! { .. }`)."""
    from app.modules.parsers.code_parser.engine import _nodes_of_types

    buf, spans = bytearray(src), {}
    for node in _nodes_of_types(root, cfg, ("macro_invocation",)):
        name = node.child_by_field_name("macro")
        args = next((c for c in node.children if c.type == "token_tree"), None)
        if (name is None or args is None or node.parent is None or node.parent.type in _ITEM_SCOPES
                or _text(name, src).rsplit("::", 1)[-1] not in _EXPRESSION_MACROS):
            continue
        spans[node.start_byte] = args
        _blank(buf, node.start_byte, args.start_byte)
        _blank_nested(args, src, buf)
    if not spans:
        return {}
    tree = parse(bytes(buf))
    out = {}
    for start, args in spans.items():
        node = tree.root_node.descendant_for_byte_range(args.start_byte, args.end_byte)
        if node is not None and node.start_byte == args.start_byte and node.end_byte == args.end_byte:
            out[start] = node
    return out


def _blank(buf: bytearray, start: int, end: int) -> None:
    buf[start:end] = re.sub(rb"[^\n]", b" ", bytes(buf[start:end]))   # lines stay put


def _blank_nested(args: Node, src: bytes, buf: bytearray) -> None:
    """Blank the `name!` / `a::name!` of every expression macro written inside *args*, a token tree whose tokens
    are flat: `format`, `!`, `(..)` in a row. Another macro inside stays a macro, its tokens unread."""
    stack = [args]
    while stack:
        tokens = stack.pop().children
        for i, token in enumerate(tokens):
            if token.type != "token_tree":
                continue
            j = i - 1
            if j >= 1 and tokens[j].type == "!" and tokens[j - 1].type == "identifier":
                if _text(tokens[j - 1], src) not in _EXPRESSION_MACROS:
                    continue
                first = j - 1
                while first >= 2 and tokens[first - 1].type == "::" and tokens[first - 2].type == "identifier":
                    first -= 2
                _blank(buf, tokens[first].start_byte, tokens[j].end_byte)
            stack.append(token)


def _without_generics(path: str) -> str:
    """`Snippet::<Annotation<'_>>::source` -> `Snippet::source`, `Vec<u8>` -> `Vec`."""
    path = re.sub(r"\s", "", path)
    while (bare := re.sub(r"<[^<>]*>", "", path)) != path:
        path = bare
    return re.sub(r"(?:::)+$", "", re.sub(r"(?:::){2,}", "::", path))


def type_name(node: Node | None, src: bytes, owner: str | None = None) -> str | None:
    """`&mut Client`, `crate::db::Client`, `Client<'a, T>` -> `Client`; `Self` is *owner* (the impl's type), unknown
    without one. A wrapper's value is the type marked by what wraps it: `Option<&T>`, `Result<T, E>`, `Mutex<T>` ->
    `?T` (`HELD`), `Box<T>`, `Arc<T>` -> `*T` (`POINTED`; a holder anywhere makes it `?`). A trait object or
    `impl Trait` is the trait, and so is a type parameter bounded by one (`M: Matcher`): a call on it is the
    trait's method."""
    via_trait, mark = False, ""
    while node is not None:
        if node.type == "reference_type":
            node = node.child_by_field_name("type")
        elif node.type in ("dynamic_type", "abstract_type"):
            node, via_trait = node.child_by_field_name("trait"), True
        elif node.type == "generic_type":
            base = node.child_by_field_name("type")
            wrapper = _text(base, src).rsplit("::", 1)[-1] if base is not None else ""
            # A repo's own `Result` alias (`FormatResult<T>`, `PrintResult<T>`) holds its value as `Result` does.
            held = wrapper in _HOLDERS or wrapper.endswith("Result")
            if not held and wrapper not in _POINTERS:
                node = base
                break
            mark = HELD if mark == HELD or held else POINTED
            args = node.child_by_field_name("type_arguments")
            node = next((a for a in args.named_children if a.type != "lifetime"), None) if args is not None else None
        else:
            break
    if node is None or node.type not in ("type_identifier", "scoped_type_identifier"):
        return None
    prefix, _, name = re.sub(r"\s", "", _text(node, src)).rpartition("::")
    if name == "Self":
        return f"{mark}{owner}" if owner else None
    if via_trait and name in _MARKER_BOUNDS:   # `impl Iterator<..>`, `dyn Fn()`: no repo trait's methods
        return None
    if node.type == "type_identifier" and (bound := _parameter_bound(node, name, src)) is not None:
        return f"{mark}{bound}" if bound else None
    if not name[:1].isupper():
        return None
    # A path keeps its module (`ast::Expr`, as the file's module sees it): the builder places it, as it does a
    # `use`, before guessing by the bare name among same-named types.
    return mark + (f"{_in_file_module(prefix, node, src)}::{name}" if prefix and prefix != "Self" else name)


def _parameter_bound(node: Node, name: str, src: bytes) -> str | None:
    """The trait bounding *name* where an enclosing item declares it a type parameter (its first bound other
    than a marker, `""` when it has none), None when no enclosing item declares it."""
    scope = node.parent
    while scope is not None:
        if scope.type in ("function_item", "impl_item", "struct_item", "enum_item", "trait_item",
                          "function_signature_item"):
            declared, bounds = False, []
            params = scope.child_by_field_name("type_parameters")
            for param in params.named_children if params is not None else ():
                if param.type == "type_parameter" and (own := param.child_by_field_name("name")) is not None \
                        and _text(own, src) == name:
                    declared = True
                    bounds.append(param.child_by_field_name("bounds"))
            for child in scope.named_children:
                if child.type == "where_clause":
                    bounds += [p.child_by_field_name("bounds") for p in child.named_children
                               if (left := p.child_by_field_name("left")) is not None and _text(left, src) == name]
            if declared or bounds:
                for trait in (t for b in bounds if b is not None for t in b.named_children):
                    base = trait.child_by_field_name("type") if trait.type == "generic_type" else trait
                    if base is not None and base.type in ("type_identifier", "scoped_type_identifier") \
                            and (found := _text(base, src).rsplit("::", 1)[-1]) not in _MARKER_BOUNDS:
                        return found
                return ""
        scope = scope.parent
    return None


# The standard prelude's traits: in scope everywhere, so a bare one is std's unless the file's own
# bindings say otherwise, which only the edge builder knows (`PRELUDE` facts).
_PRELUDE_TRAITS = frozenset({
    "Copy", "Clone", "Send", "Sync", "Sized", "Unpin", "Drop", "Fn", "FnMut", "FnOnce", "PartialEq", "Eq",
    "PartialOrd", "Ord", "AsRef", "AsMut", "Into", "From", "Default", "Iterator", "Extend", "IntoIterator",
    "DoubleEndedIterator", "ExactSizeIterator", "ToOwned", "ToString", "TryFrom", "TryInto", "FromIterator",
})
PRELUDE = "prelude"
# Bounds that say what a type parameter can do, not which trait's methods a call on it runs.
_MARKER_BOUNDS = _PRELUDE_TRAITS | {"Debug", "Display", "Hash", "Serialize", "Deserialize"}


def heritage(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`impl Trait for Type` implements Trait; `trait A: B + C` inherits B and C. A blanket impl
    (`impl<T: X> Tr for T`) names no type; a bare prelude trait (`impl Clone`) is marked `prelude`."""
    line = node.start_point[0] + 1
    bases: list[tuple[Node, str]] = []
    if node.type == "impl_item" and (trait := node.child_by_field_name("trait")) is not None:
        params = node.child_by_field_name("type_parameters")
        own = {_text(p.child_by_field_name("name") or p, src)
               for p in (params.named_children if params is not None else ())}
        head = node.child_by_field_name("type")
        if head is not None and _text(head, src) in own:
            return
        bases.append((trait, Relation.IMPLEMENTS))
    elif node.type == "trait_item" and (bounds := node.child_by_field_name("bounds")) is not None:
        bases += [(b, Relation.INHERITS) for b in bounds.named_children]
    for written, relation in bases:
        base = (written.child_by_field_name("type") or written) if written.type == "generic_type" else written
        if base.type not in ("type_identifier", "scoped_type_identifier"):
            continue
        raw = _text(base, src)
        prefix = _in_file_module(raw.rsplit("::", 1)[0], node, src) if "::" in raw else None
        out.append(PendingEdgeFact(relation=relation, to_name=raw.rsplit("::", 1)[-1], line=line, from_symbol=idx,
                                   qualified_prefix=prefix, context=PRELUDE if raw in _PRELUDE_TRAITS else None))


def _in_file_module(prefix: str, node: Node, src: bytes) -> str:
    """A path as the file's module sees it: `super::` inside an inline module peeled, and a path into a
    module this file declares (`sealed::Sealed` beside `mod sealed { .. }`, `agents::Agent` beside
    `#[path = "common/agents.rs"] mod agents;`) made `self::`-relative, so it is never an outside crate."""
    prefix = _from_file_module(prefix, _inline_modules(node))
    head = prefix.split("::", 1)[0]
    if head not in ("crate", "self", "super") and _declares_module(node, head, src):
        return f"self::{prefix}"
    return prefix


def _declares_module(node: Node, head: str, src: bytes) -> bool:
    """Whether the module scope holding *node* (the file, or the inline `mod x { .. }` it sits in) declares
    `mod <head>` directly: a `mod time {}` inside `mod tests`, or a comment, is not one."""
    scope = node.parent
    while scope is not None and scope.type != "source_file" \
            and not (scope.type == "declaration_list" and scope.parent is not None and scope.parent.type == "mod_item"):
        scope = scope.parent
    return scope is not None and any(
        child.type == "mod_item" and (name := child.child_by_field_name("name")) is not None
        and _text(name, src) == head for child in scope.named_children)


def type_table(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """`let x: T`, `let x = T::new()` / `T { .. }`, parameters `x: &T` and struct fields (calls on
    `self.client` are on the field's type). A name given two types in the file is left out."""
    from app.modules.parsers.code_parser.engine import _nodes_of_types

    seen: dict[str, set[str]] = {}

    def add(name: Node | None, declared: str | None) -> None:
        if name is not None and name.type in ("identifier", "field_identifier") and declared:
            seen.setdefault(_text(name, src), set()).add(declared)

    for node in _nodes_of_types(root, cfg, ("let_declaration", "parameter", "field_declaration")):
        name = node.child_by_field_name("pattern") or node.child_by_field_name("name")
        declared = type_name(node.child_by_field_name("type"), src)
        if declared is None and node.type == "let_declaration":
            value = node.child_by_field_name("value")
            if value is not None and value.type == "call_expression":
                fn = value.child_by_field_name("function")
                owner = fn.child_by_field_name("path") if fn is not None and fn.type == "scoped_identifier" else None
                if owner is not None:   # `T::new(..)`, `a::T::from(..)`
                    last = _text(owner, src).rsplit("::", 1)[-1].split("<")[0]
                    # `Arc::new(x)` is an Arc of something else: no type here
                    declared = last if last[:1].isupper() and last not in ("Self", *_WRAPPERS) else None
            elif value is not None and value.type == "struct_expression":
                declared = type_name(value.child_by_field_name("name"), src)
        add(name, declared)
        if node.type == "field_declaration" and declared and name is not None:
            owner = _owner_name(node, src)
            if owner:   # `Checker.semantic`, for a chain the builder follows through the field
                table.setdefault(f"{owner}.{_text(name, src)}", declared)
    for name, types in seen.items():
        if len({t.rpartition("::")[2] for t in types}) == 1:   # `T` and `a::T` are one type, the path placed
            table.setdefault(name, max(types, key=len))
    # What a function returns, keyed `Owner.method()` / `function()` as the builder's chains read it.
    for node in _nodes_of_types(root, cfg, ("function_item", "function_signature_item")):
        name, owner = node.child_by_field_name("name"), _owner_name(node, src)
        returned = type_name(node.child_by_field_name("return_type"), src, owner)
        if name is not None and returned:
            table.setdefault(f"{owner}.{_text(name, src)}()" if owner else f"{_text(name, src)}()", returned)


_MEMBER_LISTS = frozenset({"field_declaration_list", "declaration_list", "ordered_field_declaration_list"})


def _owner_name(node: Node, src: bytes) -> str | None:
    """The type a field, method or associated function belongs to: its struct, its impl's type or its trait."""
    scope = node.parent
    while scope is not None and scope.type in _MEMBER_LISTS:
        scope = scope.parent
    if scope is None:
        return None
    if scope.type == "impl_item":
        return _impl_name(scope, src)
    if scope.type in ("struct_item", "union_item", "trait_item"):
        name = scope.child_by_field_name("name")
        return _text(name, src) if name is not None else None
    return None


def receiver_type(call: Node, src: bytes, scopes: dict[int, dict[str, list[tuple[int, str]]]],
                  cfg: LanguageConfig, file_wide: str | None) -> str | None:
    """A method call's receiver as the builder types it: `T`, or `=chain` (`=Foo::new().build()`,
    `=self.config().colors`): the receiver written as calls and fields from a head the builder can type (`self`,
    a type, a function, a path), each local variable replaced by what its `let`, parameter or `if let Some(..)`
    bound before. Else *file_wide*, what the file's table says a name of the receiver is somewhere, without what
    wrapped it there (`stats: Option<Stats>` in one fn says nothing of how another holds its `stats`)."""
    target = call.child_by_field_name("function")
    if target is not None and target.type == "generic_function":
        target = target.child_by_field_name("function")
    value = target.child_by_field_name("value") if target is not None and target.type == "field_expression" else None
    chain = _chain(value, src, lambda: _bindings(call, src, scopes, cfg)) if value is not None else None
    if value is not None and value.type == "identifier" and chain == _text(value, src):
        chain = None   # bound nowhere here: `Nushell.generate()` is a unit struct the file's `use` names, or a guess
    if chain == "":
        return None    # a parameter of a type this reader cannot read: another fn's same-named variable says nothing
    if chain is not None and chain != "self":
        if "." in chain or "(" in chain:
            return f"={chain}"
        if chain.lstrip(HELD + POINTED).rpartition("::")[2][:1].isupper():   # a type (`T`, `?ast::Expr`)
            return chain
    return file_wide.lstrip(HELD + POINTED) if file_wide and not file_wide.startswith("=") else None


_SITES = -1   # the scopes cache's key for the file's binding sites


def _bindings(node: Node, src: bytes, scopes: dict, cfg: LanguageConfig) -> dict[str, list[tuple[int, str]]]:
    """name -> [(start byte, what it is bound to)] in the function holding *node*, in source order.

    A nested `fn` has its own (it sees none of its parent's locals, nor they its).
    shortcut: block scopes are not read (a `let x` in an earlier sibling block still binds a later `x`); read
    them if wrong receivers show up there."""
    fn = _function_of(node)
    if fn is None:
        return {}
    if fn.start_byte not in scopes:
        if _SITES not in scopes:   # one query for the file, not one per function
            from app.modules.parsers.code_parser.engine import _nodes_of_types

            root = fn
            while root.parent is not None:
                root = root.parent
            sites = sorted(_nodes_of_types(root, cfg, ("parameter", "let_declaration", "let_condition")),
                           key=lambda n: n.start_byte)
            scopes[_SITES] = ([n.start_byte for n in sites], sites)
        starts, sites = scopes[_SITES]
        found: dict[str, list[tuple[int, str]]] = {}
        scopes[fn.start_byte] = found
        # In source order: an initializer reads the bindings above it.
        for site in sites[bisect.bisect_left(starts, fn.start_byte):bisect.bisect_left(starts, fn.end_byte)]:
            if _function_of(site).start_byte != fn.start_byte:
                continue   # a nested `fn`'s parameter or `let` binds nothing out here
            bound = _bound(site, src, found)
            if bound is not None:
                found.setdefault(bound[0], []).append((site.start_byte, bound[1]))
    return scopes[fn.start_byte]


def _function_of(node: Node) -> Node | None:
    """The innermost `fn` holding *node*."""
    fn = node.parent
    while fn is not None and fn.type != "function_item":
        fn = fn.parent
    return fn


def _bound(node: Node, src: bytes, found: dict) -> tuple[str, str] | None:
    """(name, what it is bound to) for `let x: T`, `let x = <chain>`, a parameter `x: &T`, `if let Some(x) = e`."""
    pattern = _unmut(node.child_by_field_name("pattern"))
    if node.type == "parameter":   # one this reader cannot type (`impl AsRef<P>`, `u8`) still binds its name: ""
        declared = type_name(node.child_by_field_name("type"), src, _impl_owner(node, src))
        return (_text(pattern, src), declared or "") if pattern is not None and pattern.type == "identifier" \
            else None
    if node.type not in ("let_declaration", "let_condition") or pattern is None:
        return None
    unwrap = ""
    if pattern.type == "tuple_struct_pattern":   # `Some(x)`, `Ok(x)`: what the value holds
        kind, inner = pattern.child_by_field_name("type"), pattern.named_children[1:]
        if kind is None or _text(kind, src) not in ("Some", "Ok") or len(inner) != 1:
            return None
        pattern, unwrap = _unmut(inner[0]), ".unwrap()"
    if pattern.type != "identifier":
        return None
    declared = type_name(node.child_by_field_name("type"), src, _impl_owner(node, src))
    if declared:
        return _text(pattern, src), declared
    value = node.child_by_field_name("value")
    if value is not None and value.type == "struct_expression":
        declared = type_name(value.child_by_field_name("name"), src, _impl_owner(node, src))
        return (_text(pattern, src), declared) if declared else None
    chain = _chain(value, src, lambda: found, before=node.start_byte) if value is not None else None
    return (_text(pattern, src), chain + unwrap) if chain else None


def _unmut(pattern: Node | None) -> Node | None:
    """`mut x` -> `x`."""
    return pattern.named_children[-1] if pattern is not None and pattern.type == "mut_pattern" \
        and pattern.named_children else pattern


def _impl_owner(node: Node, src: bytes) -> str | None:
    """The type `Self` means at *node*: its impl's, by name (the impl block's own)."""
    scope = node.parent
    while scope is not None and scope.type != "impl_item":
        scope = scope.parent
    return _impl_name(scope, src) if scope is not None else None


def _impl_name(impl: Node, src: bytes) -> str | None:
    owner = type_name(impl.child_by_field_name("type"), src)
    return owner.lstrip(HELD + POINTED).rpartition("::")[2] if owner else None


def _chain(node: Node, src: bytes, bound: Callable[[], dict], before: int | None = None) -> str | None:
    """*node* as `head.field.method()` (arguments, `?`, `.await`, `&` and turbofish dropped), a local head
    replaced by its latest binding before the use (*bound* gives them, read only for such a head); None for
    anything else (an index, a literal, a macro)."""
    kind = node.type
    if kind in ("try_expression", "await_expression", "parenthesized_expression", "reference_expression") \
            or (kind == "unary_expression" and _text(node, src).startswith("*")):
        inner = node.child_by_field_name("value") or (node.named_children[0] if node.named_children else None)
        chain = _chain(inner, src, bound, before) if inner is not None else None
        # `x?` takes the value out of a `Result` / `Option`, as `unwrap()` does
        return f"{chain}.unwrap()" if chain and kind == "try_expression" else chain
    if kind == "self":
        return "self"
    if kind == "struct_expression":   # `Caller { .. }.harness()`
        return type_name(node.child_by_field_name("name"), src, _impl_owner(node, src))
    if kind == "identifier":
        name = _text(node, src)
        at = node.start_byte if before is None else before
        rows = [value for start, value in bound().get(name, ()) if start < at]
        return rows[-1] if rows else name
    if kind == "field_expression":
        value, field = node.child_by_field_name("value"), node.child_by_field_name("field")
        head = _chain(value, src, bound, before) if value is not None else None
        return f"{head}.{_text(field, src)}" if head and field is not None and field.type == "field_identifier" \
            else None
    if kind != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    if fn is not None and fn.type == "generic_function":
        fn = fn.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "field_expression":
        head = _chain(fn, src, bound, before)
        return f"{head}()" if head else None
    if fn.type in ("identifier", "scoped_identifier"):
        path = _without_generics(_text(fn, src))
        return f"{path}()" if path else None
    return None
