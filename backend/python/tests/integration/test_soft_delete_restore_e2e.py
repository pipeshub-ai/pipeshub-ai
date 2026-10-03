"""Restore from the trash against a real Neo4j and a real ArangoDB.

Drives the production code: ``KnowledgeBaseService.restore_record`` over a real
provider, with the KB's ``DataSourceEntitiesProcessor`` on a real
``GraphDataStore`` and ``ENABLE_SOFT_DELETE`` on. A collection holds a folder
"Docs" with two files (one with an attachment), and two files at its root.

- A folder delete restored by the folder's id, or by any id in its batch, brings
  back the whole subtree: every delete field cleared, visible again in the
  collection and folder listings and the live-only reads, its files queued
  for indexing again and the folder not.
- A file whose folder went to the trash after it waits for the folder, also
  when the folder goes to the trash after the restore looked: the write checks
  again. ``restore_records`` checks the parent only when asked, as the KB
  restore does; the connector's restore does not.
- A file whose name a new upload has taken comes back as "name (restored)",
  in the graph and in its type doc.
- A record that gave its external id up gets it back when it is free, and is
  refused, unchanged, while a live record holds it.
- A sync that sees again an item the connector deleted restores it, and ends
  on the indexing status it always did. The restore's own write puts it in
  line for indexing, so a sync that fails after it on Neo4j, where that write
  has already committed, leaves a record the stranded sweep picks up.
- ``restore_records`` touches only records still in the trash under the batch
  named, and brings back all of the items it is given or none of them, also
  when the graph refuses one of the writes.
- Taking an external id back from another record in the trash happens in the
  same write as the restore, so a refused restore leaves that record holding it.

Arango enforces the records schema strictly, so its run also proves restore
writes only declared fields.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  cd backend/python && pytest tests/integration/test_soft_delete_restore_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import indexing_main
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers import kb_service as kb_service_module
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.utils import (
    TRASH_STATE_FIELDS,
    TRASHED_EXTERNAL_ID_PREFIX,
)
from app.services.graph_db.neo4j import neo4j_provider as neo4j_provider_module
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.test_record_visibility_e2e import _ids_anywhere
from tests.integration.test_soft_delete_e2e import (
    _connect_arango,
    _connect_neo4j,
    _Producer,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("soft-delete-restore-it")

KB_NAMES = ("folder", "file_a", "file_b", "attachment", "solo", "report")
FOLDER_FILES = ("file_a", "file_b", "attachment")


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    service: KnowledgeBaseService
    producer: _Producer
    org_id: str
    user_id: str
    user_key: str
    kb_id: str
    drive_id: str
    ids: dict[str, str] = field(default_factory=dict)

    async def stored(self, name: str) -> dict | None:
        return await self.graph.get_document(self.ids[name], CollectionNames.RECORDS.value)

    async def live(self, names: tuple[str, ...]) -> set[str]:
        return {n for n in names if (doc := await self.stored(n)) is not None and doc.get("isDeleted") is not True}

    async def trash(self, *names: str) -> None:
        result = await self.processor.on_records_deleted_cascade(
            [self.ids[n] for n in names], self.kb_id,
            delete_source=DeleteSource.USER, deleted_by_user_id=self.user_key,
        )
        assert result["success"] is True and result["softDeleted"] is True, result

    async def restore(self, name: str) -> dict:
        return await self.service.restore_record(self.ids[name], self.user_id, self.org_id)

    def reindexed(self) -> set[str]:
        return {e["payload"]["recordId"] for e in self.producer.of_type(EventTypes.REINDEX_RECORD.value)}


def _file(w: _World, name: str, *, folder: bool = False, kb: bool = True, **extra: object) -> FileRecord:
    fields: dict = {
        "id": w.ids[name],
        "org_id": w.org_id,
        "record_name": "Docs" if folder else f"{name}.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": f"ext-{w.ids[name]}",
        "version": 1,
        "origin": OriginTypes.UPLOAD if kb else OriginTypes.CONNECTOR,
        "connector_name": Connectors.KNOWLEDGE_BASE if kb else Connectors.GOOGLE_DRIVE,
        "connector_id": w.kb_id if kb else w.drive_id,
        "mime_type": "application/vnd.folder" if folder else "application/pdf",
        "indexing_status": ProgressStatus.COMPLETED.value,
        "is_file": not folder,
    }
    return FileRecord(**{**fields, **extra})


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in (*KB_NAMES, "drive_file"):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Restore Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": w.kb_id, "name": "Collection", "type": "KB", "appGroup": "Local Storage", "scope": "personal",
          "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now},
         {"id": w.drive_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
          "scope": "team", "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now,
          "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    await g.batch_upsert_records([
        _file(w, "folder", folder=True),
        *(_file(w, n) for n in ("file_a", "file_b", "attachment", "solo")),
        _file(w, "report", record_name="report.pdf"),
        _file(w, "drive_file", kb=False, external_revision_id="rev-1"),
    ])
    for name in (*FOLDER_FILES, "solo", "report", "drive_file"):
        await g.update_node(w.ids[name], CollectionNames.RECORDS.value, {"virtualRecordId": f"vr-{w.ids[name]}"})
    await _link_to_kb(w, KB_NAMES)

    records, apps, users = CollectionNames.RECORDS.value, CollectionNames.APPS.value, CollectionNames.USERS.value
    await g.batch_create_edges(
        [_edge(w.user_key, users, w.kb_id, apps, role="OWNER", type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [_edge(w.ids["folder"], records, w.ids[c], records, relationshipType="PARENT_CHILD")
         for c in ("file_a", "file_b")]
        + [_edge(w.ids["file_b"], records, w.ids["attachment"], records, relationshipType="ATTACHMENT")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )


def _edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
            "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}


async def _link_to_kb(w: _World, names: tuple[str, ...]) -> None:
    await w.graph.batch_create_edges(
        [_edge(w.ids[n], CollectionNames.RECORDS.value, w.kb_id, CollectionNames.APPS.value, entityType="KB")
         for n in names],
        collection=CollectionNames.BELONGS_TO.value,
    )


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.user_key, w.kb_id, w.drive_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value,
                       CollectionNames.USERS.value, CollectionNames.APPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.RECORD_RELATIONS.value,
                  CollectionNames.INHERIT_PERMISSIONS.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    # The default, where each Neo4j statement commits on its own and a rollback undoes nothing.
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            env = "NEO4J_IT_URI" if request.param == "neo4j" else "ARANGO_IT_URL"
            if os.environ.get(env):
                pytest.fail(f"{request.param} is configured ({env}) but not reachable: {exc!r}")
            pytest.skip(f"{request.param} not configured ({env} unset) and not reachable locally: {exc!r}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        if isinstance(graph, Neo4jProvider):
            # The index restore reads batches by; ensure_schema creates it on a real install.
            await graph.client.execute_query(
                "CREATE INDEX record_delete_batch IF NOT EXISTS FOR (n:Record) ON (n.deleteBatchId)"
            )
        suffix = uuid.uuid4().hex[:10]
        producer = _Producer()
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = producer
        # No blob store here; renames still go through the production rename path.
        monkeypatch.setattr(processor, "_get_storage_cleanup", lambda: None)
        monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())
        monkeypatch.setattr(kb_service_module, "is_soft_delete_enabled", AsyncMock(return_value=True))
        service = KnowledgeBaseService(
            logger, graph, AsyncMock(), processor_for_kb=AsyncMock(return_value=processor),
            config_service=MagicMock(),
        )
        w = _World(
            graph=graph, processor=processor, service=service, producer=producer,
            org_id=f"org-rst-{suffix}", user_id=f"user-rst-{suffix}", user_key=f"ukey-rst-{suffix}",
            kb_id=f"kb-rst-{suffix}", drive_id=f"drive-rst-{suffix}",
        )
        processor.org_id = w.org_id
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("by", ["folder", "file_a"], ids=["by-folder", "by-a-file-inside"])
async def test_a_folder_comes_back_whole_and_is_visible_again(world: _World, by: str) -> None:
    subtree = ("folder", *FOLDER_FILES)
    await world.trash("folder")
    assert await world.live(subtree) == set()
    world.producer.events.clear()

    result = await world.restore(by)

    assert result["success"] is True, result
    assert {r["recordId"] for r in result["restoredRecords"]} == {world.ids[n] for n in subtree}
    assert await world.live(subtree) == set(subtree)
    for name in subtree:
        doc = await world.stored(name)
        assert {k: doc.get(k) for k in TRASH_STATE_FIELDS} == dict.fromkeys(TRASH_STATE_FIELDS), name

    ids = [world.ids[n] for n in subtree]
    assert {r.get("_key") or r.get("id") for r in await world.graph.get_records_by_record_ids(ids, world.org_id)} == set(ids)
    root = _ids_anywhere(await world.graph.get_kb_children(world.kb_id, 0, 100))
    assert world.ids["folder"] in root
    inside = _ids_anywhere(await world.graph.get_folder_children(world.kb_id, world.ids["folder"], 0, 100))
    assert {world.ids["file_a"], world.ids["file_b"]} <= inside

    # Their vectors went with the delete: the files are indexed again, the folder is not.
    assert world.reindexed() == {world.ids[n] for n in FOLDER_FILES}
    for name in FOLDER_FILES:
        assert (await world.stored(name))["indexingStatus"] == ProgressStatus.QUEUED.value, name
    assert (await world.stored("folder"))["indexingStatus"] == ProgressStatus.COMPLETED.value

    again = await world.restore(by)
    assert again["success"] is True and again["restoredRecords"] == []


async def test_a_file_whose_folder_was_trashed_after_it_waits_for_the_folder(world: _World) -> None:
    await world.trash("file_a")
    await world.trash("folder")
    assert (await world.stored("file_a"))["deleteBatchId"] != (await world.stored("folder"))["deleteBatchId"]

    refused = await world.restore("file_a")
    assert refused["code"] == 409, refused
    assert refused["reason"] == "'file_a.pdf' was in 'Docs', which is also in the trash. Restore 'Docs' first, then restore 'file_a.pdf'."
    assert await world.live(("file_a",)) == set()

    assert (await world.restore("folder"))["success"] is True
    assert await world.live(("folder", "file_a", "file_b")) == {"folder", "file_b"}
    assert (await world.restore("file_a"))["success"] is True
    assert await world.live(("file_a",)) == {"file_a"}


async def _restore_while_the_folder_goes_to_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch, owner: object, method: str,
) -> dict:
    """Restore file_a, trashing its folder when *owner*.*method* is first called."""
    original = getattr(owner, method)
    trashed_the_folder = False

    async def trash_the_folder_first(*args: object, **kwargs: object) -> object:
        nonlocal trashed_the_folder
        if not trashed_the_folder:
            trashed_the_folder = True
            await world.trash("folder")
        return await original(*args, **kwargs)

    monkeypatch.setattr(owner, method, trash_the_folder_first)
    result = await world.restore("file_a")
    monkeypatch.setattr(owner, method, original)
    assert trashed_the_folder, "the restore never reached its write"
    return result


async def _assert_refused_for_the_folder(world: _World, refused: dict, batch: str) -> None:
    assert (refused["success"], refused["code"]) == (False, 409), refused
    assert refused["reason"] == (
        "'file_a.pdf' was in 'Docs', which is also in the trash. Restore 'Docs' first, then restore 'file_a.pdf'."
    )
    assert refused["parentId"] == world.ids["folder"]
    file_a = await world.stored("file_a")
    assert (file_a["isDeleted"], file_a["deleteBatchId"]) == (True, batch), "the file came back under a trashed folder"
    assert world.reindexed() == set()


async def test_a_folder_trashed_after_the_check_keeps_its_file_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The KB restore reads the folder before its write, which used to trust that read."""
    await world.trash("file_a")
    batch = (await world.stored("file_a"))["deleteBatchId"]
    world.producer.events.clear()

    refused = await _restore_while_the_folder_goes_to_the_trash(
        world, monkeypatch, world.processor, "restore_trashed_records"
    )

    await _assert_refused_for_the_folder(world, refused, batch)


