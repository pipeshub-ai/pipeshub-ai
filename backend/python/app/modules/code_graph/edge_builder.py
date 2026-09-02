"""Corpus-wide edge resolution for a code repository.

Ordering is load-bearing. Imports resolve first because their output is the
evidence table every later step consults; member calls resolve last because they
need both the type tables and the method index.

Spec: ``edge-resolution.md`` at the repo root.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.code_graph.guards import (
    lang_family,
    path_proximity_winner,
    prefer_non_test,
)
from app.modules.code_graph.import_resolution import ImportResolution
from app.modules.code_graph.symbol_index import SymbolIndex, build_symbol_index
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["CODE_GRAPH_SOURCE", "EdgeBuildResult", "build_code_graph_edges"]

CODE_GRAPH_SOURCE = "code_graph"

CONFIDENCE_EXTRACTED = "EXTRACTED"
CONFIDENCE_INFERRED = "INFERRED"

_SCAN_PAGE = 2000
_WRITE_CHUNK = 500

_CALL_RELATIONS = {RecordRelations.CALLS.value}
_MODULE_RELATIONS = {
    RecordRelations.IMPORTS_FROM.value,
    RecordRelations.RE_EXPORTS.value,
}
_TYPE_RELATIONS = {
    RecordRelations.INHERITS.value,
    RecordRelations.EXTENDS.value,
    RecordRelations.IMPLEMENTS.value,
}

logger = logging.getLogger(__name__)


@dataclass
class EdgeBuildResult:
    files_total: int = 0
    files_touched: int = 0
    files_reresolved: int = 0
    blocks: int = 0
    pending_edges_seen: int = 0
    edges_by_type: dict[str, int] = field(default_factory=dict)
    # A reference to a name no file in the repo defines -- a third-party import,
    # a stdlib call. Expected, not a failure, and separated from `unresolved` so
    # a large total does not read as a bug.
    external_refs: int = 0
    unresolved: int = 0
    ambiguous_skipped: int = 0
    external_nodes: int = 0
    edges_written: int = 0
    edges_removed: int = 0
    duration_s: float = 0.0

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "files_total": self.files_total,
            "files_touched": self.files_touched,
            "files_reresolved": self.files_reresolved,
            "blocks": self.blocks,
            "pending_edges_seen": self.pending_edges_seen,
            "edges_by_type": self.edges_by_type,
            "external_refs": self.external_refs,
            "unresolved": self.unresolved,
            "ambiguous_skipped": self.ambiguous_skipped,
            "external_nodes": self.external_nodes,
            "edges_written": self.edges_written,
            "edges_removed": self.edges_removed,
            "duration_s": round(self.duration_s, 2),
        }


async def _scan_blocks(graph_provider: IGraphDBProvider, org_id: str,
                       record_group_id: str) -> list[dict]:
    """Skip-paginated sweep of every block in the repo."""
    out: list[dict] = []
    skip = 0
    filters = {"orgId": org_id, "recordGroupId": record_group_id}
    while True:
        page = await graph_provider.get_documents_paginated(
            collection=CollectionNames.BLOCKS.value,
            skip=skip,
            limit=_SCAN_PAGE,
            filters=filters,
            sort_field="_key",
        )
        if not page:
            break
        out.extend(page)
        skip += len(page)
        if len(page) < _SCAN_PAGE:
            break
    return out


async def _scan_file_paths(graph_provider: IGraphDBProvider, org_id: str,
                           docs: list[dict]) -> dict[str, str]:
    """recordId -> file path for every record the scan touched.

    Resolution is path-driven -- a relative import resolves against the
    importing file's directory, and the proximity guards score on it -- but the
    path lives on the record, so it is fetched once per record rather than read
    off every block.
    """
    record_ids = sorted({doc.get("recordId") for doc in docs if doc.get("recordId")})
    if not record_ids:
        return {}
    paths: dict[str, str] = {}
    for chunk in _chunks(record_ids, 2000):
        paths.update(
            await graph_provider.get_file_paths_for_records(
                org_id=org_id, record_ids=chunk
            )
        )
    return paths


class _Builder:
    def __init__(self, index: SymbolIndex, org_id: str, record_group_id: str,
                 log: logging.Logger) -> None:
        self.index = index
        self.org_id = org_id
        self.record_group_id = record_group_id
        self.log = log
        self.result = EdgeBuildResult()
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.external_nodes: dict[str, dict] = {}
        # file -> imported symbol names / imported module files
        self.symbol_imports: dict[str, set[str]] = {}
        self.module_imports: dict[str, set[str]] = {}
        self.re_exports: dict[str, list[str]] = {}
        self.imports = ImportResolution(set(index.record_by_file.keys()))

    # -- emit -----------------------------------------------------------

    def _add_edge(self, from_key: str, to_key: str, relation: str, *,
                  confidence: str, line: int | None) -> None:
        if from_key == to_key:  # self-loop
            return
        dedup = (from_key, to_key, relation)
        if dedup in self.edges:
            return
        now = int(time.time() * 1000)
        self.edges[dedup] = {
            "_from": from_key,
            "_to": to_key,
            "relationshipType": relation,
            "constraintName": f"{CODE_GRAPH_SOURCE}:{relation.lower()}:{from_key}:{to_key}",
            "orgId": self.org_id,
            "recordGroupId": self.record_group_id,
            "source": CODE_GRAPH_SOURCE,
            "confidence": confidence,
            "line": line,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        self.result.edges_by_type[relation] = self.result.edges_by_type.get(relation, 0) + 1

    @staticmethod
    def _block_key(block_id: str) -> str:
        return f"{CollectionNames.BLOCKS.value}/{block_id}"

    @staticmethod
    def _record_key(record_id: str) -> str:
        return f"{CollectionNames.RECORDS.value}/{record_id}"

    def _source_key(self, block_id: str, fact: dict) -> str:
        if fact.get("fromKind") == "record":
            return self._record_key(self.index.record_by_block.get(block_id, ""))
        return self._block_key(block_id)

    # -- step 3: imports ------------------------------------------------

    def resolve_imports(self, work: list[tuple[str, dict]]) -> None:
        """Module specifiers -> edges to the imported file, plus evidence.

        Sourced from the imports block that holds the statement, not from the
        file record -- the innermost block containing a reference owns it.
        """
        for block_id, fact in work:
            relation = fact.get("relation")
            if relation not in _MODULE_RELATIONS:
                continue
            record_id = self.index.record_by_block.get(block_id, "")
            from_file = self.index.file_by_record.get(record_id)
            family = self.index.family_by_block.get(block_id) or lang_family(None, from_file)
            specifier = fact.get("toName") or ""
            target_file = self.imports.resolve(specifier, from_file or "", family)
            if not target_file:
                # A bare specifier (`react`, `os`) resolves to no repo file.
                self.result.external_refs += 1
                continue
            if relation == RecordRelations.RE_EXPORTS.value:
                self.re_exports.setdefault(from_file or "", []).append(target_file)
            self.module_imports.setdefault(from_file or "", set()).add(target_file)
            target_record = self.index.record_by_file.get(target_file)
            if not target_record:
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._record_key(target_record), relation,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
            )

        # Symbol-level imports become evidence and, when the symbol is in the
        # repo, an edge to the definition itself.
        for block_id, fact in work:
            if fact.get("relation") != RecordRelations.IMPORTS.value:
                continue
            record_id = self.index.record_by_block.get(block_id, "")
            from_file = self.index.file_by_record.get(record_id) or ""
            name = fact.get("toName") or ""
            self.symbol_imports.setdefault(from_file, set()).add(name)

            family = self.index.family_by_block.get(block_id) or lang_family(None, from_file)
            specifier = fact.get("qualifiedPrefix") or ""
            target_file = self.imports.resolve(specifier, from_file, family)
            if target_file:
                target_file = self.imports.walk_barrel_chain(target_file, self.re_exports)
            candidates = self.index.candidates(name)
            if target_file:
                candidates = [cid for cid in candidates
                              if self.index.file_by_block.get(cid) == target_file]
            elif family:
                # The specifier named no repo file, so it is a package
                # (`@mui/material`) and the only thing left to go on is the
                # name. Unguarded, the single repo block called `Box` wins --
                # which is how a .tsx import of `Box` landed on box.py, and
                # `Graph` on models/graph.py.
                candidates = [cid for cid in candidates if self._family_of(cid) == family]
            if len(candidates) != 1:
                if candidates:
                    self.result.ambiguous_skipped += 1
                else:
                    # An imported symbol the repo does not define is a
                    # third-party import, not a failure.
                    self.result.external_refs += 1
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(candidates[0]),
                RecordRelations.IMPORTS.value,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
            )

    # -- evidence -------------------------------------------------------

    def _family_of(self, block_id: str) -> str | None:
        return (self.index.family_by_block.get(block_id)
                or lang_family(None, self.index.file_by_block.get(block_id)))

    def _has_import_evidence(self, candidate_id: str, call_site_file: str) -> bool:
        row = self.index.rows.get(candidate_id)
        if row is None:
            return False
        if row.name and row.name in self.symbol_imports.get(call_site_file, ()):
            return True
        return (row.file_path or "") in self.module_imports.get(call_site_file, ())

    # -- steps 4/6/7: everything else ------------------------------------

    def resolve_rest(self, work: list[tuple[str, dict]]) -> None:
        for block_id, fact in work:
            relation = fact.get("relation")
            if relation in _MODULE_RELATIONS or relation == RecordRelations.IMPORTS.value:
                continue
            name = fact.get("toName") or ""
            if not name:
                continue

            record_id = self.index.record_by_block.get(block_id, "")
            call_file = self.index.file_by_record.get(record_id) or ""
            family = self.index.family_by_block.get(block_id)

            if fact.get("restrictToFile"):
                target = self._resolve_within_record(record_id, name)
                if target:
                    self._add_edge(
                        self._source_key(block_id, fact), self._block_key(target), relation,
                        confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                    )
                else:
                    self.result.unresolved += 1
                continue

            if fact.get("isMemberCall"):
                target = self._resolve_member_call(block_id, fact, record_id)
                if target:
                    self._add_edge(
                        self._source_key(block_id, fact), self._block_key(target),
                        RecordRelations.CALLS.value,
                        confidence=CONFIDENCE_INFERRED, line=fact.get("line"),
                    )
                elif not self.index.candidates(name):
                    # `resp.json()` on a third-party object -- the method name
                    # exists nowhere in the repo.
                    self.result.external_refs += 1
                else:
                    self.result.unresolved += 1
                continue

            target, confidence = self._resolve_direct(name, call_file, family, relation)
            if not target:
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(target), relation,
                confidence=confidence, line=fact.get("line"),
            )

    def _resolve_within_record(self, record_id: str, name: str) -> str | None:
        for block_id in self.index.blocks_by_record.get(record_id, ()):
            row = self.index.rows.get(block_id)
            if row and row.name == name:
                return block_id
        return None

    def _resolve_direct(self, name: str, call_file: str, family: str | None,
                        relation: str) -> tuple[str | None, str]:
        candidates = self.index.candidates(name)
        if not candidates:
            # Nothing in the repo defines this name at all: a third-party or
            # stdlib reference, not a resolution failure.
            self.result.external_refs += 1
            return None, CONFIDENCE_INFERRED

        # Cross-language family guard.
        if family:
            candidates = [
                cid for cid in candidates
                if self.index.family_by_block.get(cid) in (None, family)
            ]
        # A type relation must land on a type; a call must land on something callable.
        if relation in _TYPE_RELATIONS:
            typed = [cid for cid in candidates if cid in self.index.type_blocks]
            candidates = typed or candidates
        elif relation in _CALL_RELATIONS:
            # Class exclusion: `select(Model)` is not a call to Model.
            callable_only = [cid for cid in candidates if cid in self.index.callable_blocks]
            if callable_only:
                candidates = callable_only
        if not candidates:
            self.result.unresolved += 1
            return None, CONFIDENCE_INFERRED

        with_evidence = [cid for cid in candidates if self._has_import_evidence(cid, call_file)]
        if with_evidence:
            candidates, confidence = with_evidence, CONFIDENCE_EXTRACTED
        else:
            same_file = [cid for cid in candidates if self.index.file_by_block.get(cid) == call_file]
            if same_file:
                candidates, confidence = same_file, CONFIDENCE_EXTRACTED
            elif family == "js" and relation in _CALL_RELATIONS:
                # ES modules have no implicit cross-module scope: without an
                # import the call cannot be reaching this definition.
                self.result.unresolved += 1
                return None, CONFIDENCE_INFERRED
            else:
                confidence = CONFIDENCE_INFERRED

        candidates = prefer_non_test(candidates, self.index.file_by_block, call_file)
        candidates = path_proximity_winner(candidates, self.index.file_by_block, call_file)
        if len(candidates) != 1:
            self.result.ambiguous_skipped += 1
            return None, confidence
        return candidates[0], confidence

    def _resolve_module_qualified_call(self, fact: dict, record_id: str) -> str | None:
        """`import analysis; analysis.analyze_game()`.

        The receiver is a module, not a typed variable, so the type table can
        never carry it. Resolve the receiver as an import specifier instead and
        look the name up among that file's own definitions.
        """
        receiver = fact.get("receiver")
        name = fact.get("toName") or ""
        if not receiver or not name:
            return None

        from_file = self.index.file_by_record.get(record_id) or ""
        family = lang_family(None, from_file)
        # The full dotted prefix first (`pkg.mod.fn()`), then the bare receiver.
        for specifier in filter(None, (fact.get("qualifiedPrefix"), receiver)):
            target_file = self.imports.resolve(specifier, from_file, family)
            if not target_file:
                continue
            target_file = self.imports.walk_barrel_chain(target_file, self.re_exports)
            target_record = self.index.record_by_file.get(target_file)
            if not target_record:
                continue
            for candidate in self.index.blocks_by_record.get(target_record, ()):
                row = self.index.rows.get(candidate)
                if row and row.name == name and not row.parent_block_id:
                    return candidate
        return None

    def _resolve_member_call(self, block_id: str, fact: dict, record_id: str) -> str | None:
        """`obj.method()` -- find the receiver's type, then its method."""
        receiver_type = fact.get("receiverType")
        receiver = fact.get("receiver")
        if not receiver_type and receiver:
            receiver_type = self.index.type_table_by_record.get(record_id, {}).get(receiver)
        if not receiver_type and receiver in ("self", "this"):
            row = self.index.rows.get(block_id)
            if row and row.parent_block_id:
                return self.index.method_index.get((row.parent_block_id, fact.get("toName") or ""))
        if not receiver_type:
            return self._resolve_module_qualified_call(fact, record_id)

        type_candidates = [
            cid for cid in self.index.candidates(receiver_type)
            if cid in self.index.type_blocks
        ]
        if len(type_candidates) != 1:  # god-node guard
            if type_candidates:
                self.result.ambiguous_skipped += 1
            return None
        return self.index.method_index.get((type_candidates[0], fact.get("toName") or ""))

    # -- step 8: prune ---------------------------------------------------

    def prune_dangling(self) -> list[dict]:
        """Drop any edge whose endpoint is not a live node.

        The last safety net: a resolver bug can only make the graph smaller,
        never wrong.
        """
        live_blocks = set(self.index.rows)
        live_records = set(self.index.blocks_by_record)
        kept: list[dict] = []
        for edge in self.edges.values():
            if not self._endpoint_live(edge["_from"], live_blocks, live_records):
                continue
            if not self._endpoint_live(edge["_to"], live_blocks, live_records):
                continue
            kept.append(edge)
        return kept

    @staticmethod
    def _endpoint_live(key: str, live_blocks: set[str], live_records: set[str]) -> bool:
        collection, _, ident = key.partition("/")
        if collection == CollectionNames.BLOCKS.value:
            return ident in live_blocks
        if collection == CollectionNames.RECORDS.value:
            return ident in live_records
        return False


