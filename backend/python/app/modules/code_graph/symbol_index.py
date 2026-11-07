"""In-memory indexes built from one scan of a repo's blocks, plus its file paths.

The scan is unavoidable -- resolution needs a complete symbol table, and a
partial one produces wrong edges rather than missing ones. Because pendingEdges
live on the same block documents, that single sweep yields the symbol index, the
deferred facts and the per-file type tables together.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.code_graph.guards import CALLABLE_KINDS, TYPE_KINDS, lang_family
from app.modules.parsers.code_parser.models import DECLARATION

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["BlockRow", "SymbolIndex", "build_symbol_index"]

_REPEAT = re.compile(r"#\d+(?=\.|$)")


def _ensure_list(value: object) -> list[dict]:
    """Accept a list or a JSON string (Neo4j stores complex props as strings)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _ensure_str_list(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _ensure_dict(value: object) -> dict[str, str]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


@dataclass(frozen=True)
class BlockRow:
    key: str
    record_id: str
    name: str | None
    kind: str | None
    qualified_name: str | None
    file_path: str | None
    language: str | None
    is_block_group: bool
    parent_block_id: str | None
    pending_edges: list[dict]
    type_table: dict[str, str]
    # Set on one block per file. The other blocks' facts hold only the resident
    # relations, so this is the complete list of names the file references.
    referenced_names: tuple[str, ...] = ()
    # Block `modifiers` (C/C++: "declaration", "static", "returns:<Class>").
    modifiers: tuple[str, ...] = ()
    # The file-summary block's `macroTable`: C/C++ function-like macro -> [line, names its body calls].
    macro_table: dict[str, list] = field(default_factory=dict)

    @staticmethod
    def from_doc(doc: dict[str, Any], paths: dict[str, str]) -> "BlockRow":
        """``paths`` maps recordId -> file path.

        The path lives on the record, not the block: a directory rename moves
        every file and changes no symbol, so storing it per block would mean ~30
        stale copies per file.
        """
        record_id = doc.get("recordId") or ""
        return BlockRow(
            key=doc.get("_key") or doc.get("id") or "",
            record_id=record_id,
            name=doc.get("name"),
            kind=(doc.get("kind") or None),
            qualified_name=doc.get("qualifiedName"),
            file_path=paths.get(record_id),
            language=doc.get("language"),
            is_block_group=bool(doc.get("isBlockGroup")),
            parent_block_id=doc.get("parentBlockId"),
            pending_edges=_ensure_list(doc.get("pendingEdges")),
            type_table=_ensure_dict(doc.get("typeTable")),
            referenced_names=_ensure_str_list(doc.get("referencedNames")),
            modifiers=_ensure_str_list(doc.get("modifiers")),
            macro_table=_ensure_dict(doc.get("macroTable")),
        )


@dataclass
class SymbolIndex:
    """Every lookup the resolution ladder performs."""

    by_name: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    rows: dict[str, BlockRow] = field(default_factory=dict)
    file_by_block: dict[str, str] = field(default_factory=dict)
    record_by_block: dict[str, str] = field(default_factory=dict)
    family_by_block: dict[str, str] = field(default_factory=dict)
    callable_blocks: set[str] = field(default_factory=set)
    type_blocks: set[str] = field(default_factory=set)
    # (parent_block_id, method_name) -> block key. Derived from parentBlockId +
    # kind rather than re-reading the METHOD edges, which saves a query.
    method_index: dict[tuple[str, str], str] = field(default_factory=dict)
    blocks_by_record: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    file_by_record: dict[str, str] = field(default_factory=dict)
    record_by_file: dict[str, str] = field(default_factory=dict)
    type_table_by_record: dict[str, dict[str, str]] = field(default_factory=dict)
    macro_table_by_record: dict[str, dict[str, list]] = field(default_factory=dict)
    names_by_record: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # Names each file references: its `referencedNames` list plus the names in
    # whatever facts its blocks carry, so a node projected before the residue
    # change (full facts, no list) is covered the same way.
    referenced_names_by_record: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # Qualified paths of the types defined so far, and the forward declarations standing in for undefined ones.
    defined_types: set[str] = field(default_factory=set)
    declared_types: dict[str, list[str]] = field(default_factory=dict)
    # C/C++ per-file lookups memoized for one build (resolvers/c_family.py): same-stem sources, and the files seen.
    c_paired: dict[str, list[str]] = field(default_factory=dict)
    c_visible: dict[str, set[str]] = field(default_factory=dict)

    def candidates(self, name: str) -> list[str]:
        return self.by_name.get(name, [])

    def add_docs(self, docs: Iterable[dict[str, Any]], paths: dict[str, str],
                 referenced_names: dict[str, tuple[str, ...]]) -> None:
        """Index block docs; called once per scanned page, so no full list is kept (SC-1)."""
        for doc in docs:
            row = BlockRow.from_doc(doc, paths)
            if row.key:
                self._add(row, referenced_names.get(row.record_id))

    def _add(self, row: BlockRow, file_refs: tuple[str, ...] | None) -> None:
        self.rows[row.key] = row
        self.record_by_block[row.key] = row.record_id
        self.blocks_by_record[row.record_id].append(row.key)
        if row.file_path:
            self.file_by_block[row.key] = row.file_path
            self.file_by_record.setdefault(row.record_id, row.file_path)
            self.record_by_file.setdefault(row.file_path, row.record_id)
        family = lang_family(row.language, row.file_path)
        if family:
            self.family_by_block[row.key] = family
        if row.type_table:
            self.type_table_by_record[row.record_id] = row.type_table
        if row.macro_table:
            self.macro_table_by_record[row.record_id] = row.macro_table

        # referencedNames comes from codeFiles (passed in referenced_names).
        # Fallback to block's resident facts for nodes projected before the
        # move, or for files that lack a codeFiles entry (uploaded .py).
        ref_set = self.referenced_names_by_record[row.record_id]
        if file_refs:
            ref_set.update(file_refs)
        else:
            ref_set.update(row.referenced_names)
            ref_set.update(
                name for name in (fact.get("toName") for fact in row.pending_edges)
                if isinstance(name, str) and name
            )

        if row.name:
            self.by_name[row.name].append(row.key)
            self.names_by_record[row.record_id].add(row.name)
            if row.parent_block_id and row.kind in ("method", "constructor"):
                self.method_index[(row.parent_block_id, row.name)] = row.key
        if row.kind in CALLABLE_KINDS:
            self.callable_blocks.add(row.key)
        elif row.kind in TYPE_KINDS:
            self._add_type(row)

    def _add_type(self, row: BlockRow) -> None:
        """A C++ forward declaration (`class Conn;`) is a type only while no definition of the same qualified name is
        indexed: a definition is never second to it, and one tree-sitter misparsed (abseil's attribute macros on
        `class Mutex`) leaves the declaration to stand for it. Order-independent, as the scan is paged."""
        path = _REPEAT.sub("", (row.qualified_name or "").partition(":")[2])
        if DECLARATION not in row.modifiers:
            self.type_blocks.add(row.key)
            self.defined_types.add(path)
            self.type_blocks.difference_update(self.declared_types.pop(path, ()))
        elif path not in self.defined_types:
            self.type_blocks.add(row.key)
            self.declared_types.setdefault(path, []).append(row.key)


def build_symbol_index(
    docs: Iterable[dict[str, Any]],
    paths: dict[str, str] | None = None,
    referenced_names: dict[str, tuple[str, ...]] | None = None,
) -> SymbolIndex:
    index = SymbolIndex()
    index.add_docs(docs, paths or {}, referenced_names or {})
    return index