# Neo4j only, and not collected for Arango at all: the graph jobs fail on any skip. Arango
# runs this write in the caller's stream transaction, whose snapshot is taken when the
# transaction begins, so a folder trashed inside that transaction is not seen there.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_folder_trashed_just_before_the_write_keeps_its_file_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await world.trash("file_a")
    batch = (await world.stored("file_a"))["deleteBatchId"]
    world.producer.events.clear()

    refused = await _restore_while_the_folder_goes_to_the_trash(world, monkeypatch, world.graph, "restore_records")

    await _assert_refused_for_the_folder(world, refused, batch)


async def test_restore_records_checks_the_parent_only_when_asked(world: _World) -> None:
    await world.trash("file_a")
    await world.trash("folder")
    batch = (await world.stored("file_a"))["deleteBatchId"]

    assert await world.graph.restore_records([{"id": world.ids["file_a"]}], batch, require_live_parent=True) == []
    assert (await world.stored("file_a"))["isDeleted"] is True

    # The connector's restore asks for nothing, and brings the file back as before.
    restored = await world.processor.restore_trashed_records(
        world.kb_id, batch, [_restore_item(world, "file_a")], restore_source=DeleteSource.CONNECTOR,
    )
    assert restored == [world.ids["file_a"]]
    assert await world.live(("folder", "file_a")) == {"file_a"}


