"""`LineageTracker` — auto-captures `DERIVED_FROM` edges over the existing
`recordRelations` edge collection. There is deliberately NO public method
that takes lineage asserted by a caller-supplied arbitrary pair without
version numbers pinned by the harness itself — see module docstring in the
plan: lineage is an observable fact of "this code run produced these
files", captured by `sandbox_bridge.py`'s POST_TOOL_USE hook, never an LLM
tool.

Backend-agnostic by construction: only `batch_create_edges`/
`get_edges_from_node`/`get_edges_to_node` (generic `IGraphDBProvider`
methods) are used, so this works unchanged against ArangoDB and Neo4j — see
`config/constants/neo4j.py`'s `EDGE_COLLECTION_TO_RELATIONSHIP` mapping for
`recordRelations` -> `RECORD_RELATION`, with `DERIVED_FROM` carried as the
`relationshipType` property exactly like every other `RecordRelations`
value already is.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .models import ArtifactLineage

logger = logging.getLogger(__name__)

__all__ = ["LineageTracker"]

_RECORDS = CollectionNames.RECORDS.value
_RELATIONS = CollectionNames.RECORD_RELATIONS.value


class LineageTracker:
    def __init__(self, graph_provider: Any) -> None:
        self._graph_provider = graph_provider

    async def record_derivation(
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
            code_artifact_id=str(latest.get("to_id")),
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
                output_artifact_id=str(e.get("from_id")),
                code_artifact_id=code_artifact_id,
                code_version=int(e.get("sourceVersion") or 1),
                output_version=int(e.get("derivedVersion") or 1),
            )
            for e in derived
        ]
