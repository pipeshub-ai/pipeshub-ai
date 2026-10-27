"""Corpus-wide edge resolution for a code repository.

Ordering is load-bearing. Imports resolve first because their output is the
evidence table every later step consults; member calls resolve last because they
need both the type tables and the method index.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, RecordRelations, RecordTypes
from app.modules.code_graph.guards import (
    lang_family,
    path_proximity_winner,
    prefer_non_test,
)
from app.modules.code_graph.import_resolution import ImportResolution
from app.modules.code_graph.module_refs import module_refs_of_path
from app.modules.code_graph.symbol_index import SymbolIndex
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Iterator

    from app.modules.code_graph.facts_source import CodeFactsSource
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["CODE_GRAPH_SOURCE", "EdgeBuildAbortedError", "EdgeBuildResult", "build_code_graph_edges"]

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
# Facts parsed before EXTENDS merged into INHERITS still say EXTENDS (that
# merge re-indexes nothing); they resolve, and are written, as INHERITS.
_LEGACY_EXTENDS = "EXTENDS"
_TYPE_RELATIONS = {
    RecordRelations.INHERITS.value,
    _LEGACY_EXTENDS,
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

# Above this share of re-resolved files without readable facts (and at least
# this many), the store is presumed unavailable rather than a few blobs being
# missing, and the build stops before touching any edge.
_MAX_MISSING_FACTS_SHARE = 0.2
# A build holds every block of the repo in memory (SC-1); past this many it is
# refused instead of risking the connectors service's memory.
# shortcut: a ceiling, not a bounded-memory build; phase 2 streams the index.
MAX_BLOCKS_PER_BUILD = max(1, env_int("CODE_EDGE_BUILD_MAX_BLOCKS", 300000))
# A bare name with no import, same-file or module evidence and more definitions
# than this is left unresolved: a proximity guess among that many is how god
# nodes form, and scoring every candidate per call made builds quadratic.
_MAX_HEURISTIC_CANDIDATES = 64
# A package re-exporting a name re-exported by another: followed this many hops.
_MAX_REEXPORT_HOPS = 4
# Languages where a bare call inside a method may be a call on `this`.
_IMPLICIT_THIS_FAMILIES = frozenset({"jvm", "csharp"})
_MIN_MISSING_FACTS_TO_ABORT = 5

logger = logging.getLogger(__name__)


class EdgeBuildTooLargeError(RuntimeError):
    """The repo has more blocks than one build may load; nothing was read or written."""


class EdgeBuildAbortedError(RuntimeError):
    """The build stopped before writing: the inputs it read cannot be trusted.

    Raised before any edge is deleted, so the graph keeps its previous edges,
    the watermark is not advanced and the repo stays owed a build.
    """


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


class _RepoFiles:
    """recordId -> path and referencedNames, read from codeFiles.

    Resolution is path-driven -- a relative import resolves against the
    importing file's directory, and the proximity guards score on it -- but the
    path lives on the codeFiles node, so it is fetched once per record rather
    than read off every block.
    """

    def __init__(self, graph_provider: IGraphDBProvider, org_id: str) -> None:
        self._graph_provider = graph_provider
        self._org_id = org_id
        self.paths: dict[str, str] = {}
        self.referenced: dict[str, tuple[str, ...]] = {}
        self._asked: set[str] = set()

    async def load(self, record_ids: Iterable[str]) -> None:
        todo = sorted({r for r in record_ids if r and r not in self._asked})
        self._asked.update(todo)
        for chunk in _chunks(todo, 2000):
            info = await self._graph_provider.get_file_paths_for_records(
                org_id=self._org_id, record_ids=chunk, raise_on_error=True
            )
            for record_id, file_info in info.items():
                if file_info.file_path:
                    self.paths[record_id] = file_info.file_path
                if file_info.referenced_names:
                    self.referenced[record_id] = file_info.referenced_names


async def _scan_repo_files(graph_provider: IGraphDBProvider, org_id: str,
                           record_group_id: str) -> _RepoFiles:
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
    files = _RepoFiles(graph_provider, org_id)
    await files.load(row.get("_key") for row in rows)
    return files


async def _index_blocks(graph_provider: IGraphDBProvider, org_id: str,
                        record_group_id: str, files: _RepoFiles) -> SymbolIndex:
    """Keyset sweep of every block in the repo, folded into the index page by page.

    A growing offset makes each page rescan everything before it; seeking on
    `_key > last` keeps every page the same cost. Each page is indexed and
    dropped, so the repo's blocks are never held twice (SC-1).
    """
    index = SymbolIndex()
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
            raise_on_error=True,
        )
        if not page:
            break
        # A block of a record with no codeFiles row in this repo (an upload).
        await files.load(doc.get("recordId") for doc in page)
        await asyncio.to_thread(index.add_docs, page, files.paths, files.referenced)
        if len(page) < _SCAN_PAGE:
            break
        after_key = page[-1].get("_key")
        if not after_key:
            break
    return index


_EXPR_DEPTH = 6
_CLASS_ARG_CALLS = frozenset({"createInstance", "get"})
_SEGMENT = re.compile(r"[A-Za-z_$][\w$]*")


def _receiver_segments(expr: str) -> list[tuple[str, bool, str]] | None:
    """`this._editor.getModel()!` -> [(this, False, ''), (_editor, False, ''), (getModel, True, '')].

    The third item is a call's first argument; an index (`a[0]`) or any other syntax gives None.
    """
    out: list[tuple[str, bool, str]] = []
    i, n = 0, len(expr)
    while i < n:
        m = _SEGMENT.match(expr, i)
        if not m:
            return None
        name, i, called, first_arg = m.group(), m.end(), False, ""
        while i < n and expr[i] in "!?<(":
            if expr[i] in "<(":
                close = {"<": ">", "(": ")"}[expr[i]]
                open_, depth, start = expr[i], 0, i
                while i < n:
                    depth += (expr[i] == open_) - (expr[i] == close)
                    i += 1
                    if depth == 0:
                        break
                if open_ == "(":
                    called, first_arg = True, expr[start + 1:i - 1].split(",", 1)[0].strip()
            else:
                i += 1
        out.append((name, called, first_arg))
        if i < n:
            if expr[i] != ".":
                return None
            i += 1
    return out


class _Builder:
    def __init__(self, index: SymbolIndex, org_id: str, record_group_id: str,
                 log: logging.Logger, repo_paths: set[str] | None = None) -> None:
        self.index = index
        self.org_id = org_id
        self.record_group_id = record_group_id
        self.log = log
        self.result = EdgeBuildResult()
        self.build_id = uuid.uuid4().hex
        # (from, to, relation) -> (confidence, resolution, line). The edge
        # documents are built per write chunk: a full dict per edge held at once
        # was most of a large build's heap (SC-1).
        self.edges: dict[tuple[str, str, str], tuple[str, str | None, int | None]] = {}
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
        self._expr_memo: dict[tuple, str | None] = {}
        self._bases_memo: dict[str, list[str]] = {}
        # (record, name) -> blocks: the same-file and imported-module rungs look
        # up one file's definitions instead of filtering every same-named block.
        self._by_record_name: dict[tuple[str, str], list[str]] = {}
        for block_id, row in index.rows.items():
            if row.name:
                self._by_record_name.setdefault((row.record_id, row.name), []).append(block_id)

    # -- emit -----------------------------------------------------------

    def _add_edge(self, from_key: str, to_key: str, relation: str, *,
                  confidence: str, line: int | None, resolution: str | None = None) -> None:
        if from_key == to_key:  # self-loop
            return
        dedup = (from_key, to_key, relation)
        if dedup in self.edges:
            return
        self.edges[dedup] = (confidence, resolution, line)
        self.result.edges_by_type[relation] = self.result.edges_by_type.get(relation, 0) + 1
        if relation == RecordRelations.CALLS.value:
            owner = self._closure_owner(from_key.partition("/")[2])
            # A sibling closure is the owner's own local, not something it calls.
            if owner and not self._nested_in(to_key.partition("/")[2], owner):
                self._add_edge(self._block_key(owner), to_key, relation, confidence=confidence,
                               line=line, resolution=resolution)

    def _nested_in(self, block_id: str, ancestor: str) -> bool:
        row = self.index.rows.get(block_id)
        for _ in range(16):  # parent chains are shallow; the bound only guards a cycle
            if row is None or not row.parent_block_id:
                return False
            if row.parent_block_id == ancestor:
                return True
            row = self.index.rows.get(row.parent_block_id)
        return False

    def _closure_owner(self, block_id: str) -> str | None:
        """`const map = (r) => this.toRuntime(r)` inside a method: that method.

        The method runs the call whenever it runs the closure, so a caller walking
        callers of `toRuntime` should reach the method in one hop, not through `map`.
        """
        row = self.index.rows.get(block_id)
        if row is None or row.kind != "function" or self.index.family_by_block.get(block_id) != "js":
            return None
        parent = self.index.rows.get(row.parent_block_id) if row.parent_block_id else None
        return row.parent_block_id if parent is not None and parent.kind in ("function", "method") else None

    def edge_document(self, key: tuple[str, str, str],
                      meta: tuple[str, str | None, int | None], now: int) -> dict:
        from_key, to_key, relation = key
        confidence, resolution, line = meta
        return {
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
            "buildId": self.build_id,
        }

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
        self.imports.learn_go_modules(
            fact.get("toName") or "" for block_id, fact in work
            if fact.get("relation") == RecordRelations.IMPORTS_FROM.value
            and self.index.family_by_block.get(block_id) == "go"
        )
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
            if family == "go":
                # A Go import names a package, a folder of files, not one file.
                self.module_imports.setdefault(from_file or "", set()).update(
                    self.imports.go_package_files(target_file))
                # shortcut: no IMPORTS_FROM edge, as the package has no one file to
                # point at; add a package node if "who imports this" is needed for Go.
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
        if len(candidates) > 1 and family == "js":
            # TS declaration merging: `interface Event` and `namespace Event` are one name;
            # bind the declaration, and `Event.map()` finds the namespace beside it.
            declared = [cid for cid in candidates if self.index.rows[cid].kind != "module"]
            candidates = declared if len(declared) == 1 else candidates
        # A member, not a nested type: `import a.Outer.Inner` still names Inner.
        owner = (self._parent_of(candidates[0])
                 if candidates and family == "jvm" and candidates[0] not in self.index.type_blocks
                 else None)
        if owner and all(self._parent_of(cid) == owner for cid in candidates):
            # `import static ...Preconditions.checkNotNull` brings in every overload,
            # and the statement names the class, so the edge goes to Preconditions.
            candidates = candidates[:1]
        if len(candidates) == 1:
            bindings[local] = candidates[0]
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(owner or candidates[0]),
                RecordRelations.IMPORTS.value,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                resolution=RESOLUTION_IMPORT,
            )
            return
        if candidates:
            self.result.ambiguous_skipped += 1
            return
        reexported = self._follow_reexport(target_file, name, family)
        if reexported:
            # `from django.urls import reverse`: the package re-exports it from base.py.
            bindings[local] = reexported
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(reexported),
                RecordRelations.IMPORTS.value,
                confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                resolution=RESOLUTION_IMPORT,
            )
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
            if relation == _LEGACY_EXTENDS:
                relation = RecordRelations.INHERITS.value
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
                    for one in target if isinstance(target, list) else [target]:
                        self._add_edge(
                            self._source_key(block_id, fact), self._block_key(one),
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

            if relation in _CALL_RELATIONS and family in _IMPLICIT_THIS_FAMILIES:
                # `getPet(id)` inside a Java method is `this.getPet(id)`: the class's
                # own method or an inherited one, before any guess by name.
                owner = self._enclosing_type(block_id)
                target = self._resolve_self_method(owner, name) if owner else None
                # `new Builder()` inside Builder names the class, not its constructor.
                if target and not self._is_constructor(target):
                    self._add_edge(
                        self._source_key(block_id, fact), self._block_key(target), relation,
                        confidence=CONFIDENCE_EXTRACTED, line=fact.get("line"),
                        resolution=RESOLUTION_TYPE,
                    )
                    continue
            target, confidence, resolution = self._resolve_direct(
                name, call_file, family, relation, fact.get("qualifiedPrefix"),
            )
            if not target:
                continue
            self._add_edge(
                self._source_key(block_id, fact), self._block_key(target),
                self._heritage_relation(relation, block_id, target),
                confidence=confidence, line=fact.get("line"), resolution=resolution,
            )

    def _heritage_relation(self, relation: str, source: str, target: str) -> str:
        """`INHERITS` becomes `IMPLEMENTS` when the base turns out to be a contract.

        Only decidable here. The parser sees `class Foo(ABC)` and can emit the
        base's NAME; whether that name is an interface is known once it
        resolves to a block. A base that resolves to nothing stays `INHERITS` --
        a third-party class we cannot inspect is not evidence of a contract,
        and neither is an interface extending another one.
        """
        if relation != RecordRelations.INHERITS.value or self._declared_interface(source):
            return relation
        return (RecordRelations.IMPLEMENTS.value if self._is_interface(target)
                else relation)

    def _declared_interface(self, block_id: str) -> bool:
        row = self.index.rows.get(block_id)
        return bool(row) and (row.kind or "").lower() in _INTERFACE_KINDS

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

        if not self.index.candidates(name):
            # Nothing in the repo defines this name at all: a third-party or
            # stdlib reference, not a resolution failure.
            self.result.external_refs += 1
            return None, CONFIDENCE_INFERRED, None

        if qualified_prefix:
            # `pkg.mod.Parent` and `Outer.Inner` are evidence the bare name is
            # not. A qualifier that does not leave exactly one candidate is
            # ignored, so an unresolved module path falls through below.
            pinned = self._prefix_candidates(
                self._eligible(self.index.candidates(name), family, relation),
                qualified_prefix, call_file, family,
            )
            if pinned:
                return pinned[0], CONFIDENCE_EXTRACTED, RESOLUTION_QUALIFIER

        same_file = self._eligible(self._defined_in(call_file, name), family, relation)
        if family == "js" and relation in _CALL_RELATIONS:
            # A bare JS/TS call never reaches a method: that takes `this.` or a receiver.
            same_file = [cid for cid in same_file if self.index.rows[cid].kind != "method"]
        if same_file:
            candidates, confidence, resolution = same_file, CONFIDENCE_EXTRACTED, RESOLUTION_SAME_FILE
        else:
            # An unqualified Go name is never an imported package's: those take `pkg.`.
            imported = [] if family == "go" else self._eligible(
                [cid for module in sorted(self.module_imports.get(call_file, ()))
                 for cid in self._defined_in(module, name)],
                family, relation,
            )
            if imported:
                candidates, confidence, resolution = imported, CONFIDENCE_EXTRACTED, RESOLUTION_MODULE
            elif family == "js" and relation in _CALL_RELATIONS:
                # ES modules have no implicit cross-module scope: without an
                # import the call cannot be reaching this definition.
                self.result.unresolved += 1
                return None, CONFIDENCE_INFERRED, None
            else:
                every = self.index.candidates(name)
                if len(every) > _MAX_HEURISTIC_CANDIDATES:
                    self.result.ambiguous_skipped += 1
                    return None, CONFIDENCE_INFERRED, None
                candidates = self._eligible(every, family, relation)
                if not candidates:
                    self.result.unresolved += 1
                    return None, CONFIDENCE_INFERRED, None
                confidence, resolution = CONFIDENCE_INFERRED, RESOLUTION_NAME

        candidates = prefer_non_test(candidates, self.index.file_by_block, call_file)
        candidates = path_proximity_winner(candidates, self.index.file_by_block, call_file)
        if len(candidates) != 1:
            self.result.ambiguous_skipped += 1
            return None, confidence, None
        return candidates[0], confidence, resolution

    def _defined_in(self, file_path: str, name: str) -> list[str]:
        record_id = self.index.record_by_file.get(file_path)
        return self._by_record_name.get((record_id, name), []) if record_id else []

    def _eligible(self, candidates: list[str], family: str | None, relation: str) -> list[str]:
        """Same language family; a type for a type relation, a callable for a call."""
        if family:
            candidates = [cid for cid in candidates if self._family_of(cid) == family]
        if relation in _TYPE_RELATIONS:
            typed = [cid for cid in candidates if cid in self.index.type_blocks]
            return typed or candidates
        if relation in _CALL_RELATIONS:
            if family == "go":
                # A bare Go call names a function: a method is only ever `x.m()`.
                candidates = [cid for cid in candidates
                              if (row := self.index.rows.get(cid)) is None or row.kind != "method"]
            # Class exclusion: `select(Model)` is not a call to Model.
            callable_only = [cid for cid in candidates
                             if cid in self.index.callable_blocks and not self._is_constructor(cid)]
            if callable_only:
                return callable_only
            # `Foo()` / `new Foo()` calls the type, which is where a constructor
            # call points in every language; a field or variable never is (P-3).
            return [cid for cid in candidates if cid in self.index.type_blocks]
        return candidates

    def _is_constructor(self, block_id: str) -> bool:
        """A method named for its type (Java, C#, C++, Kotlin): the call names the type."""
        row = self.index.rows.get(block_id)
        if row is None or row.kind not in ("method", "constructor") or not row.qualified_name:
            return False
        # `method:img.Resizer.Resizer`: its owner is the segment before its name,
        # whether the method sits in the class body or is defined out of line.
        path = row.qualified_name.partition(":")[2].split(".")
        return len(path) >= 2 and path[-2] == row.name

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
        # Only a module this file imports: an untyped variable (`def run(config):
        # config.load()`) would otherwise bind to any repo file of that name.
        imported = self.module_imports.get(from_file, ())
        bound = receiver.split(".", 1)[0] in self.module_bindings.get(from_file, {})
        # The full dotted prefix first (`pkg.mod.fn()`), then the bare receiver.
        for specifier in filter(None, (fact.get("qualifiedPrefix"), receiver)):
            target_file = self.imports.resolve(specifier, from_file, family)
            if not target_file or not (bound or target_file in imported):
                continue
            target_file = self.imports.walk_barrel_chain(target_file, self.re_exports)
            hit = self._defined_or_reexported(target_file, name)
            if hit:
                return hit
        return None

    def _resolve_member_call(self, block_id: str, fact: dict, record_id: str,
                             ) -> tuple[str | list[str] | None, str, str | None]:
        """`obj.method()` -- find the receiver's type, then its method.

        Returns ``(target, confidence, resolution)``, the target a list when the
        receiver's type is a union alias (one per member); a resolution of
        ``_EXTERNAL`` with no target means the receiver is a third-party or
        stdlib module (`logging.getLogger`).
        """
        name = fact.get("toName") or ""
        receiver_type = fact.get("receiverType")
        receiver = fact.get("receiver")
        scoped = bool(fact.get("headType")) and fact.get("qualifiedPrefix") == receiver
        # `a.f().g()` keeps `f()` as the receiver, which would match a bare `function f()`'s
        # return type; the chain is evaluated whole below instead.
        chained_call = receiver.endswith(")") and fact.get("qualifiedPrefix") != receiver if receiver else False
        if not receiver_type and receiver and not scoped and not chained_call:
            receiver_type = self.index.type_table_by_record.get(record_id, {}).get(receiver)
        if receiver_type and (receiver_type.startswith("=") or receiver_type.endswith("[]")):
            receiver_type = None
        if not receiver_type and receiver in ("self", "this"):
            owner = self._enclosing_type(block_id)
            if owner:
                return self._resolve_self_method(owner, name), CONFIDENCE_INFERRED, RESOLUTION_TYPE
        if not receiver_type and receiver in ("super()", "super"):
            # `super().__init__()`, `super.save()`: the nearest base that defines it.
            owner = self._enclosing_type(block_id)
            for base in self._base_types_of(owner) if owner else ():
                target = self._resolve_self_method(base, name)
                if target:
                    return target, CONFIDENCE_INFERRED, RESOLUTION_TYPE
            return None, CONFIDENCE_INFERRED, None
        if not receiver_type and receiver:
            bound = self._resolve_bound_receiver(receiver, name, record_id)
            if bound is not None:
                return bound
        if not receiver_type and fact.get("qualifiedPrefix") and self._family_of(block_id) == "js":
            # `this._editor.getModel().getValue()`, `c.run()` with `const c = Ctl.get(e)`.
            owner = self._expr_type(fact["qualifiedPrefix"], record_id, self._enclosing_type(block_id),
                                    head_type=fact.get("headType"), head_type_arg=fact.get("headTypeArg"))
            if owner:
                target = self._methods_on(owner, name)
                if target:
                    return target, CONFIDENCE_INFERRED, RESOLUTION_TYPE
        if not receiver_type:
            target = self._resolve_module_qualified_call(fact, record_id)
            return target, CONFIDENCE_INFERRED, RESOLUTION_MODULE if target else None

        family = self._family_of(block_id)
        if family == "go":
            return self._resolve_go_method(receiver_type, name, record_id)
        if "|" in receiver_type:  # `e: A | B`
            found: dict[str, None] = {}
            for member in receiver_type.split("|"):
                candidates = self._type_candidates(member, record_id, family)
                target = self._methods_on(candidates[0], name) if len(candidates) == 1 else None
                found.update(dict.fromkeys(target if isinstance(target, list) else [target] if target else []))
            return list(found) or None, CONFIDENCE_INFERRED, RESOLUTION_TYPE
        type_candidates = self._type_candidates(receiver_type, record_id, family)
        if len(type_candidates) != 1:  # god-node guard
            if type_candidates:
                self.result.ambiguous_skipped += 1
            return None, CONFIDENCE_INFERRED, None
        return self._methods_on(type_candidates[0], name) or None, CONFIDENCE_INFERRED, RESOLUTION_TYPE

    def _type_candidates(self, type_name: str, record_id: str, family: str | None) -> list[str]:
        """The types *type_name* can mean in a file: the one it imports or defines, if any."""
        type_candidates = [
            cid for cid in self.index.candidates(type_name)
            if cid in self.index.type_blocks
            and (family is None or self._family_of(cid) == family)
        ]
        if len(type_candidates) > 1:
            call_file = self.index.file_by_record.get(record_id) or ""
            bound = self.symbol_bindings.get(call_file, {}).get(type_name)
            local = [cid for cid in type_candidates
                     if cid in self.index.rows and self.index.rows[cid].file_path == call_file]
            if bound in type_candidates:
                type_candidates = [bound]
            elif len(local) == 1:
                type_candidates = local
        return type_candidates

    def _expr_type(self, expr: str, record_id: str, this_type: str | None, depth: int = 0, *,
                   head_type: str | None = None, array: bool = False,
                   head_type_arg: str | None = None) -> str | None:
        """The type block a JS/TS receiver expression evaluates to, or None.

        Walks `a.b().c` left to right through the parser's type tables: a
        variable's declared type or initializer, a field's type on its owner (or a
        base), a method's or function's declared return type. Each table is read
        in its own file, so a type name resolves against that file's imports.
        *head_type* is the first name's type in the calling function, when the
        parser saw one. An array of T is `[]T`, returned only when *array*; an
        `xs[*]` expression is one element of `xs`.
        """
        element = expr.endswith("[*]")
        if element:
            expr = expr[:-3]
        key = (record_id, this_type, expr, head_type, head_type_arg, element)
        if key not in self._expr_memo:
            self._expr_memo[key] = None  # a cycle (`a = a.next`) resolves to nothing
            self._expr_memo[key] = self._eval_expr(expr, record_id, this_type, depth, head_type, head_type_arg)
        owner = self._expr_memo[key]
        if element:
            return owner[2:] if owner and owner.startswith("[]") else None
        return owner if array or not owner or not owner.startswith("[]") else None

    def _eval_expr(self, expr: str, record_id: str, this_type: str | None, depth: int,
                   head_type: str | None, head_type_arg: str | None = None) -> str | None:
        segments = _receiver_segments(expr) if depth < _EXPR_DEPTH else None
        owner = None
        if segments:
            (head, called, first_arg), rest = segments[0], segments[1:]
            if head == "this" and not called:
                owner = this_type
            elif called and head in _CLASS_ARG_CALLS and _SEGMENT.fullmatch(first_arg) and first_arg[:1].isupper():
                owner = self._value_type(first_arg, record_id, this_type, depth)
            elif called:
                owner = self._return_type(self._resolve_function(head, record_id), depth)
            elif head_type:
                owner = self._declared_type(head_type, record_id, this_type, depth)
            else:
                owner = self._value_type(head, record_id, this_type, depth)
            # `IObservable<Foo>`'s argument, for a `read(): T` called on it next. A scoped
            # head keeps its own scope's argument: another function's `v` may differ.
            declared_arg = None if called or head == "this" else head_type_arg if head_type else \
                self.index.type_table_by_record.get(record_id, {}).get(f"{head}<>")
            type_arg = self._declared_type(declared_arg, record_id, this_type, depth) if declared_arg else None
            for name, called, first_arg in rest:
                if called and name in _CLASS_ARG_CALLS and _SEGMENT.fullmatch(first_arg) and first_arg[:1].isupper():
                    # `accessor.get(IFoo)`, `createInstance(Foo)`: the service or class named,
                    # whatever the receiver's type.
                    owner, type_arg = self._value_type(first_arg, record_id, this_type, depth), None
                    continue
                if owner is None or owner.startswith("[]"):
                    owner = None
                    break
                if called:
                    method = self._resolve_self_method(owner, name)
                    owner = type_arg if self._returns_type_param(method) else self._return_type(method, depth)
                    type_arg = None
                else:
                    owner, type_arg = (self._member_type(owner, name, depth),
                                       self._member_type(owner, name, depth, suffix="<>"))
        return owner

    def _value_type(self, name: str, record_id: str, this_type: str | None, depth: int) -> str | None:
        """A bare name: a typed variable, else a class it names (`Ctl.get()` is static)."""
        declared = self.index.type_table_by_record.get(record_id, {}).get(name)
        if declared:
            return self._declared_type(declared, record_id, this_type, depth)
        family = lang_family(None, self.index.file_by_record.get(record_id))
        candidates = self._type_candidates(name, record_id, family)
        return candidates[0] if len(candidates) == 1 else None

    def _declared_type(self, declared: str, record_id: str, this_type: str | None, depth: int) -> str | None:
        if declared.startswith("="):
            return self._expr_type(declared[1:], record_id, this_type, depth + 1, array=True)
        is_array = declared.endswith("[]")
        family = lang_family(None, self.index.file_by_record.get(record_id))
        candidates = self._type_candidates(declared[:-2] if is_array else declared, record_id, family)
        if len(candidates) != 1:
            return None
        return "[]" + candidates[0] if is_array else candidates[0]

    def _member_type(self, owner: str, field_name: str, depth: int, suffix: str = "") -> str | None:
        """`owner.field`: its declared type on the owner or the nearest base declaring it.

        *suffix* `<>` asks for the type argument instead (`Foo` of `IObservable<Foo>`).
        """
        seen, queue = set(), [owner]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            row = self.index.rows.get(current)
            if row is not None and row.name:
                declared = self.index.type_table_by_record.get(row.record_id, {}).get(f"{row.name}.{field_name}{suffix}")
                if declared:
                    return self._declared_type(declared, row.record_id, current, depth)
            queue.extend(self._base_types_of(current))
        return None

    def _declared_return(self, target: str | None) -> tuple[str | None, str | None]:
        """(declared return type, owner) of a function or method block."""
        row = self.index.rows.get(target) if target else None
        if row is None or not row.name:
            return None, None
        owner = self._enclosing_type(target)
        owner_row = self.index.rows.get(owner) if owner else None
        key = f"{owner_row.name}.{row.name}()" if owner_row is not None else f"{row.name}()"
        return self.index.type_table_by_record.get(row.record_id, {}).get(key), owner

    def _return_type(self, target: str | None, depth: int) -> str | None:
        declared, owner = self._declared_return(target)
        if not declared or declared.startswith("<"):
            return None
        return self._declared_type(declared, self.index.rows[target].record_id, owner, depth)

    def _returns_type_param(self, target: str | None) -> bool:
        """`read(): T` on `IObservable<T>`: what it returns is the receiver's type argument."""
        declared, _ = self._declared_return(target)
        return bool(declared and declared.startswith("<"))

    def _resolve_function(self, name: str, record_id: str) -> str | None:
        """A bare call's function, by import or same-file definition only: no counters, no guessing."""
        call_file = self.index.file_by_record.get(record_id) or ""
        bound = self.symbol_bindings.get(call_file, {}).get(name)
        if bound in self.index.callable_blocks:
            return bound
        local = [cid for cid in self._defined_in(call_file, name) if cid in self.index.callable_blocks]
        return local[0] if len(local) == 1 else None

    def _namespace_member(self, bound: str, receiver: str, name: str, record_id: str) -> str | None:
        """`Event.map()`: a function in a TS `namespace Event`, in the file the import
        binds (where it often shares its name with an interface) or in this file."""
        bound_row = self.index.rows.get(bound)
        ns_record, ns_name = (bound_row.record_id, bound_row.name) if bound_row is not None else (record_id, receiver)
        for ns in self._by_record_name.get((ns_record, ns_name), ()):
            if self.index.rows[ns].kind != "module":
                continue
            for cid in self._by_record_name.get((ns_record, name), ()):
                if self.index.rows[cid].parent_block_id == ns:
                    return cid
        return None

    def _resolve_go_method(self, receiver_type: str, name: str, record_id: str,
                           ) -> tuple[str | None, str, str | None]:
        """`c.Get()` with `c *Client`: Go names a type by package, so `Client` is
        this package's and `rest.Client` the imported rest package's."""
        call_file = self.index.file_by_record.get(record_id) or ""
        qualifier, _, type_name = receiver_type.rpartition(".")
        if qualifier:
            package = self.module_bindings.get(call_file, {}).get(qualifier)
            if not package or package == _EXTERNAL:
                return None, CONFIDENCE_INFERRED, _EXTERNAL if package else None
        else:
            package = call_file
        owner = self._defined_or_reexported(package, type_name)
        if owner not in self.index.type_blocks:
            return None, CONFIDENCE_INFERRED, None
        # INFERRED as for every language: the type table is per file, not per scope.
        return self._resolve_self_method(owner, name), CONFIDENCE_INFERRED, RESOLUTION_TYPE

    def _resolve_bound_receiver(self, receiver: str, name: str, record_id: str,
                                ) -> tuple[str | None, str, str | None] | None:
        """`jobs.run()`, `ns.f()`, `Order.from_dict()`: a receiver an import or a
        same-file class names. None when the receiver is none of those."""
        call_file = self.index.file_by_record.get(record_id) or ""
        module = self.module_bindings.get(call_file, {}).get(receiver)
        if module == _EXTERNAL:
            return None, CONFIDENCE_INFERRED, _EXTERNAL
        if module:
            target = self._defined_or_reexported(module, name)
            return target, CONFIDENCE_EXTRACTED, RESOLUTION_IMPORT if target else None
        bound = self.symbol_bindings.get(call_file, {}).get(receiver)
        if bound == _EXTERNAL:
            return None, CONFIDENCE_INFERRED, _EXTERNAL
        member = self._namespace_member(bound or "", receiver, name, record_id)
        if member:
            return member, CONFIDENCE_EXTRACTED, RESOLUTION_IMPORT if bound else RESOLUTION_SAME_FILE
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

    def _defined_or_reexported(self, file_path: str, name: str) -> str | None:
        if file_path.endswith(".go"):
            # A Go package's names are shared by every file in its folder.
            files = self.imports.go_package_files(file_path)
            if file_path not in files:  # a _test.go file sees its own names too
                files = [file_path, *files]
            hits = [hit for f in files if (hit := self._top_level_in_file(f, name))]
            return hits[0] if len(hits) == 1 else None
        return self._top_level_in_file(file_path, name) or self._follow_reexport(
            file_path, name, lang_family(None, file_path))

    def _follow_reexport(self, file_path: str, name: str, family: str | None,
                         depth: int = 0) -> str | None:
        """The definition a module makes available under ``name`` by importing it.

        `models.ForeignKey` with `from django.db import models`: models/__init__.py
        defines nothing by that name; it imports it from fields/related.py. Read
        from the file's own IMPORTS facts, which a package `__init__.py` keeps on
        its nodes. Bounded, like barrel chains.
        """
        if family == "js":
            return self._follow_barrel(file_path, name, {file_path}, depth)
        # Only a package `__init__.py` keeps its imports on its nodes; following any
        # other module's imports would see them on a full build and not on an
        # incremental one, and the two must agree.
        if depth >= _MAX_REEXPORT_HOPS or not file_path.endswith("__init__.py"):
            return None
        record = self.index.record_by_file.get(file_path)
        for block_id in self.index.blocks_by_record.get(record, ()):
            row = self.index.rows.get(block_id)
            for fact in row.pending_edges if row else ():
                if fact.get("relation") != RecordRelations.IMPORTS.value:
                    continue
                if (fact.get("localName") or fact.get("toName")) != name:
                    continue
                source = self.imports.resolve(fact.get("qualifiedPrefix") or "", file_path, family)
                if not source or source == file_path:
                    continue
                original = fact.get("toName") or name
                hit = self._top_level_in_file(source, original) or self._follow_reexport(
                    source, original, family, depth + 1)
                if hit:
                    return hit
        return None

    def _follow_barrel(self, file_path: str, name: str, seen: set[str], depth: int = 0) -> str | None:
        """`import { f } from "@pkg/common"` with an index.ts of `export * from "./a"`, `"./b"`, …:
        the one module behind the barrel that defines ``f``.

        `walk_barrel_chain` stops at a barrel with several re-exports, where the name's
        module is decided by the name itself. Two definitions behind it is ambiguous and
        binds nothing. Read from `re_exports`, which every row feeds, so an incremental
        build follows the same chains as a full one.
        """
        if depth >= _MAX_REEXPORT_HOPS:
            return None
        hits = set()
        for onward in sorted(self.re_exports.get(file_path, ())):
            if onward in seen:
                continue
            hit = self._top_level_in_file(onward, name) or self._follow_barrel(
                onward, name, seen | {onward}, depth + 1)
            if hit:
                hits.add(hit)
        return hits.pop() if len(hits) == 1 else None

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

    def _methods_on(self, owner: str, method_name: str) -> str | list[str] | None:
        """*method_name* on *owner*, or on each member when *owner* is a union alias."""
        direct = self._resolve_self_method(owner, method_name)
        if direct:
            return direct
        found = [m for m in dict.fromkeys(self._resolve_self_method(u, method_name)
                                          for u in self._union_members(owner)) if m]
        return found or None

    def _union_members(self, alias: str, depth: int = 0) -> list[str]:
        """The type blocks a `type X = A | B` alias stands for, nested aliases expanded."""
        row = self.index.rows.get(alias)
        if row is None or row.kind != "type_alias" or not row.name or depth > 3:
            return []
        value = self.index.type_table_by_record.get(row.record_id, {}).get(f"{row.name}|") or ""
        family = self._family_of(alias)
        members: list[str] = []
        for name in filter(None, value.split("|")):
            found = self._type_candidates(name, row.record_id, family)
            if len(found) == 1:
                members += self._union_members(found[0], depth + 1) or found
        return members

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
        direct = self.index.method_index.get((owner, method_name)) or self._method_by_owner_name(
            owner, method_name)
        if direct:
            return direct

        seen = {owner}
        queue = list(self._base_types_of(owner))
        while queue:
            base = queue.pop(0)
            if base in seen:
                continue
            seen.add(base)
            inherited = self.index.method_index.get((base, method_name)) or self._method_by_owner_name(
                base, method_name)
            if inherited:
                return inherited
            queue.extend(self._base_types_of(base))
        return None

    def _method_by_owner_name(self, owner: str, method_name: str) -> str | None:
        """A method declared outside its type's body (Go `func (c *Context) Render`):
        named `method:Context.Render`, in the type's own package (its folder)."""
        row = self.index.rows.get(owner)
        if row is None or not row.name or not row.file_path:
            return None
        wanted = f"{row.name}.{method_name}"
        folder = row.file_path.rsplit("/", 1)[0] if "/" in row.file_path else ""
        hits = [cid for cid in self.index.candidates(method_name)
                if (self.index.rows[cid].qualified_name or "").partition(":")[2] == wanted
                and (self.index.file_by_block.get(cid, "").rsplit("/", 1)[0]
                     if "/" in self.index.file_by_block.get(cid, "") else "") == folder]
        return hits[0] if len(hits) == 1 else None

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
        module = self.module_bindings.get(from_file, {}).get(qualified_prefix or "")
        if module and module != _EXTERNAL:
            hit = self._defined_or_reexported(module, name)
            if hit in self.index.type_blocks:
                return hit
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

    def prune_dangling(self) -> list[tuple[tuple[str, str, str], tuple[str, str | None, int | None]]]:
        """Drop any edge whose endpoint is not a live node; ``(key, meta)`` pairs
        for ``edge_document``.

        The last safety net: a resolver bug can only make the graph smaller,
        never wrong.
        """
        live_blocks = set(self.index.rows)
        live_records = set(self.index.blocks_by_record)
        return [
            (key, meta) for key, meta in self.edges.items()
            if self._endpoint_live(key[0], live_blocks, live_records)
            and self._endpoint_live(key[1], live_blocks, live_records)
        ]

    @staticmethod
    def _endpoint_live(key: str, live_blocks: set[str], live_records: set[str]) -> bool:
        collection, _, ident = key.partition("/")
        if collection == CollectionNames.BLOCKS.value:
            return ident in live_blocks
        if collection == CollectionNames.RECORDS.value:
            return ident in live_records
        return False


def _chunks(items: list, size: int = _WRITE_CHUNK) -> Iterator[list]:
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
    fence: Callable[[], Awaitable[None]] | None = None,
) -> EdgeBuildResult:
    """Resolve and write the repo's cross-file edges.

    ``touched_record_ids`` selects an incremental run: the symbol table is still
    built from *every* block, but only the touched files and their dependents are
    re-resolved.

    ``facts_source`` supplies the full facts of the files being re-resolved;
    their nodes carry only the resident relations. Without one, node facts are
    used as they are, which is complete only for blocks projected before the
    residue change.

    ``fence`` is awaited before every write and raises if this build may no
    longer write -- its lease was lost and another build may own the repo.
    """
    started = time.monotonic()
    log = log or logger
    fence = fence or _no_fence

    block_count = await graph_provider.count_nodes_by_filters(
        collection=CollectionNames.BLOCKS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
    )
    if block_count > MAX_BLOCKS_PER_BUILD:
        raise EdgeBuildTooLargeError(
            f"{block_count} blocks is over the {MAX_BLOCKS_PER_BUILD} one build may load"
        )
    files = await _scan_repo_files(graph_provider, org_id, record_group_id)
    repo_paths = set(files.paths.values())
    # The CPU-bound phases run in a worker thread: this runs on the connectors
    # service's event loop, which a large repo held for minutes -- no HTTP, no
    # other syncs, no lease renewal, and Kafka sessions timing out.
    index = await _index_blocks(graph_provider, org_id, record_group_id, files)
    builder = await asyncio.to_thread(_Builder, index, org_id, record_group_id, log, repo_paths)
    builder.result.blocks = len(index.rows)
    builder.result.files_total = len(index.blocks_by_record)

    changed_names = await _names_changed_by(graph_provider, index, org_id, touched_record_ids)
    reresolve = await asyncio.to_thread(
        _select_reresolve_set, index, touched_record_ids, changed_names,
    )
    if touched_record_ids:
        reresolve |= await _records_pointing_into(
            graph_provider, index, org_id, record_group_id, touched_record_ids
        )
    builder.result.files_touched = len(touched_record_ids or ())
    builder.result.files_reresolved = len(reresolve)

    if facts_source is not None:
        # Only a file with blocks has stored facts. A sync touches docs, tests left
        # unindexed by policy and emptied files too: counted as unreadable, a fresh
        # repo's first build aborted on every retry (B3). They stay re-resolved, so
        # an emptied file still loses its old edges.
        with_blocks = {r for r in reresolve if r in index.blocks_by_record}
        unknown = await _load_facts(facts_source, index, org_id, with_blocks, builder.result, log)
        if unknown and len(unknown) >= _MIN_MISSING_FACTS_TO_ABORT \
                and len(unknown) > _MAX_MISSING_FACTS_SHARE * len(with_blocks):
            raise EdgeBuildAbortedError(
                f"stored facts unreadable for {len(unknown)} of {len(with_blocks)} files; "
                "keeping the existing edges"
            )
        # A file whose facts are unknown keeps the edges it has: resolving it
        # from its node residue alone used to delete every call and import
        # edge it owned and report the build a success.
        reresolve -= unknown
        builder.result.files_reresolved = len(reresolve)

    edges = await asyncio.to_thread(_resolve, builder, index, reresolve)

    if not dry_run:
        # Write, then delete what this build did not write: deleting first
        # left readers an empty graph for the length of the writes, and a
        # failed write left it empty until the next build (R-8).
        now = int(time.time() * 1000)
        for chunk in _chunks(edges):
            await fence()
            await graph_provider.batch_upsert_node_relations(
                [builder.edge_document(key, meta, now) for key, meta in chunk])
        source_keys = _source_keys_for(index, reresolve)
        await fence()
        removed = await _delete_previous_edges(
            graph_provider, org_id, record_group_id, source_keys, builder.build_id
        )
        builder.result.edges_removed = removed
    builder.result.edges_written = len(edges)
    builder.result.duration_s = time.monotonic() - started
    return builder.result


