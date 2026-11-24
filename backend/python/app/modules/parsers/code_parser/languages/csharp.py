"""C#: `using` directives, base lists and a type table.

A `using` names a namespace (or, `using static` and an alias, a type), never a
file: it is kept as an IMPORTS_FROM fact with no local name, so it binds no
name when no file resolves it, and the edge builder's C# namespace scope reads
it (`code_graph.resolvers.csharp`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.models import EXPLICIT, PendingEdgeFact, Relation

if TYPE_CHECKING:
    from tree_sitter import Node

    from app.modules.parsers.code_parser.lang_config import LanguageConfig

__all__ = ["GLOBAL_USING", "USING_IN", "heritage", "imports", "modifiers", "type_table"]

# The context of a `global using`'s fact: it scopes every file of its project, not only its own.
GLOBAL_USING = "global-using"
# The context prefix of a `using` inside a namespace declaration (`namespace:A.B`): C# looks there before the
# enclosing namespace and the compilation unit.
USING_IN = "namespace:"

_NAME_TYPES = frozenset({"identifier", "qualified_name", "generic_name", "alias_qualified_name"})


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def type_name(node: Node, src: bytes) -> str:
    """`List<Order>` -> `List`, `Shop.Core.IRepo` -> `IRepo`, `Order[]` and `Order?` -> `Order`."""
    return _text(node, src).split("<")[0].split("[")[0].rstrip("?").strip().rsplit(".", 1)[-1]


def imports(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`using A.B;`, `using static A.B.C;`, `using X = A.B.C;`: what the directive names."""
    if node.type != "using_directive":
        return
    target = None
    for i, child in enumerate(node.children):
        if child.is_named and child.type in _NAME_TYPES and node.field_name_for_child(i) != "name":
            target = child
    if target is not None:
        namespace = _declaring_namespace(node, src)
        context = GLOBAL_USING if node.children[0].type == "global" else (
            USING_IN + namespace if namespace else None)
        out.append(PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=_text(target, src).split("<")[0],
                                   line=node.start_point[0] + 1, to_kind="record", context=context))


def _declaring_namespace(node: Node, src: bytes) -> str:
    """The namespace whose declaration holds a `using` (`A.B` for `namespace A { namespace B { using X; } }`
    or a `using` after `namespace A.B;`), "" at the compilation unit."""
    names, parent = [], node.parent
    while parent is not None:
        if parent.type == "namespace_declaration" and (name := parent.child_by_field_name("name")) is not None:
            names.append(_text(name, src))
        parent = parent.parent
    if names:
        return ".".join(reversed(names))
    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type != "file_scoped_namespace_declaration":
        sibling = sibling.prev_named_sibling
    name = sibling.child_by_field_name("name") if sibling is not None else None
    return _text(name, src) if name is not None else ""


def _has_modifier(node: Node, src: bytes, word: str) -> bool:
    return any(c.type == "modifier" and _text(c, src) == word for c in node.named_children)


def modifiers(def_node: Node, src: bytes) -> tuple[str, ...]:
    """What resolution needs beyond the kind: `partial` on a type; `extension` and `this:T` on a method
    whose first parameter is `this T x` (callable as `x.M()` on a T)."""
    words = ("partial",) if _has_modifier(def_node, src, "partial") else ()
    params = def_node.child_by_field_name("parameters")
    first = next((c for c in params.named_children if c.type == "parameter"), None) if params else None
    if first is not None and _has_modifier(first, src, "this"):
        extended = first.child_by_field_name("type")
        words += ("extension", *((f"this:{type_name(extended, src)}",) if extended is not None else ()))
    explicit = next((c for c in def_node.named_children if c.type == "explicit_interface_specifier"), None)
    named = explicit.named_children[0] if explicit is not None and explicit.named_children else None
    if named is not None:
        # `Task IHandler.Handle(..)` implements IHandler.Handle; it is no member `Handle` of its own type.
        # With its arity: `IHandler.Handle` beside an `IHandler<T>` base names the plain IHandler (`IHandler`0`).
        words += (f"{EXPLICIT}{type_name(named, src)}`{_type_args(named)}",)
    return words


def heritage(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`class A : Base, IRepo`, `record R(int X) : Base(X)`: every base as INHERITS; the edge
    builder makes it IMPLEMENTS when the base resolves to an interface, as for Java."""
    line = node.start_point[0] + 1
    for base_list in (c for c in node.named_children if c.type == "base_list"):
        for entry in base_list.named_children:
            base = (entry.child_by_field_name("type") or entry
                    if entry.type == "primary_constructor_base_type" else entry)
            if base.type not in _NAME_TYPES:
                continue
            raw = _text(base, src).split("<")[0].strip()
            out.append(PendingEdgeFact(
                relation=Relation.INHERITS, to_name=raw.rsplit(".", 1)[-1], line=line, from_symbol=idx,
                qualified_prefix=raw.rsplit(".", 1)[0] if "." in raw else None, type_args=_type_args(base),
            ))


def _type_args(name: Node) -> int:
    """`IHandler<int, string>` and `Shop.IThing<X>`: 2 and 1; a plain name 0."""
    generic = name.named_children[-1] if name.type == "qualified_name" and name.named_children else name
    args = next((c for c in generic.named_children if c.type == "type_argument_list"), None) \
        if generic.type == "generic_name" else None
    return len(args.named_children) if args is not None else 0


def type_table(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """Fields, properties, parameters and locals (`T x`, `var x = new T()`): calls on `x` are on T.
    A name declared with two types in the file is left out, as the receiver is then unknown."""
    from app.modules.parsers.code_parser.engine import _nodes_of_types

    seen: dict[str, set[str]] = {}

    def add(name: Node | None, type_node: Node | None) -> None:
        if name is None or type_node is None or type_node.type in ("predefined_type", "implicit_type"):
            return
        declared = type_name(type_node, src)
        if declared[:1].isupper():
            # `IHandler<Paid> h` is IHandler`1: the arity tells IHandler<T> from a plain IHandler.
            arity = _type_args(type_node)
            seen.setdefault(_text(name, src), set()).add(f"{declared}`{arity}" if arity else declared)

    for node in _nodes_of_types(root, cfg, ("variable_declaration", "parameter", "property_declaration")):
        if node.type == "variable_declaration":
            declared = node.child_by_field_name("type")
            for declarator in (c for c in node.named_children if c.type == "variable_declarator"):
                value = next((c for c in declarator.named_children if c.type == "object_creation_expression"), None)
                inferred = value.child_by_field_name("type") if value is not None else None
                add(declarator.child_by_field_name("name"),
                    inferred if declared is not None and declared.type == "implicit_type" else declared)
        else:
            add(node.child_by_field_name("name"), node.child_by_field_name("type"))
    for name, types in seen.items():
        if len(types) == 1:
            table.setdefault(name, next(iter(types)))