async def test_a_sync_restore_does_not_wait_for_the_parent(world: _World) -> None:
    """The connector restore keeps its behaviour: only the KB restore checks the parent."""
    world.ids["drive_folder"] = f"drive_folder-{uuid.uuid4().hex[:12]}"
    await world.graph.batch_upsert_records([_file(world, "drive_folder", kb=False, folder=True)])
    await world.graph.batch_create_edges(
        [_edge(world.ids["drive_folder"], CollectionNames.RECORDS.value, world.ids["drive_file"],
               CollectionNames.RECORDS.value, relationshipType="PARENT_CHILD")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )
    await world.processor.on_record_deleted(world.ids["drive_file"])
    await world.processor.on_record_deleted(world.ids["drive_folder"])
    assert (await world.stored("drive_folder"))["isDeleted"] is True

    seen_again = _file(world, "drive_file", kb=False, external_revision_id="rev-1")
    seen_again.id = str(uuid.uuid4())
    await world.processor.on_new_records([(seen_again, [])])

    assert (await world.stored("drive_file"))["isDeleted"] is False
    assert (await world.stored("drive_folder"))["isDeleted"] is True


async def test_a_file_whose_name_was_taken_comes_back_renamed(world: _World) -> None:
    await world.trash("report")
    world.ids["new_report"] = f"new-report-{uuid.uuid4().hex[:12]}"
    await world.graph.batch_upsert_records([_file(world, "new_report", record_name="report.pdf")])
    await _link_to_kb(world, ("new_report",))

    result = await world.restore("report")

    assert result["success"] is True, result
    assert result["restoredRecords"] == [
        {"recordId": world.ids["report"], "name": "report (restored).pdf", "renamedFrom": "report.pdf"}
    ]
    assert (await world.stored("report"))["recordName"] == "report (restored).pdf"
    typed = await world.graph.get_file_record_by_id(world.ids["report"])
    assert typed is not None and typed.record_name == "report (restored).pdf" and typed.is_deleted is False
    assert (await world.stored("new_report"))["recordName"] == "report.pdf"
    assert world.ids["report"] in world.reindexed()


async def _give_up_external_id(world: _World, name: str, external_id: str) -> None:
    """As a move onto its id does: the record keeps the id in trashedExternalRecordId."""
    await world.graph.update_node(
        world.ids[name], CollectionNames.RECORDS.value,
        {"externalRecordId": f"{TRASHED_EXTERNAL_ID_PREFIX}{world.ids[name]}",
         "trashedExternalRecordId": external_id},
    )


async def test_the_external_id_comes_back_only_when_it_is_free(world: _World) -> None:
    original = f"ext-{world.ids['solo']}"
    await world.trash("solo")
    await _give_up_external_id(world, "solo", original)
    await world.graph.update_node(world.ids["report"], CollectionNames.RECORDS.value, {"externalRecordId": original})

    refused = await world.restore("solo")
    assert refused["code"] == 409, refused
    assert refused["reason"] == (
        "'solo.pdf' can't be restored because 'report.pdf' has taken its place. That usually means the same "
        "item was added again after this one was deleted. To restore this one, delete 'report.pdf' first, "
        "then try again."
    )
    solo = await world.stored("solo")
    assert (solo["isDeleted"], solo["externalRecordId"]) == (True, f"{TRASHED_EXTERNAL_ID_PREFIX}{world.ids['solo']}")
    assert world.reindexed() == set()

    await world.trash("report")
    result = await world.restore("solo")
    assert result["success"] is True, result
    solo = await world.stored("solo")
    assert (solo["isDeleted"], solo["externalRecordId"], solo.get("trashedExternalRecordId")) == (False, original, None)
    holder = await world.graph.get_record_by_external_id(world.kb_id, original, visibility=RecordVisibility.ALL)
    assert holder is not None and holder.id == world.ids["solo"]
    report = await world.stored("report")
    assert (report["isDeleted"], report["externalRecordId"], report["trashedExternalRecordId"]) == (
        True, f"{TRASHED_EXTERNAL_ID_PREFIX}{world.ids['report']}", original,
    )

    # And the other way round, the record that gave it up now waits.
    assert (await world.restore("report"))["code"] == 409


async def test_a_sync_restores_an_item_the_connector_deleted(world: _World) -> None:
    await world.processor.on_record_deleted(world.ids["drive_file"])
    assert (await world.stored("drive_file"))["deleteSource"] == DeleteSource.CONNECTOR.value
    world.producer.events.clear()

    seen_again = _file(world, "drive_file", kb=False, external_revision_id="rev-1")
    minted = seen_again.id = str(uuid.uuid4())
    await world.processor.on_new_records([(seen_again, [])])

    doc = await world.stored("drive_file")
    assert doc["isDeleted"] is False and doc.get("deleteBatchId") is None
    assert await world.graph.get_document(minted, CollectionNames.RECORDS.value) is None
    assert [e["payload"]["recordId"] for e in world.producer.of_type(EventTypes.NEW_RECORD.value)] == [
        world.ids["drive_file"]
    ]


async def _sync_sees_drive_file_again(world: _World, **extra: object) -> None:
    seen_again = _file(world, "drive_file", kb=False, external_revision_id="rev-1", **extra)
    seen_again.id = str(uuid.uuid4())
    await world.processor.on_new_records([(seen_again, [])])


@pytest.mark.parametrize(
    ("stored", "after", "published"),
    [
        (ProgressStatus.COMPLETED.value, ProgressStatus.QUEUED.value, True),
        (ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.AUTO_INDEX_OFF.value, False),
    ],
    ids=["indexed", "manual-only-never-indexed"],
)
async def test_a_sync_restore_ends_on_the_indexing_status_it_always_did(
    world: _World, stored: str, after: str, published: bool,
) -> None:
    await world.graph.update_node(world.ids["drive_file"], CollectionNames.RECORDS.value, {"indexingStatus": stored})
    await world.processor.on_record_deleted(world.ids["drive_file"])
    world.producer.events.clear()

    await _sync_sees_drive_file_again(world, indexing_status=stored)

    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["indexingStatus"]) == (False, after)
    assert bool(world.producer.of_type(EventTypes.NEW_RECORD.value)) is published


