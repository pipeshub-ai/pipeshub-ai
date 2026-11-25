"""Erasing artifacts (E7): a deleted conversation's, and drafts a cancelled task discarded (T5).

Each artifact's stored bytes go first (every version, through Node storage's
purge), then its graph nodes and edges. A crash in between leaves the graph
nodes, so the next call finds the artifact again, purges an already-absent
document (no error) and deletes the nodes: every call is idempotent and the
caller repeats it until nothing remains.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of
from app.models.entities import RecordType

from .registry import DRAFT_DISCARDED_REASON

logger = logging.getLogger(__name__)

ERASE_PAGE = 50

__all__ = ["ERASE_PAGE", "ArtifactErasureReport", "ArtifactEraser", "BlobPurger"]


class BlobPurger(Protocol):
    async def purge_document(self, org_id: str, document_id: str) -> int:
        """How many documents this call removed: 0 when it was already gone."""
        ...


@dataclass(frozen=True)
class ArtifactErasureReport:
    artifacts: int
    blobs: int
    remaining: bool


def _key(document: dict[str, Any]) -> str | None:
    key = document.get("_key") or document.get("id")
    return str(key) if key else None


class ArtifactEraser:
    def __init__(self, graph_provider: Any, blobs: BlobPurger, *, page: int = ERASE_PAGE) -> None:  # noqa: ANN401 - IGraphDBProvider
        self._graph = graph_provider
        self._blobs = blobs
        self._page = page

    async def erase_conversation(self, org_id: str, conversation_id: str) -> ArtifactErasureReport:
        """Up to one page of the conversation's artifacts; `remaining` while more exist."""
        documents = await self._graph.get_documents_paginated(
            CollectionNames.ARTIFACTS.value,
            skip=0,
            limit=self._page + 1,
            filters={"orgId": org_id, "conversationId": conversation_id},
            sort_field="_key",
            raise_on_error=True,
        )
        keys = [key for key in map(_key, documents[: self._page]) if key is not None]
        artifacts, blobs = await self._erase(org_id, keys)
        return ArtifactErasureReport(artifacts, blobs, remaining=len(documents) > self._page)

    async def collect_discarded(self, org_id: str, before_ms: int) -> ArtifactErasureReport:
        """Drafts discarded before `before_ms`; one stamped later waits for a later pass."""
        records = await self._graph.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=self._page,
            filters={"orgId": org_id, "reason": DRAFT_DISCARDED_REASON},
            sort_field="_key",
            raise_on_error=True,
        )
        # The discard is the last write to a draft's record, so its update time is the discard time.
        due = [
            key
            for record in records
            if (key := _key(record)) is not None and int(record.get("updatedAtTimestamp") or 0) < before_ms
        ]
        artifacts, blobs = await self._erase(org_id, due)
        return ArtifactErasureReport(artifacts, blobs, remaining=len(records) == self._page and len(due) == len(records))

    async def erase_org(self, org_id: str) -> ArtifactErasureReport:
        """Up to one page of every artifact the org holds, whatever its conversation (CLEANUP-G1).

        Records first, by the ``ARTIFACT`` record type, then any artifact node a crash left without its record.
        """
        records = await self._graph.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=self._page + 1,
            filters={"orgId": org_id, "recordType": RecordType.ARTIFACT.value},
            sort_field="_key",
            raise_on_error=True,
        )
        if records:
            keys = [key for key in map(_key, records[: self._page]) if key is not None]
            artifacts, blobs = await self._erase(org_id, keys)
            return ArtifactErasureReport(artifacts, blobs, remaining=True)
        orphans = await self._graph.get_documents_paginated(
            CollectionNames.ARTIFACTS.value,
            skip=0,
            limit=self._page + 1,
            filters={"orgId": org_id},
            sort_field="_key",
            raise_on_error=True,
        )
        keys = [key for key in map(_key, orphans[: self._page]) if key is not None]
        if keys:
            await self._graph.delete_nodes_and_edges(keys, CollectionNames.ARTIFACTS.value)
        return ArtifactErasureReport(len(keys), 0, remaining=len(orphans) > self._page)

    async def _erase(self, org_id: str, keys: list[str]) -> tuple[int, int]:
        artifacts = blobs = 0
        for key in keys:
            record: dict[str, Any] | None = await self._graph.get_document(key, CollectionNames.RECORDS.value)
            if record is not None and org_of(record) != org_id:
                logger.warning("artifacts.erase_refused reason=foreign_org artifact=%s", key)
                continue
            document_id = str((record or {}).get("externalRecordId") or "")
            if document_id and await self._blobs.purge_document(org_id, document_id):
                blobs += 1
            await self._graph.delete_nodes_and_edges([key], CollectionNames.RECORDS.value)
            await self._graph.delete_nodes_and_edges([key], CollectionNames.ARTIFACTS.value)
            artifacts += 1
        return artifacts, blobs
