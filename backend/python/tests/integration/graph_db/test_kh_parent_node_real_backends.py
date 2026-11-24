"""The knowledge hub's parent of a node, against a real Neo4j and a real ArangoDB.

The tree is written through the real ``DataSourceEntitiesProcessor``, as a connector writes it. ArangoDB
answered no parent for every connector record: the branch that reads it is a subquery, whose value is an
array, and an array names no document.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml
(NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; the defaults match it). The
PostgreSQL counterpart is ``postgres/test_kh_parent_node.py``.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("kh-parent-node-it")

FOLDER_MIME_TYPES = [MimeTypes.FOLDER.value]


@dataclass
class _Tree:
    graph: object
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    now: int

    def group(self, name: str) -> RecordGroup:
        return RecordGroup(
            org_id=self.org_id,
            name=name,
            external_group_id=f"{name}-{self.connector_id}",
            connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
            connector_id=self.connector_id,
            group_type=RecordGroupType.DRIVE,
            source_created_at=self.now,
            source_updated_at=self.now,
        )

    def record(self, name: str, *, group: str, parent: str | None = None, folder: bool = False) -> FileRecord:
        return FileRecord(
            id=str(uuid.uuid4()),
            org_id=self.org_id,
            record_name=name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_id=f"{name}-{self.connector_id}",
            external_record_group_id=f"{group}-{self.connector_id}",
            external_revision_id="rev-1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
            connector_id=self.connector_id,
            created_at=self.now,
            updated_at=self.now,
            source_created_at=self.now,
            source_updated_at=self.now,
            is_file=not folder,
            mime_type=MimeTypes.FOLDER.value if folder else MimeTypes.PDF.value,
            parent_external_record_id=f"{parent}-{self.connector_id}" if parent else None,
            parent_record_type=RecordType.FILE if parent else None,
            indexing_status=ProgressStatus.COMPLETED.value,
        )

    async def record_id(self, name: str) -> str:
        stored = await self.graph.get_record_by_external_id(self.connector_id, f"{name}-{self.connector_id}")
        assert stored is not None, name
        return stored.id

    async def group_id(self, name: str) -> str:
        stored = await self.graph.get_record_group_by_external_id(self.connector_id, f"{name}-{self.connector_id}")
        assert stored is not None, name
        return stored.id

    async def parent_of(self, node_id: str) -> dict | None:
        return await self.graph.get_knowledge_hub_parent_node(node_id, FOLDER_MIME_TYPES)


async def _seed(graph: object) -> _Tree:
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    org_id, connector_id = f"org-{run}", f"drive-{run}"
    assert await graph.batch_upsert_nodes(
        [{"id": connector_id, "orgId": org_id, "name": "Drive", "type": Connectors.GOOGLE_DRIVE_WORKSPACE.value,
          "appGroup": "Google Workspace", "scope": "team", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
    processor.org_id = org_id
    producer = MagicMock()
    producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    processor.messaging_producer = producer
    return _Tree(graph, processor, org_id, connector_id, now)


async def _cleanup(tree: _Tree) -> None:
    with contextlib.suppress(Exception):
        records = await tree.graph.get_nodes_by_filters(
            CollectionNames.RECORDS.value, {"connectorId": tree.connector_id}
        ) or []
        groups = await tree.graph.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": tree.connector_id}
        ) or []
        await tree.graph.delete_nodes_and_edges(
            [r.get("_key") or r.get("id") for r in records], CollectionNames.RECORDS.value
        )
        await tree.graph.delete_nodes_and_edges(
            [g.get("_key") or g.get("id") for g in groups], CollectionNames.RECORD_GROUPS.value
        )
        await tree.graph.delete_nodes_and_edges([tree.connector_id], CollectionNames.APPS.value)


@pytest.fixture(params=["neo4j", "arango"])
async def tree(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Tree]:
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "kh_parent_node_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    seeded = await _seed(graph)
    try:
        # drive "drive" > folder "docs" > file "guide" > file "figure"; "loose" sits directly in the drive.
        await seeded.processor.on_new_record_groups([(seeded.group("drive"), [])])
        await seeded.processor.on_new_records([
            (seeded.record("docs", group="drive", folder=True), []),
            (seeded.record("guide", group="drive", parent="docs"), []),
            (seeded.record("figure", group="drive", parent="guide"), []),
            (seeded.record("loose", group="drive"), []),
        ])
        yield seeded
    finally:
        await _cleanup(seeded)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


async def test_a_record_under_a_folder_has_the_folder_as_its_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("guide"))
    assert parent == {"id": await tree.record_id("docs"), "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_under_a_record_that_is_not_a_folder_has_it_as_a_record_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("figure"))
    assert parent == {"id": await tree.record_id("guide"), "name": "guide", "nodeType": "record", "subType": "FILE"}


async def test_a_record_directly_in_a_group_has_the_group_as_its_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("loose"))
    assert parent == {"id": await tree.group_id("drive"), "name": "drive", "nodeType": "recordGroup", "subType": "DRIVE"}


async def test_a_group_has_its_app_as_parent_and_the_app_has_none(tree: _Tree) -> None:
    assert await tree.parent_of(await tree.group_id("drive")) == {
        "id": tree.connector_id, "name": "Drive", "nodeType": "app", "subType": Connectors.GOOGLE_DRIVE_WORKSPACE.value,
    }
    assert await tree.parent_of(tree.connector_id) is None
