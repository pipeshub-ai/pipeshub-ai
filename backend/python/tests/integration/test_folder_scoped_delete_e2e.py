"""Folder deletes stay inside the folder, on a real Neo4j and a real ArangoDB.

``delete_records_recursive`` is the one delete path for files and folders. Two
guarantees are pinned here, on both backends:

* With ``within_folder_id``, a root is deleted only if it sits under that folder
  through containment edges (PARENT_CHILD / ATTACHMENT). A record in a sibling
  folder is kept even when a RELATED or DERIVED_FROM edge links it to the folder's
  contents, and a record moved out of the folder is kept. The check runs in the
  delete's own query, so there is no window between checking and deleting.
* A cascade follows containment edges only. On ArangoDB it used to filter on the
  last edge of each path, so it walked through a RELATED edge and deleted the
  target's children.

The tree::

    folder_a/            folder_a -RELATED-> b_sub
      a1                 a1 -DERIVED_FROM-> b1
        (attachment)     a1 -ATTACHMENT-> attached
      sub/
        s1
    folder_b/
      b1
      b_sub/
        b_sub_file
    root_file

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import connect_arango, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "folder_scoped_delete_it"
ORG_ID = "org-folder-delete-it"
NAMES = ("folder_a", "a1", "attached", "sub", "s1", "folder_b", "b1", "b_sub", "b_sub_file", "root_file")
FOLDERS = {"folder_a", "sub", "folder_b", "b_sub"}

logger = logging.getLogger("folder-scoped-delete-it")


@dataclass
class _Tree:
    graph: IGraphDBProvider
    connector_id: str
    ids: dict[str, str]

    async def exists(self, name: str) -> bool:
        return await self.graph.get_document(self.ids[name], CollectionNames.RECORDS.value) is not None

    async def link(self, parent: str, child: str, relation: str) -> None:
        now = get_epoch_timestamp_in_ms()
        assert await self.graph.batch_create_edges(
            [{
                "from_id": self.ids[parent], "from_collection": CollectionNames.RECORDS.value,
                "to_id": self.ids[child], "to_collection": CollectionNames.RECORDS.value,
                "relationshipType": relation, "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.RECORD_RELATIONS.value,
        )

    async def delete(self, names: list[str], **kwargs: object) -> dict:
        return await self.graph.delete_records_recursive(
            [self.ids[n] for n in names], self.connector_id, **kwargs
        )


def _record(connector_id: str, name: str) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    return FileRecord(
        org_id=ORG_ID,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=f"{name}-{uuid.uuid4().hex[:8]}",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id=connector_id,
        mime_type="text/plain",
        is_file=name not in FOLDERS,
        created_at=now,
        updated_at=now,
        source_created_at=now,
        source_updated_at=now,
    )


async def _remove_everything(graph: IGraphDBProvider, tree: _Tree) -> None:
    left = [rid for rid in tree.ids.values() if await graph.get_document(rid, CollectionNames.RECORDS.value)]
    if left:
        await graph.delete_records_recursive(left, tree.connector_id)


@pytest.fixture(params=["neo4j", "arango"])
async def tree(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Tree]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        connector_id = f"kb-folder-delete-{uuid.uuid4().hex[:10]}"
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = ORG_ID
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)

        records = {name: _record(connector_id, name) for name in NAMES}
        await processor.on_new_records([(record, []) for record in records.values()])
        t = _Tree(graph, connector_id, {name: record.id for name, record in records.items()})
        cleanup.push_async_callback(_remove_everything, graph, t)

        for parent, child in (
            ("folder_a", "a1"), ("folder_a", "sub"), ("sub", "s1"),
            ("folder_b", "b1"), ("folder_b", "b_sub"), ("b_sub", "b_sub_file"),
        ):
            await t.link(parent, child, "PARENT_CHILD")
        await t.link("a1", "attached", "ATTACHMENT")
        await t.link("a1", "b1", "DERIVED_FROM")
        await t.link("folder_a", "b_sub", "RELATED")
        assert all([await t.exists(name) for name in NAMES]), "the tree was not stored"
        yield t


async def test_only_records_inside_the_folder_are_deleted(tree: _Tree) -> None:
    result = await tree.delete(["a1", "s1", "b1", "root_file"], within_folder_id=tree.ids["folder_a"])

    assert result["success"] is True
    assert {f["record_id"] for f in result["failed_records"]} == {tree.ids["b1"], tree.ids["root_file"]}
    for name in ("a1", "attached", "s1"):
        assert not await tree.exists(name), f"{name} is inside folder_a and should be gone"
    for name in ("b1", "root_file", "folder_a", "sub", "folder_b"):
        assert await tree.exists(name), (
            f"{name} was deleted through folder_a's route although it is not inside folder_a"
        )


async def test_a_record_moved_out_of_the_folder_is_kept(tree: _Tree) -> None:
    """The check is part of the delete query, so it sees the tree as it is at delete time."""
    assert await tree.graph.delete_parent_child_edge_to_record(tree.ids["s1"])
    await tree.link("folder_b", "s1", "PARENT_CHILD")

    result = await tree.delete(["s1"], within_folder_id=tree.ids["folder_a"])

    assert result["successfully_deleted"] == 0
    assert await tree.exists("s1"), "a record moved out of the folder was still deleted through it"


async def test_a_folder_cascade_follows_only_containment_edges(tree: _Tree) -> None:
    result = await tree.delete(["folder_a"])

    assert result["success"] is True
    for name in ("folder_a", "a1", "attached", "sub", "s1"):
        assert not await tree.exists(name), f"{name} is under folder_a and should be gone"
    for name in ("folder_b", "b1", "b_sub", "b_sub_file", "root_file"):
        assert await tree.exists(name), (
            f"{name} was deleted by folder_a's cascade through a RELATED or DERIVED_FROM edge"
        )