async def _no_fence() -> None:
    return None


def _resolve(builder: _Builder, index: SymbolIndex, reresolve: set[str]) -> list[tuple]:
    work = [
        (block_id, fact)
        for block_id, row in index.rows.items() if row.record_id in reresolve
        for fact in row.pending_edges
    ]
    builder.result.pending_edges_seen = len(work)
    builder.resolve_imports(work)
    builder.resolve_rest(work)
    builder.link_overrides()
    return builder.prune_dangling()


async def _load_facts(
    source: CodeFactsSource,
    index: SymbolIndex,
    org_id: str,
    record_ids: set[str],
    result: EdgeBuildResult,
    log: logging.Logger,
) -> set[str]:
    """Swap the node residue for the blob's full facts on every re-resolved file.

    Runs after the re-resolve set is chosen, which only needs what the nodes
    carry. Returns the files whose facts could not be read or predate code
    metadata; the caller leaves their edges alone.
    """
    loaded = await source.load(org_id, record_ids)
    unknown: set[str] = set()
    for record_id in record_ids:
        facts = loaded.get(record_id)
        if facts is None or not facts.recognised:
            result.facts_missing += 1
            unknown.add(record_id)
            log.warning(
                "No usable stored facts for record %s; keeping its existing edges",
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
    return unknown


async def _names_changed_by(graph_provider: IGraphDBProvider, index: SymbolIndex,
                            org_id: str, touched: set[str] | None) -> set[str]:
    """Names whose meaning the touched files may have changed.

    The definitions they added, removed, moved or re-kinded
    (``codeFiles.changedNames``; a removed one is no longer in their blocks),
    and the ``module:`` references that mean them, which is how an importer
    refers to a module that was just added or edited. Not every name they
    define: one edit to a file defining ``get`` re-resolved every file calling
    any ``get`` (R-7); callers already bound into the file are found by their
    edges instead (``_records_pointing_into``).
    Read for the touched records themselves rather than off the block scan: a
    file that lost every definition has no blocks left to be found by.
    """
    if not touched:
        return set()
    names: set[str] = set()
    for chunk in _chunks(sorted(touched), 2000):
        info = await graph_provider.get_file_paths_for_records(
            org_id=org_id, record_ids=chunk, raise_on_error=True
        )
        for record_id, file_info in info.items():
            names.update(file_info.changed_names)
            names.update(module_refs_of_path(file_info.file_path))
            if not file_info.has_code_file:
                # Nowhere to record its changes, so every name it defines.
                names.update(index.names_by_record.get(record_id, ()))
    return names


def _select_reresolve_set(index: SymbolIndex, touched: set[str] | None,
                          changed_names: set[str] = frozenset()) -> set[str]:
    """Touched files plus the unchanged files whose edges they invalidate.

    A change in B can break an edge belonging to A: B deleting a symbol leaves
    A's edge dangling or binds it elsewhere, B adding one means A's unresolved
    reference should now resolve. Neither shows up in A's own timestamp, and a
    deleted name is no longer in B's blocks -- ``changed_names`` carries it.
    """
    if not touched:
        return set(index.blocks_by_record)

    selected = set(touched)

    # B added or removed a name -> re-resolve anything that references that name.
    if changed_names:
        for record_id, names in index.referenced_names_by_record.items():
            if record_id not in selected and names & changed_names:
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
            edge_collection=CollectionNames.RECORD_LINKS.value,
            filters={
                "source": CODE_GRAPH_SOURCE,
                "orgId": org_id,
                "recordGroupId": record_group_id,
            },
            raise_on_error=True,
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
                                 record_group_id: str, source_keys: list[str],
                                 build_id: str) -> int:
    """Remove the code_graph edges sourced by the re-resolved files that this
    build (``build_id``) did not write.

    Scoped by `_from`: an unscoped delete would erase edges belonging to files
    this run is not going to rebuild.
    """
    if not source_keys:
        return 0

    removed = 0
    for chunk in _chunks(source_keys, 2000):
        removed += await graph_provider.delete_edges_by_source_keys(
            source_keys=chunk,
            edge_collection=CollectionNames.RECORD_LINKS.value,
            filters={
                "source": CODE_GRAPH_SOURCE,
                "orgId": org_id,
                "recordGroupId": record_group_id,
            },
            exclude={"buildId": build_id},
        )
    return removed
