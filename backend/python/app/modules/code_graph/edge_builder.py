"""Corpus-wide edge resolution for a code repository.

Ordering is load-bearing. Imports resolve first because their output is the
evidence table every later step consults; member calls resolve last because they
need both the type tables and the method index.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations, RecordTypes
from app.modules.code_graph.facts_source import CodeFactsSource
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

# Exactly what `BlockRow.from_doc` reads; the rest of the block document (its
# text, hashes, timestamps) never reaches the symbol index.
_BLOCK_SCAN_FIELDS = [
    "_key", "recordId", "name", "kind", "qualifiedName", "language",
    "isBlockGroup", "parentBlockId", "pendingEdges", "typeTable", "referencedNames",
]

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

# Kinds a grammar spells out as a contract rather than an implementation.
# `trait` is Rust's, `protocol` Swift's; both behave like an interface here.
_INTERFACE_KINDS = frozenset({"interface", "trait", "protocol"})

# Python has no interface keyword -- `class Foo(ABC)` is the idiom, and the
# heritage fact naming the base is already on the class block, so no new
# indexed field is needed to recognise it. `@abstractmethod` would be the
# sharper signal but decorators are not projected onto block nodes.
_ABSTRACT_BASE_NAMES = frozenset({"ABC", "ABCMeta", "Protocol"})

# Why an edge exists, stored on it so a wrong edge can be traced to its rule.
RESOLUTION_IMPORT = "import-binding"
RESOLUTION_SAME_FILE = "same-file"
RESOLUTION_QUALIFIER = "qualifier"
RESOLUTION_MODULE = "module-import"
RESOLUTION_TYPE = "receiver-type"
RESOLUTION_NAME = "name-heuristic"

# A local name an import binds to something outside the repo (`from requests
# import get`). It shadows every repo definition of that name in the file.
_EXTERNAL = ""

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
    # Re-resolved files whose full facts came from the blob, and those whose
    # blob could not be read and were resolved from their node residue alone.
    facts_loaded: int = 0
    facts_missing: int = 0
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
            "facts_loaded": self.facts_loaded,
            "facts_missing": self.facts_missing,
            "duration_s": round(self.duration_s, 2),
        }


async def _scan_blocks(graph_provider: IGraphDBProvider, org_id: str,
                       record_group_id: str) -> list[dict]:
    """Keyset sweep of every block in the repo, projected to the index's fields.

    A growing offset makes each page rescan everything before it; seeking on
    `_key > last` keeps every page the same cost.
    """
    out: list[dict] = []
    after_key: str | None = None
    filters = {"orgId": org_id, "recordGroupId": record_group_id}
    while True:
        page = await graph_provider.get_documents_paginated(
            collection=CollectionNames.BLOCKS.value,
            limit=_SCAN_PAGE,
            filters=filters,
            sort_field="_key",
            after_key=after_key,
            return_fields=_BLOCK_SCAN_FIELDS,
        )
        if not page:
            break
        out.extend(page)
        if len(page) < _SCAN_PAGE:
            break
        after_key = page[-1].get("_key")
        if not after_key:
            break
    return out


async def _scan_file_info(graph_provider: IGraphDBProvider, org_id: str,
                          docs: list[dict]) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """(recordId -> filePath, recordId -> referencedNames) for every record.

    Resolution is path-driven -- a relative import resolves against the
    importing file's directory, and the proximity guards score on it -- but the
    path lives on the codeFiles node, so it is fetched once per record rather
    than read off every block. referencedNames is also on codeFiles.
    """
    record_ids = sorted({doc.get("recordId") for doc in docs if doc.get("recordId")})
    if not record_ids:
        return {}, {}
    paths: dict[str, str] = {}
    referenced: dict[str, tuple[str, ...]] = {}
    for chunk in _chunks(record_ids, 2000):
        info = await graph_provider.get_file_paths_for_records(
            org_id=org_id, record_ids=chunk
        )
        for record_id, file_info in info.items():
            paths[record_id] = file_info.file_path
            if file_info.referenced_names:
                referenced[record_id] = file_info.referenced_names
    return paths, referenced


async def _scan_repo_paths(graph_provider: IGraphDBProvider, org_id: str,
                           record_group_id: str) -> set[str]:
    """Every code file in the repo, including those with no blocks.

    Import resolution needs the empty ones: an empty `__init__.py` is what
    makes its directory a package, and without it `import requests` matches
    `app/legacy/requests.py` as though `legacy/` were a source root.
    """
    rows = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id,
                 "recordType": RecordTypes.CODE_FILE.value},
        return_fields=["_key"],
        raise_on_error=True,
    )
    record_ids = sorted({row["_key"] for row in rows if row.get("_key")})
    paths: set[str] = set()
    for chunk in _chunks(record_ids, 2000):
        info = await graph_provider.get_file_paths_for_records(org_id=org_id, record_ids=chunk)
        paths.update(file_info.file_path for file_info in info.values() if file_info.file_path)
    return paths


class _Builder:
    def __init__(self, index: SymbolIndex, org_id: str, record_group_id: str,
                 log: logging.Logger, repo_paths: set[str] | None = None) -> None:
        self.index = index
        self.org_id = org_id
        self.record_group_id = record_group_id
        self.log = log
        self.result = EdgeBuildResult()
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.external_nodes: dict[str, dict] = {}
        # file -> imported module files, the evidence a module-level reference uses
        self.module_imports: dict[str, set[str]] = {}
        # file -> local name -> what the file's imports bind it to: a block for
        # `from m import f`, a file for `import m` / `import * as m`, or
        # `_EXTERNAL`. Calls resolve through these before any name matching.
        self.symbol_bindings: dict[str, dict[str, str]] = {}
        self.module_bindings: dict[str, dict[str, str]] = {}
        self.re_exports: dict[str, set[str]] = {}
        self.imports = ImportResolution(set(index.record_by_file) | (repo_paths or set()))
        self._interface_memo: dict[str, bool] = {}
        self._bases_memo: dict[str, list[str]] = {}

    # -- emit -----------------------------------------------------------

    def _add_edge(self, from_key: str, to_key: str, relation: str, *,
                  confidence: str, line: int | None, resolution: str | None = None) -> None:
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
            "resolution": resolution,
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
        self._collect_re_exports()
        for block_id, fact in work:
            relation = fact.get("relation")
            if relation not in _MODULE_RELATIONS:
                continue
            record_id = self.index.record_by_block.get(block_id, "")
            from_file = self.index.file_by_record.get(record_id)
            family = self.index.family_by_block.get(block_id) or lang_family(None, from_file)
            specifier = fact.get("toName") or ""
            target_file = self.imports.resolve(specifier, from_file or "", family)
            if relation == RecordRelations.IMPORTS_FROM.value:
                # `import m` binds `m`; a JS import binds a module only as `* as ns`.
                local = fact.get("localName") or (specifier if family == "py" else None)
                if local:
                    self.module_bindings.setdefault(from_file or "", {})[local] = (
                        target_file or _EXTERNAL
                    )
            if not target_file:
                # A bare specifier (`react`, `os`) resolves to no repo file.
                self.result.external_refs += 1
                continue
            self.module_imports.setdefault(from_file or "", set()).add(target_file)
            target_record = self.index.record_by_file.get(target_file)
            if not target_record:
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._record_key(target_record), relation,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                resolution=RESOLUTION_IMPORT,
            )

        # Symbol-level imports bind a local name and, when the symbol is in the
        # repo, become an edge to the definition itself.
        for block_id, fact in work:
            if fact.get("relation") == RecordRelations.IMPORTS.value:
                self._bind_symbol_import(block_id, fact)

    def _bind_symbol_import(self, block_id: str, fact: dict) -> None:
        record_id = self.index.record_by_block.get(block_id, "")
        from_file = self.index.file_by_record.get(record_id) or ""
        name = fact.get("toName") or ""
        local = fact.get("localName") or name
        bindings = self.symbol_bindings.setdefault(from_file, {})

        family = self.index.family_by_block.get(block_id) or lang_family(None, from_file)
        specifier = fact.get("qualifiedPrefix") or ""
        target_file = self.imports.resolve(specifier, from_file, family)
        if not target_file:
            # A package import (`requests`, `lodash`). Matching the name across
            # the repo instead is how `from requests import get` used to land
            # on a repo `get` and an import of `logging` on telemetry/logging.py.
            bindings[local] = _EXTERNAL
            self.result.external_refs += 1
            return
        target_file = self.imports.walk_barrel_chain(target_file, self.re_exports)
        candidates = [cid for cid in self.index.candidates(name)
                      if self.index.file_by_block.get(cid) == target_file]
        top_level = [cid for cid in candidates if not self._parent_of(cid)]
        candidates = top_level or candidates
        if len(candidates) == 1:
            bindings[local] = candidates[0]
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(candidates[0]),
                RecordRelations.IMPORTS.value,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                resolution=RESOLUTION_IMPORT,
            )
            return
        if candidates:
            self.result.ambiguous_skipped += 1
            return
        submodule = self._python_submodule(specifier, name, from_file, family)
        if submodule:
            # `from app.analytics import jobs`: the name is a module, not a symbol.
            self.module_bindings.setdefault(from_file, {})[local] = submodule
            self.module_imports.setdefault(from_file, set()).add(submodule)
            self._add_edge(
                self._source_key(block_id, fact),
                self._record_key(self.index.record_by_file[submodule]),
                RecordRelations.IMPORTS_FROM.value,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                resolution=RESOLUTION_IMPORT,
            )
        # Otherwise the file re-exports the name from elsewhere (a Python
        # `__init__`); it stays unbound and resolves by the weaker rules.

    def _python_submodule(self, specifier: str, name: str, from_file: str,
                          family: str | None) -> str | None:
        if family != "py" or not specifier:
            return None
        joined = f"{specifier}{name}" if specifier.endswith(".") else f"{specifier}.{name}"
        target = self.imports.resolve(joined, from_file, family)
        return target if target in self.index.record_by_file else None

    def _parent_of(self, block_id: str) -> str | None:
        row = self.index.rows.get(block_id)
        return row.parent_block_id if row else None

    def _collect_re_exports(self) -> None:
        """Barrel hops, read from every row rather than the re-resolve slice.

        `walk_barrel_chain` looks up barrels an incremental run is not
        re-resolving, so building this from `work` alone would stop the walk at
        the barrel and drop the import edge to the defining module.
        """
        for block_id, row in self.index.rows.items():
            from_file = row.file_path or ""
            if not from_file:
                continue
            for fact in row.pending_edges:
                if fact.get("relation") != RecordRelations.RE_EXPORTS.value:
                    continue
                family = (self.index.family_by_block.get(block_id)
                          or lang_family(None, from_file))
                target_file = self.imports.resolve(
                    fact.get("toName") or "", from_file, family
                )
                if target_file:
                    self.re_exports.setdefault(from_file, set()).add(target_file)

    # -- evidence -------------------------------------------------------

    def _family_of(self, block_id: str) -> str | None:
        return (self.index.family_by_block.get(block_id)
                or lang_family(None, self.index.file_by_block.get(block_id)))

    def _has_module_evidence(self, candidate_id: str, call_site_file: str) -> bool:
        """The call site's file imports the module that defines the candidate.

        Deliberately not "imports some symbol of that name": that matched any
        same-named definition, so an imported `validate` lent its evidence to a
        same-directory decoy.
        """
        row = self.index.rows.get(candidate_id)
        return row is not None and (row.file_path or "") in self.module_imports.get(call_site_file, ())

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
                target, confidence, resolution = self._resolve_member_call(block_id, fact, record_id)
                if target:
                    self._add_edge(
                        self._source_key(block_id, fact), self._block_key(target),
                        RecordRelations.CALLS.value,
                        confidence=confidence, line=fact.get("line"), resolution=resolution,
                    )
                elif resolution == _EXTERNAL or not self.index.candidates(name):
                    # `resp.json()` on a third-party object -- the method name
                    # exists nowhere in the repo.
                    self.result.external_refs += 1
                else:
                    self.result.unresolved += 1
                continue

            target, confidence, resolution = self._resolve_direct(
                name, call_file, family, relation, fact.get("qualifiedPrefix"),
            )
            if not target:
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(target),
                self._heritage_relation(relation, target),
                confidence=confidence, line=fact.get("line"), resolution=resolution,
            )

    def _heritage_relation(self, relation: str, target: str) -> str:
        """`INHERITS` becomes `IMPLEMENTS` when the base turns out to be a contract.

        Only decidable here. The parser sees `class Foo(ABC)` and can emit the
        base's NAME; whether that name is an interface is known once it
        resolves to a block. A base that resolves to nothing stays `INHERITS` --
        a third-party class we cannot inspect is not evidence of a contract.
        """
        if relation != RecordRelations.INHERITS.value:
            return relation
        return (RecordRelations.IMPLEMENTS.value if self._is_interface(target)
                else relation)

    def _is_interface(self, block_id: str) -> bool:
        """Whether a resolved type block is an interface or an abstract base."""
        cached = self._interface_memo.get(block_id)
        if cached is not None:
            return cached
        row = self.index.rows.get(block_id)
        verdict = bool(row) and (
            (row.kind or "").lower() in _INTERFACE_KINDS
            or any(
                fact.get("relation") in _TYPE_RELATIONS
                and (fact.get("toName") or "") in _ABSTRACT_BASE_NAMES
                for fact in row.pending_edges
            )
        )
        self._interface_memo[block_id] = verdict
        return verdict

    # -- step 7b: overrides ---------------------------------------------

    def link_overrides(self) -> None:
        """``C.m --OVERRIDES--> B.m`` for every resolved base that defines ``m``.

        The base does not have to be an interface. A call typed as the base
        still lands on the base method, and this edge is what an inbound walk
        follows to the implementation. If the direct base does not define the
        method, the nearest ancestor that does is the target, so a middle class
        that only inherits ``run`` does not hide ``A.run`` from ``C``. A middle
        class that defines ``run`` stops the walk: ``C.run`` overrides
        ``B.run``, and ``B.run`` overrides ``A.run``.

        Runs after `resolve_rest`, over the heritage edges it just wrote. The
        subclass's heritage fact and its methods live in the same record, so an
        incremental run that re-resolves a subclass re-derives its overrides in
        the same pass.
        """
        methods_by_owner: dict[str, list[tuple[str, str]]] = {}
        for (owner, method_name), block_id in self.index.method_index.items():
            methods_by_owner.setdefault(owner, []).append((method_name, block_id))

        for from_key, to_key, relation in list(self.edges):
            if relation not in _TYPE_RELATIONS:
                continue
            base = to_key.rpartition("/")[2]
            child = from_key.rpartition("/")[2]
            for method_name, block_id in methods_by_owner.get(child, ()):
                # Start at the base, not the child: the child's own method
                # would otherwise match and the edge would be a self-loop.
                base_method = self._resolve_self_method(base, method_name)
                if not base_method or base_method == block_id:
                    continue
                self._add_edge(
                    self._block_key(block_id), self._block_key(base_method),
                    RecordRelations.OVERRIDES.value,
                    # Proven, not guessed: a heritage edge the parser extracted
                    # plus an exact name match on the base chain.
                    confidence=CONFIDENCE_EXTRACTED, line=None, resolution=RESOLUTION_TYPE,
                )

    def _resolve_within_record(self, record_id: str, name: str) -> str | None:
        for block_id in self.index.blocks_by_record.get(record_id, ()):
            row = self.index.rows.get(block_id)
            if row and row.name == name:
                return block_id
        return None

    def _resolve_direct(self, name: str, call_file: str, family: str | None,
                        relation: str, qualified_prefix: str | None = None,
                        ) -> tuple[str | None, str, str | None]:
        """Name -> definition, strongest evidence first.

        An import binding decides outright; then a dotted qualifier, a
        same-file definition, an imported module defining the name; a bare-name
        guess comes last and is only ever INFERRED.
        """
        bound = self.symbol_bindings.get(call_file, {}).get(name) if not qualified_prefix else None
        if bound == _EXTERNAL:
            self.result.external_refs += 1
            return None, CONFIDENCE_INFERRED, None
        if (bound and (family is None or self._family_of(bound) == family)
                and (relation not in _TYPE_RELATIONS or bound in self.index.type_blocks)):
            return bound, CONFIDENCE_EXTRACTED, RESOLUTION_IMPORT

        candidates = self.index.candidates(name)
        if not candidates:
            # Nothing in the repo defines this name at all: a third-party or
            # stdlib reference, not a resolution failure.
            self.result.external_refs += 1
            return None, CONFIDENCE_INFERRED, None

        if family:
            candidates = [cid for cid in candidates if self._family_of(cid) == family]
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
            return None, CONFIDENCE_INFERRED, None

        # `pkg.mod.Parent` and `Outer.Inner` are evidence the bare name is not.
        # A qualifier that does not leave exactly one candidate is ignored, so
        # an unresolved module path still falls through to import and proximity.
        pinned = self._prefix_candidates(candidates, qualified_prefix, call_file, family)
        if pinned:
            return pinned[0], CONFIDENCE_EXTRACTED, RESOLUTION_QUALIFIER

        same_file = [cid for cid in candidates if self.index.file_by_block.get(cid) == call_file]
        with_evidence = [cid for cid in candidates if self._has_module_evidence(cid, call_file)]
        if same_file:
            candidates, confidence, resolution = same_file, CONFIDENCE_EXTRACTED, RESOLUTION_SAME_FILE
        elif with_evidence:
            candidates, confidence, resolution = with_evidence, CONFIDENCE_EXTRACTED, RESOLUTION_MODULE
        elif family == "js" and relation in _CALL_RELATIONS:
            # ES modules have no implicit cross-module scope: without an
            # import the call cannot be reaching this definition.
            self.result.unresolved += 1
            return None, CONFIDENCE_INFERRED, None
        else:
            confidence, resolution = CONFIDENCE_INFERRED, RESOLUTION_NAME

        candidates = prefer_non_test(candidates, self.index.file_by_block, call_file)
        candidates = path_proximity_winner(candidates, self.index.file_by_block, call_file)
        if len(candidates) != 1:
            self.result.ambiguous_skipped += 1
            return None, confidence, None
        return candidates[0], confidence, resolution

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

    def _resolve_member_call(self, block_id: str, fact: dict, record_id: str,
                             ) -> tuple[str | None, str, str | None]:
        """`obj.method()` -- find the receiver's type, then its method.

        Returns ``(target, confidence, resolution)``; a resolution of
        ``_EXTERNAL`` with no target means the receiver is a third-party or
        stdlib module (`logging.getLogger`).
        """
        name = fact.get("toName") or ""
        receiver_type = fact.get("receiverType")
        receiver = fact.get("receiver")
        if not receiver_type and receiver:
            receiver_type = self.index.type_table_by_record.get(record_id, {}).get(receiver)
        if not receiver_type and receiver in ("self", "this"):
            owner = self._enclosing_type(block_id)
            if owner:
                return self._resolve_self_method(owner, name), CONFIDENCE_INFERRED, RESOLUTION_TYPE
        if not receiver_type and receiver:
            bound = self._resolve_bound_receiver(receiver, name, record_id)
            if bound is not None:
                return bound
        if not receiver_type:
            target = self._resolve_module_qualified_call(fact, record_id)
            return target, CONFIDENCE_INFERRED, RESOLUTION_MODULE if target else None

        family = self._family_of(block_id)
        type_candidates = [
            cid for cid in self.index.candidates(receiver_type)
            if cid in self.index.type_blocks
            and (family is None or self._family_of(cid) == family)
        ]
        if len(type_candidates) != 1:  # god-node guard
            if type_candidates:
                self.result.ambiguous_skipped += 1
            return None, CONFIDENCE_INFERRED, None
        return (self._resolve_self_method(type_candidates[0], name),
                CONFIDENCE_INFERRED, RESOLUTION_TYPE)

    def _resolve_bound_receiver(self, receiver: str, name: str, record_id: str,
                                ) -> tuple[str | None, str, str | None] | None:
        """`jobs.run()`, `ns.f()`, `Order.from_dict()`: a receiver an import or a
        same-file class names. None when the receiver is none of those."""
        call_file = self.index.file_by_record.get(record_id) or ""
        module = self.module_bindings.get(call_file, {}).get(receiver)
        if module == _EXTERNAL:
            return None, CONFIDENCE_INFERRED, _EXTERNAL
        if module:
            target = self._top_level_in_file(module, name)
            return target, CONFIDENCE_EXTRACTED, RESOLUTION_IMPORT if target else None
        bound = self.symbol_bindings.get(call_file, {}).get(receiver)
        if bound == _EXTERNAL:
            return None, CONFIDENCE_INFERRED, _EXTERNAL
        if not bound:
            local = [cid for cid in self.index.blocks_by_record.get(record_id, ())
                     if cid in self.index.type_blocks
                     and (self.index.rows[cid].name if cid in self.index.rows else None) == receiver]
            bound = local[0] if len(local) == 1 else None
        if bound and bound in self.index.type_blocks:
            target = self._resolve_self_method(bound, name)
            return target, CONFIDENCE_EXTRACTED, RESOLUTION_IMPORT if target else None
        return None

    def _top_level_in_file(self, file_path: str, name: str) -> str | None:
        record = self.index.record_by_file.get(file_path)
        for candidate in self.index.blocks_by_record.get(record, ()):
            row = self.index.rows.get(candidate)
            if row and row.name == name and not row.parent_block_id:
                return candidate
        return None

    def _enclosing_type(self, block_id: str) -> str | None:
        """The class a block sits in, through any intermediate scopes.

        A `self.x()` inside a closure nested in a method has the method as its
        parent, so a single parent hop would miss the class.
        """
        seen: set[str] = set()
        row = self.index.rows.get(block_id)
        while row is not None and row.parent_block_id and row.parent_block_id not in seen:
            parent_id = row.parent_block_id
            seen.add(parent_id)
            if parent_id in self.index.type_blocks:
                return parent_id
            row = self.index.rows.get(parent_id)
        return None

    def _resolve_self_method(self, owner: str, method_name: str) -> str | None:
        """`m` on a known type: the owner's own `m`, else the nearest base that defines it.

        Serves `self.m()` and `conn.m()` on a typed receiver alike.

        Calling a method the subclass never overrode is the common case, not an
        edge case: `BaseConnector.notify` has ~38 `await self.notify(...)` call
        sites across the connectors and every one of them resolved to nothing
        before this walk, leaving the method with no inbound CALLS at all.
        """
        if not method_name:
            return None
        direct = self.index.method_index.get((owner, method_name))
        if direct:
            return direct

        seen = {owner}
        queue = list(self._base_types_of(owner))
        while queue:
            base = queue.pop(0)
            if base in seen:
                continue
            seen.add(base)
            inherited = self.index.method_index.get((base, method_name))
            if inherited:
                return inherited
            queue.extend(self._base_types_of(base))
        return None

    def _base_types_of(self, type_block: str) -> list[str]:
        """Resolved base classes of a type, from its own heritage facts.

        Read from `pending_edges` rather than `self.edges`: `resolve_rest`
        interleaves heritage and call facts, so a subclass's heritage edge may
        not be written yet when one of its calls needs it.
        """
        cached = self._bases_memo.get(type_block)
        if cached is not None:
            return cached

        bases: list[str] = []
        row = self.index.rows.get(type_block)
        if row is not None:
            family = self._family_of(type_block)
            for fact in row.pending_edges:
                if fact.get("relation") not in _TYPE_RELATIONS:
                    continue
                target = self._resolve_type_name(
                    fact.get("toName") or "", row.file_path or "", family,
                    fact.get("qualifiedPrefix"),
                )
                if target:
                    bases.append(target)
        self._bases_memo[type_block] = bases
        return bases

    def _prefix_candidates(self, candidates: list[str], prefix: str | None,
                           from_file: str, family: str | None) -> list[str] | None:
        """One candidate selected by a dotted qualifier, or None to ignore it.

        A qualifier that resolves to a repo file (`pkg.mod.Parent`) keeps only
        definitions in that file. Otherwise the last segment is the enclosing
        type (`Outer.Inner`). Anything other than exactly one survivor is not
        evidence: the caller falls through to the bare-name ladder.
        """
        if not prefix:
            return None
        target_file = self.imports.resolve(prefix, from_file, family)
        if target_file:
            target_file = self.imports.walk_barrel_chain(target_file, self.re_exports)
            narrowed = [
                cid for cid in candidates
                if self.index.file_by_block.get(cid) == target_file
            ]
        else:
            parent_name = prefix.rsplit(".", 1)[-1]
            narrowed = [
                cid for cid in candidates if self._parent_name(cid) == parent_name
            ]
        return narrowed if len(narrowed) == 1 else None

    def _parent_name(self, block_id: str) -> str | None:
        row = self.index.rows.get(block_id)
        if row is None or not row.parent_block_id:
            return None
        parent = self.index.rows.get(row.parent_block_id)
        return parent.name if parent else None

    def _resolve_type_name(self, name: str, from_file: str, family: str | None,
                           qualified_prefix: str | None = None) -> str | None:
        """Name -> type block. Deliberately without `_resolve_direct`'s counters:
        a heritage hop taken while resolving a call is not itself a resolution
        the run should report on."""
        if not name:
            return None
        bound = self.symbol_bindings.get(from_file, {}).get(name) if not qualified_prefix else None
        if bound == _EXTERNAL:
            return None
        if bound in self.index.type_blocks:
            return bound
        candidates = [
            cid for cid in self.index.candidates(name) if cid in self.index.type_blocks
        ]
        if family:
            candidates = [
                cid for cid in candidates
                if self._family_of(cid) == family
            ]
        pinned = self._prefix_candidates(candidates, qualified_prefix, from_file, family)
        if pinned:
            return pinned[0]
        if len(candidates) > 1:
            same_file = [cid for cid in candidates
                         if self.index.file_by_block.get(cid) == from_file]
            with_evidence = same_file or [
                cid for cid in candidates if self._has_module_evidence(cid, from_file)
            ]
            candidates = with_evidence or candidates
            candidates = prefer_non_test(candidates, self.index.file_by_block, from_file)
            candidates = path_proximity_winner(
                candidates, self.index.file_by_block, from_file
            )
        return candidates[0] if len(candidates) == 1 else None

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
    facts_source: CodeFactsSource | None = None,
    dry_run: bool = False,
    log: logging.Logger | None = None,
) -> EdgeBuildResult:
    """Resolve and write the repo's cross-file edges.

    ``touched_record_ids`` selects an incremental run: the symbol table is still
    built from *every* block, but only the touched files and their dependents are
    re-resolved.

    ``facts_source`` supplies the full facts of the files being re-resolved;
    their nodes carry only the resident relations. Without one, node facts are
    used as they are, which is complete only for blocks projected before the
    residue change.
    """
    started = time.monotonic()
    log = log or logger

    docs = await _scan_blocks(graph_provider, org_id, record_group_id)
    paths, referenced_names = await _scan_file_info(graph_provider, org_id, docs)
    index = build_symbol_index(docs, paths, referenced_names)
    repo_paths = await _scan_repo_paths(graph_provider, org_id, record_group_id)

    builder = _Builder(index, org_id, record_group_id, log, repo_paths)
    builder.result.blocks = len(docs)
    builder.result.files_total = len(index.blocks_by_record)

    reresolve = _select_reresolve_set(index, touched_record_ids)
    if touched_record_ids:
        reresolve |= await _records_pointing_into(
            graph_provider, index, org_id, record_group_id, touched_record_ids
        )
    builder.result.files_touched = len(touched_record_ids or ())
    builder.result.files_reresolved = len(reresolve)

    if facts_source is not None:
        await _load_facts(facts_source, index, org_id, reresolve, builder.result, log)

    work: list[tuple[str, dict]] = []
    for block_id, row in index.rows.items():
        if row.record_id not in reresolve:
            continue
        for fact in row.pending_edges:
            work.append((block_id, fact))
    builder.result.pending_edges_seen = len(work)

    builder.resolve_imports(work)
    builder.resolve_rest(work)
    builder.link_overrides()
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


async def _load_facts(
    source: CodeFactsSource,
    index: SymbolIndex,
    org_id: str,
    record_ids: set[str],
    result: EdgeBuildResult,
    log: logging.Logger,
) -> None:
    """Swap the node residue for the blob's full facts on every re-resolved file.

    Runs after the re-resolve set is chosen, which only needs what the nodes
    carry. A file whose blob cannot be read keeps its node facts, so its call
    and import edges stay missing until the blob is readable.
    """
    loaded = await source.load(org_id, record_ids)
    for record_id in record_ids:
        facts = loaded.get(record_id)
        if facts is None:
            result.facts_missing += 1
            log.warning(
                "No stored facts for record %s; resolving from its node residue only",
                record_id,
            )
            continue
        result.facts_loaded += 1
        for block_id in index.blocks_by_record.get(record_id, ()):
            index.rows[block_id] = replace(
                index.rows[block_id], pending_edges=facts.pending_edges.get(block_id, [])
            )
        if facts.type_table:
            index.type_table_by_record[record_id] = facts.type_table


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
        for record_id, names in index.referenced_names_by_record.items():
            if record_id not in selected and names & touched_names:
                selected.add(record_id)
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
