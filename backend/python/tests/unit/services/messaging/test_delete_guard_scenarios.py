"""The real-store scenarios of the late-purge guard and of pushed values, run on an in-memory graph.

``tests/integration/graph_db/delete_guard_support.py`` holds the scenarios the Neo4j, ArangoDB and PostgreSQL
integration tests run. Here they run on a dict, so a mistake in a scenario itself shows up without a store (the
stores' own behaviour -- what ``get_document`` returns for a purged or re-created row -- is what those tests add).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.connectors.sources.localKB.push import contract as C
from tests.integration.graph_db.delete_guard_support import CHECKS, pushed_bytes

pytestmark = pytest.mark.unit


class DictGraph:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_document(
        self, key: str, collection: str, **_kw: object
    ) -> dict[str, Any] | None:
        row = self.rows.get((collection, key))
        return dict(row) if row else None

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str) -> bool:
        for node in nodes:
            self.rows[(collection, node["_key"])] = dict(node)
        return True

    async def delete_nodes_and_edges(self, keys: list[str], collection: str) -> bool:
        for key in keys:
            self.rows.pop((collection, key), None)
        return True


class DictLives:
    def __init__(self) -> None:
        self.graph = DictGraph()
        self.org_id, self.record_id = "org-1", "pushed-1"

    async def commit(self, revision: int) -> bytes:
        data = pushed_bytes(revision)
        await self.graph.batch_upsert_nodes(
            [
                {
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
            ],
            "records",
        )
        return data

    async def purge(self) -> None:
        await self.graph.delete_nodes_and_edges([self.record_id], "records")

    async def repush(self) -> None:
        await self.commit(1)


@pytest.mark.parametrize("name", sorted(CHECKS))
async def test_scenario_on_an_in_memory_graph(name: str) -> None:
    await CHECKS[name](DictLives())
