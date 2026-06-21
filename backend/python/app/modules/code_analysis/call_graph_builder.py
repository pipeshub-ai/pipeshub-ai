"""
call_graph_builder.py — post-full-sync CALLS edge builder.

After a repository sync completes and the indexing pipeline has stored
``definitions`` and ``calls`` on each ``codeFiles`` node (via
``call_extractor.extract_symbols``), this module:

1. Fetches all ``CODE_FILE`` records in the repository (one query).
2. Builds a **repo symbol table**: ``symbol_name → [record_id]`` from the
   ``definitions`` stored on each file.
3. For each file's ``calls`` list, resolves each callee to a target record:

   a. **Import-disambiguation** (priority 1): check whether the caller file
      imports anything from files that define the callee.  Uses the
      ``imports`` field (raw import texts) + the language-specific resolver
      from ``import_resolver.py`` to compute candidate paths, then looks up
      those paths in the path→record map.  If exactly one candidate file
      defines the callee, that file is the target.

   b. **Unique-name fallback** (priority 2): if the callee name appears in
      exactly one file's symbol table (no ambiguity), use that file.

   c. **Skip** if no match, multiple matches (ambiguous), or self-loop.

4. Emits ``CALLS`` edges via ``batch_upsert_record_relations`` — idempotent
   because the UPSERT key is
   ``(_from, _to, relationshipType="CALLS", constraintName="{caller}:{callee}")``.
   Each (caller_symbol, callee_symbol) pair between the same file pair is a
   distinct edge, preserving the per-symbol payload for future symbol-level
   graph promotion.

Backend-agnostic: uses only ``IGraphDBProvider`` methods present on the
interface and implemented by both ArangoDB and Neo4j.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.models.entities import CodeFileRecord, RecordType
from app.modules.code_analysis.import_resolver import _RESOLVERS
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)


class CallGraphBuilder:
    """Build file-level ``CALLS`` edges for an entire repository.

    Args:
        graph_provider: Graph DB provider (ArangoDB or Neo4j); only uses
            methods declared on ``IGraphDBProvider``.
        org_id: Organisation ID for multi-tenant scoping.
        record_group_id: Internal ``RecordGroup._key`` of the repository.
        connector_id: Connector ID associated with the repository (required
            by ``get_records_by_record_group``).
    """

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        org_id: str,
        record_group_id: str,
        connector_id: str,
    ) -> None:
        self._gp = graph_provider
        self._org_id = org_id
        self._record_group_id = record_group_id
        self._connector_id = connector_id
        self._logger = logging.getLogger(f"{__name__}.{record_group_id}")

    async def resolve_all(self) -> dict[str, int]:
        """Resolve call sites across the repo and persist CALLS edges.

        Returns:
            Stats dict with:
            ``files_processed``, ``edges_created``, ``edges_skipped``
            (unresolved / ambiguous / external), ``errors``.
        """
        stats = {"files_processed": 0, "edges_created": 0, "edges_skipped": 0, "errors": 0}

        # ── Fetch all records in this record group ────────────────────────────
        try:
            all_records = await self._gp.get_records_by_record_group(
                self._record_group_id,
                self._connector_id,
                self._org_id,
                depth=-1,
            )
        except Exception as exc:
            self._logger.error("Failed to fetch records for group %s: %s", self._record_group_id, exc)
            stats["errors"] += 1
            return stats

        code_files: list[CodeFileRecord] = [
            r for r in all_records if isinstance(r, CodeFileRecord)
        ]

        if not code_files:
            self._logger.info(
                "No CODE_FILE records found in record group %s", self._record_group_id
            )
            return stats

        # ── Build lookup tables ───────────────────────────────────────────────
        # path → record_id (strip leading "/" for normalised matching)
        path_to_record: dict[str, str] = {}
        for rec in code_files:
            if rec.file_path and rec.id:
                path_to_record[rec.file_path.lstrip("/")] = rec.id

        # symbol_name → list[record_id] that define that symbol
        symbol_table: dict[str, list[str]] = defaultdict(list)
        for rec in code_files:
            for sym in rec.definitions or []:
                if sym:
                    symbol_table[sym].append(rec.id)

        self._logger.info(
            "CallGraphBuilder: %d CODE_FILE records, %d unique symbols in group %s",
            len(code_files),
            len(symbol_table),
            self._record_group_id,
        )

        # ── Resolve call sites per file ───────────────────────────────────────
        all_edges: list[dict] = []
        for rec in code_files:
            try:
                edges = self._resolve_file_calls(rec, path_to_record, symbol_table, stats)
                all_edges.extend(edges)
                stats["files_processed"] += 1
            except Exception as exc:
                self._logger.warning("Error resolving calls for %s: %s", rec.id, exc)
                stats["errors"] += 1

        # ── Write all CALLS edges in one batch ────────────────────────────────
        if all_edges:
            try:
                await self._gp.batch_upsert_record_relations(all_edges)
                stats["edges_created"] = len(all_edges)
            except Exception as exc:
                self._logger.error("Failed to upsert CALLS edges: %s", exc)
                stats["errors"] += len(all_edges)
                stats["edges_created"] = 0

        self._logger.info("CallGraphBuilder complete: %s", stats)
        return stats

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_file_calls(
        self,
        rec: CodeFileRecord,
        path_to_record: dict[str, str],
        symbol_table: dict[str, list[str]],
        stats: dict[str, int],
    ) -> list[dict]:
        """Resolve all call sites in *rec* and return edge dicts."""
        source_id = rec.id or ""
        file_path = rec.file_path or ""
        language = rec.language or ""
        calls = rec.calls or []         # [{name, line, caller}, ...]
        imports = rec.imports or []     # raw import texts

        if not source_id or not file_path or not calls:
            return []

        # Build a set of record_ids reachable via this file's imports
        # (for import-disambiguation).
        imported_record_ids: set[str] = set()
        resolver_fn = _RESOLVERS.get(language)
        if resolver_fn and imports:
            for import_text in imports:
                for candidate in resolver_fn(import_text, file_path):
                    rid = path_to_record.get(candidate.lstrip("/"))
                    if rid:
                        imported_record_ids.add(rid)

        edges: list[dict] = []
        # De-dup key = (source_id, target_id, caller, callee) so that two
        # distinct caller symbols calling the same callee produce distinct edges
        # (each gets a unique constraintName = "caller:callee").
        seen: set[tuple[str, str, str, str]] = set()

        for call_site in calls:
            callee = call_site.get("name") or ""
            if not callee:
                continue
            caller = call_site.get("caller") or ""
            line = call_site.get("line") or 0

            target_id = self._resolve_callee(
                callee=callee,
                source_id=source_id,
                symbol_table=symbol_table,
                imported_record_ids=imported_record_ids,
            )

            if target_id is None:
                stats["edges_skipped"] += 1
                continue

            key = (source_id, target_id, caller, callee)
            if key in seen:
                continue
            seen.add(key)

            edges.append({
                "_from": f"{CollectionNames.RECORDS.value}/{source_id}",
                "_to": f"{CollectionNames.RECORDS.value}/{target_id}",
                "relationshipType": RecordRelations.CALLS.value,
                # constraintName = "caller:callee" keeps distinct per-symbol edges
                # from collapsing under the UPSERT key.  Bridges to a future
                # symbol-level (SCIP/CPG-style) ontology: a later phase can
                # promote these payloads into symbol nodes + CALLS edges between them.
                "constraintName": f"{caller}:{callee}",
                "orgId": self._org_id,
                "sourceSymbol": caller,
                "targetSymbol": callee,
                "sourceLineNumber": line,
            })

        return edges

    def _resolve_callee(
        self,
        callee: str,
        source_id: str,
        symbol_table: dict[str, list[str]],
        imported_record_ids: set[str],
    ) -> str | None:
        """Return the target record_id for *callee*, or ``None`` if unresolvable.

        Resolution priority:
        1. Import-disambiguation: callee is defined in exactly one of the
           files that the source file imports.
        2. Unique-name fallback: callee appears in exactly one file globally.
        3. Skip: self-loop, ambiguous (multiple candidates), or external.
        """
        candidates = symbol_table.get(callee)
        if not candidates:
            return None  # external or undefined — skip

        # Remove self-loops
        candidates = [rid for rid in candidates if rid != source_id]
        if not candidates:
            return None  # callee is only in this file (self-definition)

        if len(candidates) == 1:
            # Only one defining file — unique match
            return candidates[0]

        # Multiple candidates — try import-disambiguation
        import_candidates = [rid for rid in candidates if rid in imported_record_ids]
        if len(import_candidates) == 1:
            return import_candidates[0]

        # Ambiguous → skip
        return None