# Neo4j only: Arango runs the sync in one stream transaction, so the failure rolls the
# restore back with it, and the graph jobs fail on any skip.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_sync_restore_that_fails_after_its_write_leaves_the_item_for_the_stranded_sweep(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore's write commits on its own; it used to leave the item live and COMPLETED with no vectors."""
    await world.processor.on_record_deleted(world.ids["drive_file"])
    world.producer.events.clear()
    original = world.processor._handle_parent_record

    async def parent_lookup_fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("parent lookup failed")

    monkeypatch.setattr(world.processor, "_handle_parent_record", parent_lookup_fails)
    with pytest.raises(RuntimeError, match="parent lookup failed"):
        await _sync_sees_drive_file_again(world)
    monkeypatch.setattr(world.processor, "_handle_parent_record", original)

    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["indexingStatus"]) == (False, ProgressStatus.NOT_STARTED.value)
    assert doc["queuedAtTimestamp"] > 0
    assert world.producer.events == []
    # The read the stranded sweep pages through, by status, finds it.
    waiting = await world.graph.get_documents_paginated(
        CollectionNames.RECORDS.value, limit=1000,
        filters={"indexingStatus": ProgressStatus.NOT_STARTED.value}, raise_on_error=True,
    )
    assert world.ids["drive_file"] in {d.get("_key") or d.get("id") for d in waiting}

    # The sync's next attempt sees a live record and sends it for indexing.
    await _sync_sees_drive_file_again(world, indexing_status=ProgressStatus.QUEUED.value)
    assert [e["payload"]["recordId"] for e in world.producer.of_type(EventTypes.NEW_RECORD.value)] == [
        world.ids["drive_file"]
    ]