def _chunks(items: list, size: int = _WRITE_CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


async def build_code_graph_edges(
    *,
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_group_id: str,
    touched_record_ids: set[str] | None = None,
    dry_run: bool = False,
    log: logging.Logger | None = None,
) -> EdgeBuildResult:
    """Resolve and write the repo's cross-file edges.

    ``touched_record_ids`` selects an incremental run: the symbol table is still
    built from *every* block, but only the touched files and their dependents are
    re-resolved.
    """
    started = time.monotonic()
    log = log or logger

    docs = await _scan_blocks(graph_provider, org_id, record_group_id)
    paths = await _scan_file_paths(graph_provider, org_id, docs)
    index = build_symbol_index(docs, paths)

    builder = _Builder(index, org_id, record_group_id, log)
    builder.result.blocks = len(docs)
    builder.result.files_total = len(index.blocks_by_record)

    reresolve = _select_reresolve_set(index, touched_record_ids)
    if touched_record_ids:
        reresolve |= await _records_pointing_into(
            graph_provider, index, org_id, record_group_id, touched_record_ids
        )
    builder.result.files_touched = len(touched_record_ids or ())
    builder.result.files_reresolved = len(reresolve)

    work: list[tuple[str, dict]] = []
    for block_id, row in index.rows.items():
        if row.record_id not in reresolve:
            continue
        for fact in row.pending_edges:
            work.append((block_id, fact))
    builder.result.pending_edges_seen = len(work)

    builder.resolve_imports(work)
    builder.resolve_rest(work)
    edges = builder.prune_dangling()

    if not dry_run:
        source_keys = _source_keys_for(index, reresolve)
        removed = await _delete_previous_edges(graph_provider, org_id, record_group_id, source_keys)
        builder.result.edges_removed = removed
        for chunk in _chunks(edges):
            await graph_provider.batch_upsert_record_relations(chunk)
    builder.result.edges_written = len(edges)
    builder.result.duration_s = time.monotonic() - started
    return builder.result


def _select_reresolve_set(index: SymbolIndex, touched: set[str] | None) -> set[str]:
    """Touched files plus the unchanged files whose edges they invalidate.

    A change in B can break an edge belonging to A: B deleting a symbol leaves
    A's edge dangling, B adding one means A's unresolved reference should now
    resolve. Neither shows up in A's own timestamp.
    """
    if not touched:
        return set(index.blocks_by_record)

    selected = set(touched)

    # B added or removed a name -> re-resolve anything that references that name.
    touched_names: set[str] = set()
    for record_id in touched:
        touched_names |= index.names_by_record.get(record_id, set())

    if touched_names:
        for block_id, row in index.rows.items():
            if row.record_id in selected:
                continue
            for fact in row.pending_edges:
                if fact.get("toName") in touched_names:
                    selected.add(row.record_id)
                    break
    return selected


def _source_keys_for(index: SymbolIndex, record_ids: set[str]) -> list[str]:
    """Every `_from` key an edge produced by these records could carry."""
    keys: list[str] = []
    for record_id in record_ids:
        keys.append(f"{CollectionNames.RECORDS.value}/{record_id}")
        keys.extend(
            f"{CollectionNames.BLOCKS.value}/{block_id}"
            for block_id in index.blocks_by_record.get(record_id, ())
        )
    return keys


async def _records_pointing_into(
    graph_provider: IGraphDBProvider,
    index: SymbolIndex,
    org_id: str,
    record_group_id: str,
    touched: set[str],
) -> set[str]:
    """Records whose existing edges land inside a touched file.

    If B deletes or renames a symbol, A's edge into it is now dangling even
    though A never changed, so A has to be re-resolved to drop it.
    """
    target_keys = _source_keys_for(index, touched)
    if not target_keys:
        return set()

    found: set[str] = set()
    for chunk in _chunks(target_keys, 2000):
        rows = await graph_provider.get_edges_by_target_keys(
            target_keys=chunk,
            edge_collection=CollectionNames.RECORD_RELATIONS.value,
            filters={
                "source": CODE_GRAPH_SOURCE,
                "orgId": org_id,
                "recordGroupId": record_group_id,
            },
        )
        for key in rows:
            if not isinstance(key, str):
                continue
            collection, _, ident = key.partition("/")
            if collection == CollectionNames.RECORDS.value:
                found.add(ident)
            elif collection == CollectionNames.BLOCKS.value:
                record_id = index.record_by_block.get(ident)
                if record_id:
                    found.add(record_id)
    return found


async def _delete_previous_edges(graph_provider: IGraphDBProvider, org_id: str,
                                 record_group_id: str, source_keys: list[str]) -> int:
    """Remove prior code_graph edges sourced by the files being re-resolved.

    Scoped by `_from`: an unscoped delete would erase edges belonging to files
    this run is not going to rebuild.
    """
    if not source_keys:
        return 0

    removed = 0
    for chunk in _chunks(source_keys, 2000):
        removed += await graph_provider.delete_edges_by_source_keys(
            source_keys=chunk,
            edge_collection=CollectionNames.RECORD_RELATIONS.value,
            filters={
                "source": CODE_GRAPH_SOURCE,
                "orgId": org_id,
                "recordGroupId": record_group_id,
            },
        )
    return removed
