"""PHP: `use` imports, extends / implements / trait uses, and a type table.

`use A\\B\\C;` imports the class C: an IMPORTS_FROM to the fully qualified name
(the file declaring it) and an IMPORTS of C qualified by that name, bound under
its alias when it has one. Only the item imported is an import; the namespace
segments before it are not (the answer key's rule).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.models import PendingEdgeFact, Relation

if TYPE_CHECKING:
    from tree_sitter import Node

    from app.modules.parsers.code_parser.lang_config import LanguageConfig

__all__ = ["heritage", "imports", "type_table"]

_NAMES = frozenset({"name", "qualified_name"})


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _split(qualified: str) -> tuple[str, str | None]:
    """`\\App\\Models\\User` -> (`User`, `App\\Models`)."""
    prefix, _, name = qualified.strip().lstrip("\\").rpartition("\\")
    return name, prefix or None


def imports(node: Node, src: bytes, out: list[PendingEdgeFact]) -> None:
    """`use A\\B;`, `use A\\B as C;`, `use A\\{B, C as D};`, `use function A\\f;`."""
    if node.type != "namespace_use_declaration":
        return
    line = node.start_point[0] + 1
    group = next((c for c in node.named_children if c.type == "namespace_name"), None)
    base = _text(group, src).lstrip("\\") + "\\" if group is not None else ""
    for clause in (c for root in node.named_children
                   for c in ([root] if root.type == "namespace_use_clause" else root.named_children)
                   if c.type == "namespace_use_clause"):
        alias = clause.child_by_field_name("alias")
        target = next((c for c in clause.named_children if c.type in _NAMES
                       and (alias is None or c.start_byte != alias.start_byte)), None)
        if target is None:
            continue
        qualified = base + _text(target, src).lstrip("\\")
        name, _ = _split(qualified)
        out.append(PendingEdgeFact(relation=Relation.IMPORTS_FROM, to_name=qualified, line=line, to_kind="record"))
        out.append(PendingEdgeFact(relation=Relation.IMPORTS, to_name=name, line=line, to_kind="block",
                                   qualified_prefix=qualified,
                                   local_name=_text(alias, src) if alias is not None else None))


def heritage(node: Node, src: bytes, out: list[PendingEdgeFact], idx: int) -> None:
    """`extends`, `implements` and, in the body, `use Trait;`."""
    line = node.start_point[0] + 1
    body = node.child_by_field_name("body")
    clauses = [(c, Relation.INHERITS) for c in node.named_children if c.type == "base_clause"]
    clauses += [(c, Relation.IMPLEMENTS) for c in node.named_children if c.type == "class_interface_clause"]
    clauses += [(c, Relation.INHERITS) for c in (body.named_children if body is not None else ())
                if c.type == "use_declaration"]
    for clause, relation in clauses:
        for item in (c for c in clause.named_children if c.type in _NAMES):
            name, prefix = _split(_text(item, src))
            out.append(PendingEdgeFact(relation=relation, to_name=name, line=line, from_symbol=idx,
                                       qualified_prefix=prefix))


def _type(node: Node | None, src: bytes) -> str | None:
    """`?User`, `\\App\\User` -> `User`; a builtin, union or intersection type -> None."""
    if node is None:
        return None
    if node.type == "optional_type":
        node = next(iter(node.named_children), None)
        if node is None:
            return None
    if node.type != "named_type":
        return None
    name, _ = _split(_text(node, src))
    return name if name[:1].isupper() else None


def type_table(root: Node, src: bytes, table: dict[str, str], cfg: LanguageConfig) -> None:
    """Typed properties (`$this->repo`), parameters (`$repo`, and `$this->repo` when promoted) and
    `$x = new T()`. A name given two types in the file is left out."""
    from app.modules.parsers.code_parser.engine import _nodes_of_types

    seen: dict[str, set[str]] = {}

    def add(key: str, declared: str | None) -> None:
        if declared:
            seen.setdefault(key, set()).add(declared)

    for node in _nodes_of_types(root, cfg, ("property_declaration", "simple_parameter",
                                            "property_promotion_parameter", "assignment_expression")):
        if node.type == "property_declaration":
            declared = _type(node.child_by_field_name("type"), src)
            for element in (c for c in node.named_children if c.type == "property_element"):
                var = element.child_by_field_name("name")
                if var is not None:
                    add("$this->" + _text(var, src).lstrip("$"), declared)
        elif node.type in ("simple_parameter", "property_promotion_parameter"):
            var = node.child_by_field_name("name")
            declared = _type(node.child_by_field_name("type"), src)
            if var is not None:
                add(_text(var, src), declared)
                if node.type == "property_promotion_parameter":
                    add("$this->" + _text(var, src).lstrip("$"), declared)
        else:
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and left.type == "variable_name" and right is not None \
                    and right.type == "object_creation_expression":
                created = next((c for c in right.named_children if c.type in _NAMES), None)
                if created is not None:
                    name, _ = _split(_text(created, src))
                    add(_text(left, src), name if name[:1].isupper() else None)
    for key, types in seen.items():
        if len(types) == 1:
            table.setdefault(key, next(iter(types)))