class _SweepProducer:
    """What the stranded sweep sends, kept for the test."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        self.sent.append((event_type, payload))
        return True


async def _run_stranded_sweep_an_hour_later(monkeypatch: pytest.MonkeyPatch, graph: IGraphDBProvider) -> list:
    """The indexing service's own sweep, with its clock moved past the republish threshold."""
    monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")
    later = get_epoch_timestamp_in_ms() + 2 * 3600 * 1000
    monkeypatch.setattr(indexing_main, "get_epoch_timestamp_in_ms", lambda: later)
    producer = _SweepProducer()

    async def run_coordination(coro: object) -> object:
        return await coro

    await indexing_main._republish_stranded_records(
        graph_provider=graph, logger=logger, producer=producer,
        run_coordination=run_coordination, concurrency_manager=None, page_size=500,
    )
    return producer.sent


# Neo4j only, for the same reason as the test above.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_the_stranded_sweep_republishes_a_restore_that_failed_after_its_write(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indexing left md5Checksum and virtualRecordId on the record, which the sweep
    took for a duplicate parked behind a twin, so it never sent the record again."""
    await world.graph.update_node(
        world.ids["drive_file"], CollectionNames.RECORDS.value, {"md5Checksum": "md5-indexed-before"}
    )
    await world.processor.on_record_deleted(world.ids["drive_file"])

    async def parent_lookup_fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("parent lookup failed")

    monkeypatch.setattr(world.processor, "_handle_parent_record", parent_lookup_fails)
    with pytest.raises(RuntimeError, match="parent lookup failed"):
        await _sync_sees_drive_file_again(world)

    sent = await _run_stranded_sweep_an_hour_later(monkeypatch, world.graph)

    assert [(event, payload["virtualRecordId"]) for event, payload in sent
            if payload["recordId"] == world.ids["drive_file"]] == [
        (EventTypes.REINDEX_RECORD.value, f"vr-{world.ids['drive_file']}")
    ]
    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["indexingStatus"]) == (False, ProgressStatus.NOT_STARTED.value)
    assert doc.get("md5Checksum") is None
    assert doc["virtualRecordId"] == f"vr-{world.ids['drive_file']}", "citations still resolve"


async def test_the_stranded_sweep_republishes_a_restore_whose_index_event_was_lost(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync finishes but the broker never acks the index event. The upsert used to
    write the source's checksum back, so the sweep again took the row for a parked duplicate."""
    await world.graph.update_node(
        world.ids["drive_file"], CollectionNames.RECORDS.value, {"md5Checksum": "md5-indexed-before"}
    )
    await world.processor.on_record_deleted(world.ids["drive_file"])

    async def never_acked(_topic: str, messages: list) -> list[bool]:
        return [False] * len(messages)

    monkeypatch.setattr(world.producer, "send_messages", never_acked)
    await _sync_sees_drive_file_again(world, md5_hash="md5-from-source")

    sent = await _run_stranded_sweep_an_hour_later(monkeypatch, world.graph)

    assert [event for event, payload in sent if payload["recordId"] == world.ids["drive_file"]] == [
        EventTypes.REINDEX_RECORD.value
    ]
    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["indexingStatus"]) == (False, ProgressStatus.NOT_STARTED.value)
    assert doc.get("md5Checksum") is None
    assert doc["virtualRecordId"] == f"vr-{world.ids['drive_file']}"


