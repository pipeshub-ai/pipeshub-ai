"""Folder deletes stay inside the folder, on a real Neo4j and a real ArangoDB.

``delete_records_recursive`` is the one delete path for files and folders. Two
guarantees are pinned here, on both backends:

* With ``within_folder_id``, a root is deleted only if it sits under that folder
  through containment edges (PARENT_CHILD / ATTACHMENT). A record in a sibling
  folder is kept even when a RELATED or DERIVED_FROM edge links it to the folder's
  contents, and a record moved out of the folder is kept. The check runs in the
  delete's own query, so there is no window between checking and deleting. A
  record moved into the subtree mid-delete is never left under a deleted parent:
  Neo4j deletes and reports it, ArangoDB stops the delete.
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
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

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
    processor: DataSourceEntitiesProcessor

    async def add(self, names: list[str], *, folders: bool) -> None:
        records = [_record(self.connector_id, name, is_file=not folders) for name in names]
        await self.processor.on_new_records([(record, []) for record in records])
        self.ids.update({record.record_name: record.id for record in records})

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


def _record(connector_id: str, name: str, *, is_file: bool | None = None) -> FileRecord:
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
        is_file=(name not in FOLDERS) if is_file is None else is_file,
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
            backend_unavailable(request.param, exc)
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
        t = _Tree(graph, connector_id, {name: record.id for name, record in records.items()}, processor)
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


async def test_a_move_between_the_check_and_the_delete_keeps_the_record(tree: _Tree) -> None:
    """The move commits right after the delete has read the tree and before it deletes."""
    owner = tree.graph.client if isinstance(tree.graph, Neo4jProvider) else tree.graph
    original = owner.execute_query
    moved = False

    async def move_after_the_inventory(query, *args, **kwargs):
        nonlocal moved
        result = await original(query, *args, **kwargs)
        if not moved and "valid_root" in query:
            moved = True
            assert await tree.graph.delete_parent_child_edge_to_record(tree.ids["s1"])
            await tree.link("folder_b", "s1", "PARENT_CHILD")
        return result

    owner.execute_query = move_after_the_inventory
    try:
        result = await tree.delete(["s1"], within_folder_id=tree.ids["folder_a"])
    finally:
        owner.execute_query = original

    assert moved, "the test never reached the delete's own check"
    assert await tree.exists("s1"), f"a record moved out mid-delete was still deleted: {result}"
    assert result.get("successfully_deleted", 0) == 0


async def test_containment_deeper_than_twenty_levels_is_followed(tree: _Tree) -> None:
    levels = [f"level_{i}" for i in range(25)]
    await tree.add(levels, folders=True)
    await tree.add(["deep_file"], folders=False)
    chain = ["folder_a", *levels, "deep_file"]
    for parent, child in zip(chain, chain[1:]):
        await tree.link(parent, child, "PARENT_CHILD")

    scoped = await tree.delete(["deep_file"], within_folder_id=tree.ids["folder_a"])
    assert scoped["successfully_deleted"] == 1, f"a file 26 levels down was not seen as inside: {scoped}"
    assert not await tree.exists("deep_file")

    await tree.add(["deep_file_2"], folders=False)
    await tree.link(levels[-1], "deep_file_2", "PARENT_CHILD")
    await tree.delete(["folder_a"])
    for name in (*levels, "deep_file_2"):
        assert not await tree.exists(name), f"{name} survived its folder's delete"


async def test_what_is_reported_deleted_is_exactly_what_was_deleted(tree: _Tree) -> None:
    """A record moved into the subtree after the inventory: reported iff actually removed."""
    owner = tree.graph.client if isinstance(tree.graph, Neo4jProvider) else tree.graph
    original = owner.execute_query
    moved = False

    async def move_in_after_the_inventory(query, *args, **kwargs):
        nonlocal moved
        result = await original(query, *args, **kwargs)
        if not moved and "valid_root" in query:
            moved = True
            assert await tree.graph.delete_parent_child_edge_to_record(tree.ids["b1"])
            await tree.link("sub", "b1", "PARENT_CHILD")
        return result

    owner.execute_query = move_in_after_the_inventory
    try:
        result = await tree.delete(["sub"], within_folder_id=tree.ids["folder_a"])
    finally:
        owner.execute_query = original

    assert moved, "the test never reached the delete's own check"
    reported = {r["record_id"] for r in result.get("deleted_records", [])}
    gone = {tree.ids[n] for n in ("sub", "s1", "b1") if not await tree.exists(n)}
    assert reported == gone, f"reported deleted {reported}, actually gone {gone}"
    if await tree.exists("b1"):
        assert await tree.exists("sub"), f"b1 was moved into sub and kept, but sub was deleted: {result}"
        assert await tree.exists("s1"), f"the stopped delete removed s1: {result}"
        assert not reported, f"the stopped delete reported removed records: {result}"


async def test_a_move_committed_during_the_deletes_deletes_nothing(tree: _Tree) -> None:
    """ArangoDB only: a move that commits after the first re-read is outside the delete's
    snapshot; the re-read after the deletes must roll the whole delete back. (Neo4j's delete
    walks the live tree in the statement itself.)"""
    if isinstance(tree.graph, Neo4jProvider):
        pytest.skip("Neo4j re-walks the live subtree in its delete statement")
    original = tree.graph.execute_query
    moved = False

    async def move_in_after_the_first_reread(query, *args, **kwargs) -> object:
        nonlocal moved
        result = await original(query, *args, **kwargs)
        if not moved and "@root_keys" in query:
            moved = True
            assert await tree.graph.delete_parent_child_edge_to_record(tree.ids["b1"])
            await tree.link("sub", "b1", "PARENT_CHILD")
        return result

    tree.graph.execute_query = move_in_after_the_first_reread
    try:
        result = await tree.delete(["sub"], within_folder_id=tree.ids["folder_a"])
    finally:
        tree.graph.execute_query = original

    assert moved, "the delete never re-read the folder"
    assert result["success"] is False and result.get("code") == 409, result
    for name in ("sub", "s1", "b1"):
        assert await tree.exists(name), f"{name} was deleted by a delete that reported nothing deleted: {result}"
