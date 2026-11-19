"""C# namespaces: which files a bare name in a file can see without naming them.

C# imports namespaces, not files, and a file sees its own namespace and every
enclosing one (`A.B.C` sees `A.B` and `A`) with no directive at all. The scope
of a file is the files declaring any of those namespaces or a namespace (or
the namespace of a type) it names in a `using`, plus its project's `global using`s:
those of the nearest folder above it that writes any (no .csproj is read). A
`global using` fact stays resident on its node, so an incremental build sees an
unchanged GlobalUsings.cs, and a change to one re-resolves the files under its
folder (block_projection's `folder-scope:` names).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.code_graph.resolvers.namespaces import namespaces
from app.modules.parsers.code_parser.languages.csharp import GLOBAL_USING, USING_IN

if TYPE_CHECKING:
    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["extension_methods", "namespace_scope", "partial_parts", "scope_levels"]

_IMPORTS_FROM = "IMPORTS_FROM"


def _enclosing(namespace: str) -> list[str]:
    parts = namespace.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _usings(index: SymbolIndex, file_path: str) -> list[tuple[str, str | None]]:
    """(named namespace or type, the fact's context: GLOBAL_USING, USING_IN + namespace, or None at the
    compilation unit) for each using of *file_path*."""
    record = index.record_by_file.get(file_path)
    return [(fact["toName"], fact.get("context"))
            for block_id in (index.blocks_by_record.get(record, ()) if record else ())
            for fact in index.rows[block_id].pending_edges
            if fact.get("relation") == _IMPORTS_FROM and fact.get("toName")]


def _named(using: str) -> tuple[str, str]:
    # `using static A.B.C;` and `using X = A.B.C;` name a type: its namespace is A.B.
    return using, using.rpartition(".")[0]


def _project_usings(index: SymbolIndex, file_path: str) -> set[str]:
    """The `global using`s of *file_path*'s project: those of the nearest folder above it that writes any."""
    spaces = namespaces(index, ".cs")
    if not hasattr(spaces, "global_usings"):
        spaces.global_usings = {}
        for path in (p for p in index.record_by_file if p.endswith(".cs")):
            found = {named for named, context in _usings(index, path) if context == GLOBAL_USING}
            if found:
                spaces.global_usings.setdefault(path.rpartition("/")[0], set()).update(found)
    folder = file_path.rpartition("/")[0]
    while True:
        if folder in spaces.global_usings:
            return spaces.global_usings[folder]
        if not folder:
            return set()
        folder = folder.rpartition("/")[0]


def namespace_scope(index: SymbolIndex, file_path: str) -> list[str]:
    """The other files whose definitions a bare name in *file_path* sees, sorted."""
    spaces = namespaces(index, ".cs")
    if file_path not in spaces.scope:
        spaces.scope[file_path] = sorted(set().union(*scope_levels(index, file_path)))
    return spaces.scope[file_path]


def scope_levels(index: SymbolIndex, file_path: str) -> list[set[str]]:
    """The files of *file_path*'s scope in C#'s lookup order, nearest first: from the innermost namespace
    outward, that namespace's members, then the usings its declaration holds; last the compilation unit
    (file-level usings and the project's `global using`s, one level).

    shortcut: one order per file, from its deepest namespace outward, then its other namespaces'; a file
    declaring sibling namespaces gets that one order for all of them.
    """
    spaces = namespaces(index, ".cs")
    key = ("levels", file_path)
    if key not in spaces.scope:
        own = sorted(spaces.of_file.get(file_path, ()), key=lambda ns: (-len(ns), ns))
        chain = list(dict.fromkeys(ns for declared in own for ns in _enclosing(declared)))
        usings_at: dict[str, set[str]] = {}
        for named, context in _usings(index, file_path):
            level = context.removeprefix(USING_IN) if context and context.startswith(USING_IN) else ""
            usings_at.setdefault(level, set()).update(_named(named))
        unit = usings_at.get("", set()) | {n for u in _project_usings(index, file_path) for n in _named(u)}
        wanted = [names for ns in chain for names in ({ns}, usings_at.get(ns, set()))] + [unit]
        spaces.scope[key] = [set(spaces.in_namespaces(names, file_path)) for names in wanted]
    return spaces.scope[key]


def extension_methods(index: SymbolIndex, file_path: str, name: str) -> list[str]:
    """The extension methods named *name* that *file_path* sees: its own and its namespace scope's."""
    visible = {file_path, *namespace_scope(index, file_path)}
    return [cid for cid in index.candidates(name)
            if "extension" in index.rows[cid].modifiers and index.file_by_block.get(cid) in visible]


def partial_parts(index: SymbolIndex, type_block: str) -> list[str]:
    """A `partial` type's other declarations: `partial` blocks of the same kind, qualified name and
    type-parameter count (`Foo` and `Foo<T>` are two types).

    shortcut: no project boundary (.csproj files are not indexed), so two `partial` types of one qualified
    name in two projects count as one; read project roots if repos with such duplicates show up.
    """
    row = index.rows.get(type_block)
    if row is None or not row.qualified_name or "partial" not in row.modifiers:
        return []
    declared = namespaces(index, ".cs").blocks.get(row.qualified_name.partition(":")[2], ())
    return [b for b in declared if b != type_block and (part := index.rows[b]).kind == row.kind
            and "partial" in part.modifiers and part.type_param_count == row.type_param_count]