async def test_restore_records_changes_only_its_own_batch(world: _World) -> None:
    await world.trash("solo")
    batch = (await world.stored("solo"))["deleteBatchId"]
    assert await world.graph.restore_records([{"id": world.ids["solo"]}], "another-batch") == []
    assert await world.graph.restore_records([{"id": world.ids["report"]}], batch) == []
    assert (await world.stored("solo"))["isDeleted"] is True

    assert await world.graph.restore_records([{"id": world.ids["solo"]}], batch) == [world.ids["solo"]]
    assert await world.graph.restore_records([{"id": world.ids["solo"]}], batch) == []


async def _add_sibling_written_before_soft_delete(world: _World, name: str, **extra: object) -> None:
    """A live record stored without ``isDeleted``, as everything written before soft delete is."""
    world.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await world.graph.batch_upsert_records([_file(world, name, **extra)])
    await _link_to_kb(world, (name,))
    if isinstance(world.graph, Neo4jProvider):
        await world.graph.client.execute_query(
            "MATCH (r:Record {id: $id}) REMOVE r.isDeleted", parameters={"id": world.ids[name]}
        )
    else:
        await world.graph.http_client.execute_aql(
            "UPDATE @key WITH { isDeleted: null } IN records OPTIONS { keepNull: false }",
            {"key": world.ids[name]},
        )
    assert "isDeleted" not in await world.stored(name)


async def test_a_file_is_renamed_past_a_sibling_stored_without_is_deleted(world: _World) -> None:
    await world.trash("report")
    await _add_sibling_written_before_soft_delete(world, "old_report", record_name="report.pdf")

    result = await world.restore("report")

    assert result["success"] is True, result
    assert (await world.stored("report"))["recordName"] == "report (restored).pdf"
    assert (await world.stored("old_report"))["recordName"] == "report.pdf"


async def test_a_root_folder_is_renamed_past_a_sibling_stored_without_is_deleted(world: _World) -> None:
    await world.trash("folder")
    await _add_sibling_written_before_soft_delete(world, "old_docs", folder=True)

    result = await world.restore("folder")

    assert result["success"] is True, result
    assert (await world.stored("folder"))["recordName"] == "Docs (restored)"
    assert (await world.stored("old_docs"))["recordName"] == "Docs"


