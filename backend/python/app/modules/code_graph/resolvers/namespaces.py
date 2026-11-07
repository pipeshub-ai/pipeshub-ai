"""The namespaces of one language's files, read from the indexed blocks (no project files are read).

A namespace block (C# `namespace A.B`, PHP `namespace A\\B`, named `A.B` by the
parser) says which file declares which namespace; a definition's qualified name
(`class:A.B.C`) says which file declares A.B.C. Built once per build and kept on
the build's index.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["Namespaces", "namespaces"]

_DECLARING_KINDS = frozenset({"class", "interface", "trait", "enum", "struct", "function", "type_alias"})


class Namespaces:
    def __init__(self, index: SymbolIndex, extension: str) -> None:
        self.files: dict[str, set[str]] = {}       # namespace -> files declaring it
        self.of_file: dict[str, set[str]] = {}     # file -> namespaces it declares
        self.declared: dict[str, set[str]] = {}    # `A.B.C` -> files declaring that definition
        self.blocks: dict[str, list[str]] = {}     # `A.B.C` -> the blocks declaring it
        for row in index.rows.values():
            path = row.file_path or ""
            if not path.endswith(extension):
                continue
            if row.kind == "module" and row.name:
                # `namespace A { namespace B { } }` is A.B, which its block's qualified name spells out.
                name = (row.qualified_name or "").partition(":")[2] or row.name
                self.files.setdefault(name, set()).add(path)
                self.of_file.setdefault(path, set()).add(name)
            elif row.kind in _DECLARING_KINDS and row.qualified_name:
                qualified = row.qualified_name.partition(":")[2]
                self.declared.setdefault(qualified, set()).add(path)
                self.blocks.setdefault(qualified, []).append(row.key)
        self.scope: dict[object, list[str]] = {}   # a hook's memo: file (or a hook's own key) -> its scope

    def in_namespaces(self, wanted: set[str], file_path: str) -> list[str]:
        """The files declaring any of *wanted*, *file_path* left out, sorted."""
        return sorted({f for ns in wanted for f in self.files.get(ns, ())} - {file_path})


def namespaces(index: SymbolIndex, extension: str) -> Namespaces:
    # Kept on the index, which lives for one build; an index is unhashable, so no weak map.
    cache = index.__dict__.setdefault("_namespaces", {})
    if extension not in cache:
        cache[extension] = Namespaces(index, extension)
    return cache[extension]
