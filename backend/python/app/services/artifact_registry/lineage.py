"""`LineageTracker` — auto-captures `DERIVED_FROM` edges over the existing
`nodeRelations` edge collection. There is deliberately NO public method
that takes lineage asserted by a caller-supplied arbitrary pair without
version numbers pinned by the harness itself — see module docstring in the
plan: lineage is an observable fact of "this code run produced these
files", captured by `sandbox_bridge.py`'s POST_TOOL_USE hook, never an LLM
tool.

Backend-agnostic by construction: only `batch_create_edges`/
`get_edges_from_node`/`get_edges_to_node` (generic `IGraphDBProvider`
methods) are used, so this works unchanged against ArangoDB and Neo4j — see
`config/constants/neo4j.py`'s `EDGE_COLLECTION_TO_RELATIONSHIP` mapping for
`nodeRelations` -> `NODE_RELATION`, with `DERIVED_FROM` carried as the
`relationshipType` property exactly like every other `RecordRelations`
value already is.

Provenance (P3.5) is per output version: run, task, workspace version,
command, inputs and the source records the content came from. It lives on
the artifact document as a JSON string (`artifacts.provenance`, Neo4j-safe
like `versions`) because a version may have no source at all and still be
known, which an edge cannot say.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.core.org_context import org_of
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from harness_core.artifacts.types import MAX_PROVENANCE_SOURCES, union_sources

from .models import ArtifactLineage, ArtifactProvenance

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

__all__ = ["LineageTracker"]

_RECORDS = CollectionNames.RECORDS.value
_ARTIFACTS = CollectionNames.ARTIFACTS.value
_RELATIONS = CollectionNames.NODE_RELATIONS.value
# Bounds the document reads one `sources_of` call keeps in flight.
_SOURCES_CONCURRENCY = 8


def _endpoint(edge: dict, side: str) -> str:
    """The key at one end of an edge: Neo4j returns `from_id`/`to_id`, while
    ArangoDB returns raw `_from`/`_to` handles (`records/<key>`)."""
    key = edge.get(f"{side}_id")
    if key:
        return str(key)
    return str(edge.get(f"_{side}") or "").split("/", 1)[-1]


class LineageTracker:
    def __init__(self, graph_provider: Any) -> None:
        self._graph_provider = graph_provider

    async def record_derivation(
        self, *, output_artifact_id: str, output_version: int,
        code_artifact_id: str | None = None, code_version: int | None = None,
        provenance: ArtifactProvenance | None = None,
    ) -> None:
        """Record how `output_version` of `output_artifact_id` was made: the
        code artifact that produced it (an edge, below) and/or its
        provenance (`_record_provenance`)."""
        if provenance is not None:
            await self._record_provenance(output_artifact_id, output_version, provenance)
        if code_artifact_id is not None:
            await self._record_code_edge(
                output_artifact_id=output_artifact_id, code_artifact_id=code_artifact_id,
                code_version=code_version or 1, output_version=output_version,
            )

    async def _record_code_edge(
        self, *, output_artifact_id: str, code_artifact_id: str, code_version: int, output_version: int,
    ) -> None:
        """Write ``output --DERIVED_FROM(sourceVersion, derivedVersion)--> code``.

        Idempotent per (output, code) pair is NOT enforced here — every
        run_code execution against the same code artifact creates a new
        edge carrying that run's specific versions, so the full history of
        "which code version produced which output version" is preserved
        rather than collapsed to a single edge. `get_lineage_for_output`
        returns the MOST RECENT one.
        """
        now = get_epoch_timestamp_in_ms()
        edge = {
            "from_id": output_artifact_id,
            "from_collection": _RECORDS,
            "to_id": code_artifact_id,
            "to_collection": _RECORDS,
            "relationshipType": RecordRelations.DERIVED_FROM.value,
            "sourceVersion": code_version,
            "derivedVersion": output_version,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }
        ok = await self._graph_provider.batch_create_edges([edge], _RELATIONS)
        if not ok:
            logger.warning(
                "Failed to record DERIVED_FROM edge: output=%s code=%s (code_v=%d, output_v=%d)",
                output_artifact_id, code_artifact_id, code_version, output_version,
            )
            return
        logger.info(
            "Recorded lineage: output=%s (v%d) DERIVED_FROM code=%s (v%d)",
            output_artifact_id, output_version, code_artifact_id, code_version,
        )

    async def _record_provenance(self, artifact_id: str, version: int, provenance: ArtifactProvenance) -> None:
        """Store `provenance` for `version`, merged with what is already there:
        the union of sources, and unknown wins. Read-modify-write: a lost
        concurrent update leaves that version without an entry, which reads
        as unknown (hidden), never as readable."""
        doc: dict[str, object] = await self._graph_provider.get_document(artifact_id, _ARTIFACTS) or {}
        entries = {entry.version: entry for entry in _parse_provenance(artifact_id, doc.get("provenance"))}
        sources = _bounded(provenance.source_record_ids)
        earlier = entries.get(version)
        if earlier is not None:
            merged = union_sources(_frozen(earlier.source_record_ids), _frozen(sources))
            sources = sorted(merged) if merged is not None else None
        entries[version] = provenance.model_copy(update={
            "version": version,
            "source_record_ids": sources,
            "recorded_at": provenance.recorded_at or get_epoch_timestamp_in_ms(),
        })
        payload = json.dumps(
            [e.model_dump(mode="json", by_alias=True) for _, e in sorted(entries.items())],
            separators=(",", ":"),
        )
        await self._graph_provider.update_node(artifact_id, _ARTIFACTS, {"provenance": payload})
        logger.info(
            "artifacts.provenance_recorded artifactId=%s version=%d sources=%s",
            artifact_id, version, "unknown" if sources is None else len(sources),
        )

    async def provenance_of(self, artifact_id: str) -> list[ArtifactProvenance]:
        doc: dict[str, object] = await self._graph_provider.get_document(artifact_id, _ARTIFACTS) or {}
        return _parse_provenance(artifact_id, doc.get("provenance"))

    async def sources_of(
        self, org_id: str, artifact_ids: Sequence[str],
    ) -> dict[str, frozenset[str] | None]:
        """Source records over every version of each artifact; `None` when the
        artifact is missing, deleted, of another org, or any version 1..current
        lacks a known entry. I/O errors propagate so callers fail closed."""
        gate = asyncio.Semaphore(_SOURCES_CONCURRENCY)

        async def one(artifact_id: str) -> frozenset[str] | None:
            async with gate:
                record = await self._graph_provider.get_document(artifact_id, _RECORDS)
                if not record or org_of(record) != org_id or record.get("isDeleted"):
                    return None
                doc: dict[str, object] = await self._graph_provider.get_document(artifact_id, _ARTIFACTS) or {}
            entries = {e.version: e for e in _parse_provenance(artifact_id, doc.get("provenance"))}
            current = int(record.get("version") or 1)
            per_version = [entries.get(v) for v in range(1, current + 1)]
            if any(entry is None for entry in per_version):
                return None
            return union_sources(*(_frozen(e.source_record_ids) for e in per_version if e is not None))

        wanted = list(dict.fromkeys(artifact_ids))
        answers = await asyncio.gather(*(one(aid) for aid in wanted))
        return dict(zip(wanted, answers, strict=True))

    async def get_lineage_for_output(self, output_artifact_id: str) -> ArtifactLineage | None:
        """The code artifact (+ versions) that produced `output_artifact_id`,
        most-recently-recorded edge wins when a code artifact was re-run
        more than once against the same output."""
        edges = await self._graph_provider.get_edges_from_node(f"{_RECORDS}/{output_artifact_id}", _RELATIONS)
        derived = [e for e in edges if e.get("relationshipType") == RecordRelations.DERIVED_FROM.value]
        if not derived:
            return None
        latest = max(derived, key=lambda e: e.get("createdAtTimestamp") or 0)
        return ArtifactLineage(
            output_artifact_id=output_artifact_id,
            code_artifact_id=_endpoint(latest, "to"),
            code_version=int(latest.get("sourceVersion") or 1),
            output_version=int(latest.get("derivedVersion") or 1),
        )

    async def get_outputs_for_code(self, code_artifact_id: str) -> list[ArtifactLineage]:
        """Every output ever derived from any version of `code_artifact_id`
        — lets a follow-up turn answer "what did this code produce?"."""
        edges = await self._graph_provider.get_edges_to_node(f"{_RECORDS}/{code_artifact_id}", _RELATIONS)
        derived = [e for e in edges if e.get("relationshipType") == RecordRelations.DERIVED_FROM.value]
        return [
            ArtifactLineage(
                output_artifact_id=_endpoint(e, "from"),
                code_artifact_id=code_artifact_id,
                code_version=int(e.get("sourceVersion") or 1),
                output_version=int(e.get("derivedVersion") or 1),
            )
            for e in derived
        ]


def _parse_provenance(artifact_id: str, raw: object) -> list[ArtifactProvenance]:
    """The stored entries; a malformed field reads as no entries (unknown, so hidden)."""
    if not raw:
        return []
    try:
        items: object = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(items, list):
            raise TypeError("provenance is not a list")
        return [ArtifactProvenance.model_validate(item) for item in cast("list[object]", items)]
    except (TypeError, ValueError, ValidationError):
        logger.warning("artifacts.provenance_unreadable artifactId=%s", artifact_id)
        return []


def _frozen(ids: list[str] | None) -> frozenset[str] | None:
    return frozenset(ids) if ids is not None else None


def _bounded(ids: list[str] | None) -> list[str] | None:
    if ids is None or len(set(ids)) > MAX_PROVENANCE_SOURCES:
        return None
    return sorted(set(ids))