@pytest.mark.parametrize("kind", ["file", "folder"])
async def test_a_failed_name_lookup_fails_the_restore_and_keeps_the_record_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """Read as "no clash", a failed lookup would bring the record back under a taken name."""
    name = "report" if kind == "file" else "folder"
    await world.trash(name)
    world.producer.events.clear()
    neo4j = isinstance(world.graph, Neo4jProvider)
    owner = world.graph.client if neo4j else world.graph
    marker = "name_lower" if kind == "file" else ("$folder_name" if neo4j else "@name_variants")
    original = owner.execute_query
    failed = False

    async def fail_the_name_lookup(query, *args, **kwargs) -> object:
        nonlocal failed
        if marker in query:
            failed = True
            raise RuntimeError("graph unavailable")
        return await original(query, *args, **kwargs)

    monkeypatch.setattr(owner, "execute_query", fail_the_name_lookup)
    result = await world.restore(name)
    monkeypatch.setattr(owner, "execute_query", original)

    assert failed, "the restore never looked the name up"
    assert result["success"] is False and result["code"] == 500, result
    assert (await world.stored(name))["isDeleted"] is True
    assert world.reindexed() == set()


async def test_restore_records_brings_back_all_of_its_items_or_none(world: _World) -> None:
    subtree = ("folder", *FOLDER_FILES)
    await world.trash("folder")
    await world.trash("solo")
    batch = (await world.stored("folder"))["deleteBatchId"]
    ids = [world.ids[n] for n in subtree]

    assert await world.graph.restore_records([{"id": i} for i in [*ids, world.ids["solo"]]], batch) == []
    assert await world.live((*subtree, "solo")) == set()

    assert sorted(await world.graph.restore_records([{"id": i} for i in ids], batch)) == sorted(ids)


# Neo4j only, and not collected for Arango at all: the graph jobs fail on any skip,
# and Arango's restore runs inside the caller's stream transaction.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_restore_the_graph_refuses_partway_leaves_the_whole_batch_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each Neo4j statement commits on its own, so a restore split over statements kept what came before a refusal."""
    # A restore used to run in chunks, each its own auto-commit; one key per chunk makes
    # any record restored before the refused one visible here.
    monkeypatch.setattr(neo4j_provider_module, "SOFT_DELETE_CHUNK", 1, raising=False)
    subtree = ("folder", *FOLDER_FILES)
    await world.trash("folder")
    batch = (await world.stored("folder"))["deleteBatchId"]
    world.producer.events.clear()
    client = world.graph.client
    await client.execute_query(
        "CREATE CONSTRAINT restore_it_poison IF NOT EXISTS "
        "FOR (n:RestoreItPoison) REQUIRE n.updatedAtTimestamp IS UNIQUE"
    )
    try:
        # Restore stamps one update time on every record it brings back, so of two records
        # that may not share one, whichever comes back second is refused.
        poisoned = [world.ids["file_a"], world.ids["attachment"]]
        await client.execute_query(
            "UNWIND range(0, size($ids) - 1) AS i MATCH (r:Record {id: $ids[i]}) "
            "SET r.updatedAtTimestamp = -1 - i, r:RestoreItPoison",
            parameters={"ids": poisoned},
        )
        result = await world.restore("folder")
    finally:
        await client.execute_query("DROP CONSTRAINT restore_it_poison IF EXISTS")

    assert result["success"] is False and result["code"] == 500, result
    restored = {n for n in subtree if (await world.stored(n)).get("isDeleted") is not True}
    assert restored == set(), f"a refused restore brought part of the folder back: {restored}"
    assert {n: (await world.stored(n)).get("deleteBatchId") for n in subtree} == dict.fromkeys(subtree, batch)
    assert world.reindexed() == set()


@contextlib.asynccontextmanager
async def _refuse_restoring_two_records(world: _World, names: tuple[str, str]) -> AsyncIterator[None]:
    """Make the graph refuse any write that stamps one update time on both records, as restore does."""
    client = world.graph.client
    await client.execute_query(
        "CREATE CONSTRAINT restore_it_poison IF NOT EXISTS "
        "FOR (n:RestoreItPoison) REQUIRE n.updatedAtTimestamp IS UNIQUE"
    )
    try:
        await client.execute_query(
            "UNWIND range(0, size($ids) - 1) AS i MATCH (r:Record {id: $ids[i]}) "
            "SET r.updatedAtTimestamp = -1 - i, r:RestoreItPoison",
            parameters={"ids": [world.ids[n] for n in names]},
        )
        yield
    finally:
        await client.execute_query("DROP CONSTRAINT restore_it_poison IF EXISTS")


