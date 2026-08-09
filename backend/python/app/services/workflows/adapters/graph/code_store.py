"""GraphWorkflowCodeStore: ICodeStore over IGraphDBProvider.

Workflow source files are small Python scripts (typically < 50 KB); storing
them inline in the graph DB avoids blob-storage pipeline complexity for phase 1.
Document schema: `id` = artifact_id, `source` = UTF-8 source text.

Uses only generic `IGraphDBProvider` methods so it works with both ArangoDB
and Neo4j.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.services.workflows.domain.models import ArtifactRef

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["GraphWorkflowCodeStore", "ArangoWorkflowCodeStore"]

logger = logging.getLogger(__name__)

_COLLECTION = CollectionNames.WORKFLOW_SOURCES.value


class GraphWorkflowCodeStore:
    """ICodeStore backed by graph DB (inline source storage)."""

    def __init__(self, graph_provider: "IGraphDBProvider") -> None:
        self._graph = graph_provider

    async def put(
        self,
        workflow_id: str,
        org_id: str,
        source: bytes,
        *,
        content_type: str = "text/x-python",
    ) -> ArtifactRef:
        artifact_id = str(uuid.uuid4())
        node = {
            "id": artifact_id,
            "artifact_id": artifact_id,
            "workflow_id": workflow_id,
            "org_id": org_id,
            "content_type": content_type,
            "source": source.decode("utf-8"),
            "size_bytes": len(source),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            result = await self._graph.batch_upsert_nodes(
                nodes=[node],
                collection=_COLLECTION,
            )
        except Exception:
            logger.exception(
                "Failed to store workflow source for workflow %s (artifact=%s)",
                workflow_id, artifact_id,
            )
            raise
        if result is False:
            raise RuntimeError(f"Failed to store workflow source for workflow {workflow_id}")
        return ArtifactRef(artifact_id=artifact_id, version="1")

    async def get(self, ref: ArtifactRef) -> bytes:
        try:
            doc = await self._graph.get_document(
                document_key=ref.artifact_id,
                collection=_COLLECTION,
            )
        except Exception:
            logger.exception(
                "Failed to read workflow source artifact %s", ref.artifact_id,
            )
            raise
        if doc is None:
            raise KeyError(f"Workflow source artifact {ref.artifact_id!r} not found")
        source = doc.get("source", "")
        return source.encode("utf-8") if isinstance(source, str) else bytes(source)

    async def delete(self, ref: ArtifactRef) -> bool:
        """Only used to clean up a blob whose version row was never created —
        deleting source a stored version still points at would make that
        version unrunnable."""
        return bool(await self._graph.delete_nodes([ref.artifact_id], _COLLECTION))


ArangoWorkflowCodeStore = GraphWorkflowCodeStore
