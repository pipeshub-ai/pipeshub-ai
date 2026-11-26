"""``Lives`` over the PostgreSQL graph provider: a pushed record is a row naming a revision."""

from __future__ import annotations

from typing import Any

from app.connectors.sources.localKB.push import contract as C
from tests.integration.graph_db.delete_guard_support import pushed_bytes

from .pg_support import OrgBound, new_org


class PgLives:
    def __init__(self, pg_db: Any) -> None:  # noqa: ANN401
        from .test_trash import bound

        self.org_id = new_org()
        self.graph: OrgBound = bound(pg_db, self.org_id)
        self.record_id = "pushed-1"
        self.revision = 0

    def _row(self, revision: int, data: bytes) -> dict[str, Any]:
        return {
            "_key": self.record_id,
            "orgId": self.org_id,
            "recordName": "doc",
            "recordType": "FILE",
            "origin": "UPLOAD",
            "connectorName": "KB",
            "connectorId": "kb1",
            "externalRecordId": f"doc-{revision}",
            "version": revision,
            "mimeType": "application/vnd.pipeshub.blocks",
            "indexingStatus": "QUEUED",
            "pushOrigin": True,
            "externalRevisionId": C.revision_id("a" * 64, C.content_rev(data)),
            "contentRev": C.content_rev(data),
        }

    async def commit(self, revision: int) -> bytes:
        data = pushed_bytes(revision)
        assert await self.graph.batch_upsert_nodes(
            [self._row(revision, data)], "records"
        )
        return data

    async def purge(self) -> None:
        assert await self.graph.delete_nodes_and_edges([self.record_id], "records")
        assert await self.graph.get_document(self.record_id, "records") is None

    async def repush(self) -> None:
        await self.commit(1)