async def _a_trashed_record_holds_file_as_old_id(world: _World) -> tuple[str, str]:
    """file_a is in the trash with its id given up, and solo, also in the trash, holds that id now."""
    old_id = f"ext-{world.ids['file_a']}"
    await world.trash("folder")
    await _give_up_external_id(world, "file_a", old_id)
    await world.graph.update_node(world.ids["solo"], CollectionNames.RECORDS.value, {"externalRecordId": old_id})
    await world.trash("solo")
    return old_id, (await world.stored("folder"))["deleteBatchId"]


def _restore_item(world: _World, name: str, gave_up: str | None = None) -> dict:
    return {"id": world.ids[name], "name": f"{name}.pdf", "trashedExternalRecordId": gave_up, "set": {}}


async def _assert_nothing_moved(world: _World, old_id: str) -> None:
    solo = await world.stored("solo")
    assert (solo["isDeleted"], solo["externalRecordId"], solo.get("trashedExternalRecordId")) == (
        True, old_id, None,
    ), "a refused restore took the external id away from the other record in the trash"
    file_a = await world.stored("file_a")
    assert (file_a["isDeleted"], file_a["externalRecordId"], file_a["trashedExternalRecordId"]) == (
        True, f"{TRASHED_EXTERNAL_ID_PREFIX}{world.ids['file_a']}", old_id,
    )
    assert await world.live(FOLDER_FILES) == set()


async def test_a_restore_refused_by_a_later_item_leaves_the_other_records_external_id_alone(
    world: _World,
) -> None:
    """On Neo4j each statement commits on its own, so an id given up before the refusal stayed given up."""
    old_id, batch = await _a_trashed_record_holds_file_as_old_id(world)
    file_b_id = f"ext-{world.ids['file_b']}"
    await _give_up_external_id(world, "file_b", file_b_id)
    await world.graph.update_node(world.ids["report"], CollectionNames.RECORDS.value, {"externalRecordId": file_b_id})

    with pytest.raises(processor_module.RestoreRefused) as refused:
        await world.processor.restore_trashed_records(
            world.kb_id, batch,
            [_restore_item(world, "file_a", old_id), _restore_item(world, "file_b", file_b_id)],
        )

    assert refused.value.code == 409
    assert refused.value.reason == (
        "'file_b.pdf' can't be restored because 'report.pdf' has taken its place. That usually means the same "
        "item was added again after this one was deleted. To restore this one, delete 'report.pdf' first, "
        "then try again."
    )
    assert refused.value.details["conflicting_record_id"] == world.ids["report"]
    await _assert_nothing_moved(world, old_id)


# Neo4j only, as the uniqueness constraint that refuses the write is Neo4j's.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_restore_the_graph_refuses_leaves_the_other_records_external_id_alone(world: _World) -> None:
    old_id, batch = await _a_trashed_record_holds_file_as_old_id(world)

    async with _refuse_restoring_two_records(world, ("file_a", "file_b")):
        with pytest.raises(Exception, match="RestoreItPoison|restore_it_poison|already exists"):
            await world.processor.restore_trashed_records(
                world.kb_id, batch, [_restore_item(world, "file_a", old_id), _restore_item(world, "file_b")],
            )

    await _assert_nothing_moved(world, old_id)


async def test_a_restore_takes_its_external_id_back_from_a_record_in_the_trash(world: _World) -> None:
    old_id, batch = await _a_trashed_record_holds_file_as_old_id(world)
    file_b_id = f"ext-{world.ids['file_b']}"
    await _give_up_external_id(world, "file_b", file_b_id)

    restored = await world.processor.restore_trashed_records(
        world.kb_id, batch,
        [_restore_item(world, "file_a", old_id), _restore_item(world, "file_b", file_b_id)],
    )

    assert sorted(restored) == sorted([world.ids["file_a"], world.ids["file_b"]])
    for name, external_id in (("file_a", old_id), ("file_b", file_b_id)):
        doc = await world.stored(name)
        assert (doc["isDeleted"], doc["externalRecordId"], doc.get("trashedExternalRecordId")) == (
            False, external_id, None,
        ), name
    solo = await world.stored("solo")
    assert (solo["isDeleted"], solo["externalRecordId"], solo["trashedExternalRecordId"]) == (
        True, f"{TRASHED_EXTERNAL_ID_PREFIX}{world.ids['solo']}", old_id,
    )
    holder = await world.graph.get_record_by_external_id(world.kb_id, old_id, visibility=RecordVisibility.ALL)
    assert holder is not None and holder.id == world.ids["file_a"]
