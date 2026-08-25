"""In-memory indexes built from one scan of a repo's blocks, plus its file paths.

The scan is unavoidable -- resolution needs a complete symbol table, and a
partial one produces wrong edges rather than missing ones. Because pendingEdges
live on the same block documents, that single sweep yields the symbol index, the
deferred facts and the per-file type tables together.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.modules.code_graph.guards import CALLABLE_KINDS, TYPE_KINDS, lang_family

__all__ = ["BlockRow", "SymbolIndex", "build_symbol_index"]


def _ensure_list(value: Any) -> list[dict]:
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


def _ensure_dict(value: Any) -> dict[str, str]:
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
    names_by_record: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def candidates(self, name: str) -> list[str]:
        return self.by_name.get(name, [])


def build_symbol_index(
    docs: Iterable[dict[str, Any]], paths: dict[str, str] | None = None
) -> SymbolIndex:
    paths = paths or {}
    index = SymbolIndex()
    for doc in docs:
        row = BlockRow.from_doc(doc, paths)
        if not row.key:
            continue
        index.rows[row.key] = row
        index.record_by_block[row.key] = row.record_id
        index.blocks_by_record[row.record_id].append(row.key)
        if row.file_path:
            index.file_by_block[row.key] = row.file_path
            index.file_by_record.setdefault(row.record_id, row.file_path)
            index.record_by_file.setdefault(row.file_path, row.record_id)
        family = lang_family(row.language, row.file_path)
        if family:
            index.family_by_block[row.key] = family
        if row.type_table:
            index.type_table_by_record[row.record_id] = row.type_table

        if row.name:
            index.by_name[row.name].append(row.key)
            index.names_by_record[row.record_id].add(row.name)
            if row.parent_block_id and row.kind in ("method", "constructor"):
                index.method_index[(row.parent_block_id, row.name)] = row.key
        if row.kind in CALLABLE_KINDS:
            index.callable_blocks.add(row.key)
        elif row.kind in TYPE_KINDS:
            index.type_blocks.add(row.key)
    return index
