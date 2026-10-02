"""The soft-delete write path against a real Neo4j and a real ArangoDB.

Drives the production code, ``DataSourceEntitiesProcessor`` over a real
``GraphDataStore``, with ``ENABLE_SOFT_DELETE`` switched on, and checks the graph
afterwards. A KB folder holds two files, one of them with an attachment, and a
file that was already in the trash. A second file sits outside the folder.

- A folder delete marks the folder and everything under it with one batch id,
  who deleted it and when, and keeps every node, edge and type doc.
- The file already in the trash keeps its own batch.
- Only ``softDeleteRecords`` is published, never ``deleteRecord``.
- An attachments-only cascade and a single-record connector delete mark only
  what they reach.
- A sync of an item the user deleted leaves it in the trash.
- A UI/API delete by the KB owner goes to the trash as a USER delete, and
  takes exactly what that backend's hard delete removes: a folder alone, a
  mail with its direct attachments on Arango and alone on Neo4j.
- An Outlook sync delete (by external id) goes to the trash as a CONNECTOR
  delete and takes exactly what that backend's hard delete removes: the
  message with its direct attachments on Arango, the message alone on Neo4j.
- A move onto an external id a trashed record holds keeps the trash entry: it
  gives the id up (kept in ``trashedExternalRecordId``) and no ``deleteRecord``
  is published.
- With the flag off, the same cascade still removes the records.

Arango enforces the records schema strictly, so its run also proves the write
path only sets declared fields.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_soft_delete_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from app.models.entities import FileRecord, MailRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "soft_delete_it"

logger = logging.getLogger("soft-delete-it")

KB_NAMES = ("folder", "file_a", "file_b", "attachment", "old_trash", "outside")
DRIVE_NAMES = ("drive_file", "drive_child")
# A mail with a direct attachment, an attachment of that attachment, and a PARENT_CHILD child.
MAIL_NAMES = ("mail", "mail_attachment", "mail_attachment_attachment", "mail_child")
OUTLOOK_NAMES = ("outlook_mail", "outlook_attachment", "outlook_attachment_attachment")


class _Producer:
    """Records every event the processor sends; always acks."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        self.events.append(message)
        return True

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        self.events.extend(m for _key, m in messages)
        return [True] * len(messages)

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("eventType") == event_type]


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    producer: _Producer
    org_id: str
    user_id: str
    user_key: str
    kb_id: str
    connector_id: str
    mail_connector_id: str
    ids: dict[str, str] = field(default_factory=dict)

    async def stored(self, name: str) -> dict | None:
        return await self.graph.get_document(self.ids[name], CollectionNames.RECORDS.value)

    async def children(self, name: str) -> set[str]:
        edges = await self.graph.get_edges_from_node(
            f"{CollectionNames.RECORDS.value}/{self.ids[name]}", CollectionNames.RECORD_RELATIONS.value
        )
        return {(e.get("_to") or e.get("to_id") or "").split("/")[-1] for e in edges}


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    return provider


async def _connect_arango() -> IGraphDBProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    await provider.ensure_schema()
    return provider


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.user_key, w.kb_id, w.connector_id, w.mail_connector_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value, CollectionNames.MAILS.value,
                       CollectionNames.USERS.value, CollectionNames.APPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.RECORD_RELATIONS.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
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
        suffix = uuid.uuid4().hex[:10]
        producer = _Producer()
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = producer
        w = _World(
            graph=graph, processor=processor, producer=producer,
            org_id=f"org-soft-{suffix}", user_id=f"user-soft-{suffix}", user_key=f"ukey-soft-{suffix}",
            kb_id=f"kb-soft-{suffix}", connector_id=f"drive-soft-{suffix}", mail_connector_id=f"gmail-soft-{suffix}",
        )
        processor.org_id = w.org_id
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


def _file(w: _World, name: str, *, kb: bool, folder: bool = False, **extra: object) -> FileRecord:
    fields: dict = {
        "id": w.ids[name],
        "org_id": w.org_id,
        "record_name": f"{name}.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": f"ext-{w.ids[name]}",
        "version": 1,
        "origin": OriginTypes.UPLOAD if kb else OriginTypes.CONNECTOR,
        "connector_name": Connectors.KNOWLEDGE_BASE if kb else Connectors.GOOGLE_DRIVE,
        "connector_id": w.kb_id if kb else w.connector_id,
        "mime_type": "application/vnd.folder" if folder else "application/pdf",
        "indexing_status": ProgressStatus.COMPLETED.value,
        "is_file": not folder,
    }
    return FileRecord(**{**fields, **extra})


def _mail_file(w: _World, name: str, connector: Connectors = Connectors.GOOGLE_MAIL) -> FileRecord:
    return _file(w, name, kb=False, connector_name=connector, connector_id=w.mail_connector_id)


def _outlook_message(w: _World) -> MailRecord:
    return MailRecord(
        id=w.ids["outlook_mail"], org_id=w.org_id, record_name="Quarterly numbers",
        record_type=RecordType.MAIL, external_record_id=f"ext-{w.ids['outlook_mail']}", version=1,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.OUTLOOK, connector_id=w.mail_connector_id,
        mime_type="text/html", indexing_status=ProgressStatus.COMPLETED.value, subject="Quarterly numbers",
    )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in (*KB_NAMES, *DRIVE_NAMES, *MAIL_NAMES, *OUTLOOK_NAMES):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Trash Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": w.kb_id, "name": "Collection", "type": "KB", "appGroup": "Local Storage", "scope": "personal",
          "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now},
         {"id": w.connector_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
          "scope": "team", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    await g.batch_upsert_records([
        _file(w, "folder", kb=True, folder=True),
        _file(w, "file_a", kb=True),
        _file(w, "file_b", kb=True),
        _file(w, "attachment", kb=True),
        _file(w, "old_trash", kb=True, is_deleted=True, deleted_at=now - 5000,
              delete_source="USER", delete_batch_id="old-batch"),
        _file(w, "outside", kb=True),
        _file(w, "drive_file", kb=False),
        _file(w, "drive_child", kb=False),
        *(_mail_file(w, n) for n in MAIL_NAMES),
        _outlook_message(w),
        *(_mail_file(w, n, Connectors.OUTLOOK) for n in OUTLOOK_NAMES[1:]),
    ])
    for name in ("file_a", "file_b", "attachment", "outside", "drive_file", "drive_child", *OUTLOOK_NAMES):
        await g.update_node(w.ids[name], CollectionNames.RECORDS.value, {"virtualRecordId": f"vr-{w.ids[name]}"})

    def edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
        return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    records, apps, users = CollectionNames.RECORDS.value, CollectionNames.APPS.value, CollectionNames.USERS.value
    await g.batch_create_edges(
        [edge(w.user_key, users, w.kb_id, apps, role="OWNER", type="USER"),
         edge(w.user_key, users, w.ids["mail"], records, role="OWNER", type="USER"),
         edge(w.user_key, users, w.ids["outlook_mail"], records, role="OWNER", type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [edge(w.ids[n], records, w.kb_id, apps, entityType="KB") for n in KB_NAMES],
        collection=CollectionNames.BELONGS_TO.value,
    )
    await g.batch_create_edges(
        [edge(w.ids["folder"], records, w.ids[c], records, relationshipType="PARENT_CHILD")
         for c in ("file_a", "file_b", "old_trash")]
        + [edge(w.ids["file_b"], records, w.ids["attachment"], records, relationshipType="ATTACHMENT"),
           edge(w.ids["drive_file"], records, w.ids["drive_child"], records, relationshipType="PARENT_CHILD"),
           edge(w.ids["mail"], records, w.ids["mail_attachment"], records, relationshipType="ATTACHMENT"),
           edge(w.ids["mail_attachment"], records, w.ids["mail_attachment_attachment"], records,
                relationshipType="ATTACHMENT"),
           edge(w.ids["mail"], records, w.ids["mail_child"], records, relationshipType="PARENT_CHILD"),
           edge(w.ids["outlook_mail"], records, w.ids["outlook_attachment"], records, relationshipType="ATTACHMENT"),
           edge(w.ids["outlook_attachment"], records, w.ids["outlook_attachment_attachment"], records,
                relationshipType="ATTACHMENT")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=on))


# ---------------------------------------------------------------------------


async def test_a_folder_delete_moves_its_subtree_to_the_trash_as_one_batch(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    started = get_epoch_timestamp_in_ms()
    result = await world.processor.on_records_deleted_cascade(
        [world.ids["folder"]], world.kb_id, delete_source=DeleteSource.USER, deleted_by_user_id=world.user_key,
    )
    assert result["success"] is True and result["successfully_deleted"] == 1

    subtree = ("folder", "file_a", "file_b", "attachment")
    docs = {n: await world.stored(n) for n in subtree}
    batches = {d["deleteBatchId"] for d in docs.values()}
    assert len(batches) == 1 and None not in batches
    for name, doc in docs.items():
        assert doc["isDeleted"] is True, name
        assert doc["deleteSource"] == "USER", name
        assert doc["deletedByUserId"] == world.user_key, name
        assert doc["deletedAtTimestamp"] >= started, name

    assert (await world.stored("old_trash"))["deleteBatchId"] == "old-batch"
    assert (await world.stored("outside")).get("isDeleted") is not True

    # Structure and type docs survive, so the batch can be restored as it was.
    assert {world.ids[c] for c in ("file_a", "file_b", "old_trash")} <= await world.children("folder")
    assert world.ids["attachment"] in await world.children("file_b")
    typed = await world.graph.get_file_record_by_id(world.ids["file_a"])
    assert typed is not None and typed.is_deleted is True

    assert world.producer.of_type(EventTypes.DELETE_RECORD.value) == []
    (event,) = world.producer.of_type(EventTypes.SOFT_DELETE_RECORDS.value)
    assert set(event["payload"]["virtualRecordIds"]) == {
        f"vr-{world.ids[n]}" for n in ("file_a", "file_b", "attachment")
    }
    assert event["payload"]["batchId"] == batches.pop()


async def test_an_attachments_only_cascade_leaves_children_alone(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    await world.processor.on_records_deleted_cascade([world.ids["folder"]], world.kb_id, cascade_children=False)
    assert (await world.stored("folder"))["isDeleted"] is True
    for name in ("file_a", "file_b", "attachment"):
        assert (await world.stored(name)).get("isDeleted") is not True, name

    await world.processor.on_records_deleted_cascade([world.ids["file_b"]], world.kb_id, cascade_children=False)
    assert (await world.stored("file_b"))["isDeleted"] is True
    assert (await world.stored("attachment"))["isDeleted"] is True


async def test_a_connector_delete_marks_only_the_record(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    await world.processor.on_record_deleted(world.ids["drive_file"])
    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["deleteSource"], doc.get("deletedByUserId")) == (True, "CONNECTOR", None)
    assert (await world.stored("drive_child")).get("isDeleted") is not True
    assert world.ids["drive_child"] in await world.children("drive_file")


async def test_a_sync_leaves_a_user_deleted_item_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    await world.processor.on_records_deleted_cascade(
        [world.ids["drive_file"]], world.connector_id, delete_source=DeleteSource.USER,
        deleted_by_user_id=world.user_key,
    )
    world.producer.events.clear()

    seen_again = _file(world, "drive_file", kb=False)
    seen_again.id = str(uuid.uuid4())
    seen_again.record_name = "renamed at the source.pdf"
    seen_again.external_revision_id = "rev-2"
    await world.processor.on_new_records([(seen_again, [])])

    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["deleteSource"], doc["recordName"]) == (True, "USER", "drive_file.pdf")
    assert await world.graph.get_document(seen_again.id, CollectionNames.RECORDS.value) is None
    assert world.producer.of_type(EventTypes.NEW_RECORD.value) == []


async def test_an_api_delete_by_the_owner_goes_to_the_trash(world: _World) -> None:
    result = await world.graph.delete_record(world.ids["outside"], world.user_id, world.org_id, soft_delete=True)
    assert result["success"] is True and result["softDeleted"] is True
    assert result["virtualRecordIds"] == [f"vr-{world.ids['outside']}"]
    doc = await world.stored("outside")
    assert (doc["isDeleted"], doc["deleteSource"], doc["deletedByUserId"]) == (True, "USER", world.user_key)

    again = await world.graph.delete_record(world.ids["outside"], world.user_id, world.org_id, soft_delete=True)
    assert again["success"] is False and again["code"] == 404


async def _trash_drive_file_with_revision(world: _World, revision: str) -> None:
    await world.graph.update_node(
        world.ids["drive_file"], CollectionNames.RECORDS.value, {"externalRevisionId": revision}
    )
    await world.processor.on_records_deleted_cascade(
        [world.ids["drive_file"]], world.connector_id, delete_source=DeleteSource.USER,
        deleted_by_user_id=world.user_key,
    )
    world.producer.events.clear()


async def test_rename_detection_does_not_match_a_trashed_record(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Object stores find a rename by revision; after a hard delete there would be nothing to find."""
    _flag(monkeypatch, True)
    await _trash_drive_file_with_revision(world, "rev-1")
    assert await world.processor.get_record_by_external_revision_id(world.connector_id, "rev-1") is None


async def test_an_upsert_reusing_a_trashed_records_id_leaves_it_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, True)
    await _trash_drive_file_with_revision(world, "rev-1")

    renamed = _file(world, "drive_file", kb=False)
    renamed.external_record_id = f"renamed-{world.ids['drive_file']}"
    renamed.record_name = "renamed.pdf"
    renamed.external_revision_id = "rev-1"
    await world.processor.on_new_records([(renamed, [])])

    doc = await world.stored("drive_file")
    assert (doc["isDeleted"], doc["externalRecordId"], doc["recordName"]) == (
        True, f"ext-{world.ids['drive_file']}", "drive_file.pdf",
    )
    assert world.producer.of_type(EventTypes.NEW_RECORD.value) == []


async def test_a_move_onto_an_id_held_in_the_trash_keeps_the_trash_entry(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitLab, GitHub, network share and KB renames move records here; the trashed
    holder of the target id gives the id up instead of being retired."""
    _flag(monkeypatch, True)
    monkeypatch.setattr(world.processor, "_get_storage_cleanup", lambda: None)
    await world.processor.on_record_deleted(world.ids["drive_file"])
    world.producer.events.clear()
    target = f"ext-{world.ids['drive_file']}"

    moved = _file(world, "drive_child", kb=False)
    moved.id = str(uuid.uuid4())
    moved.external_record_id = target
    moved.record_name = "moved.pdf"
    await world.processor.on_records_moved([(f"ext-{world.ids['drive_child']}", moved, [])])

    trashed = await world.stored("drive_file")
    assert trashed is not None
    assert (trashed["isDeleted"], trashed["deleteSource"], trashed["virtualRecordId"]) == (
        True, "CONNECTOR", f"vr-{world.ids['drive_file']}",
    )
    assert (trashed["externalRecordId"], trashed["trashedExternalRecordId"]) == (
        f"trashed:{world.ids['drive_file']}", target,
    )
    assert (await world.stored("drive_child"))["externalRecordId"] == target
    holder = await world.graph.get_record_by_external_id(world.connector_id, target, visibility=RecordVisibility.ALL)
    assert holder is not None and holder.id == world.ids["drive_child"]
    assert await world.graph.get_record_by_external_id(
        world.connector_id, target, visibility=RecordVisibility.DELETED
    ) is None
    assert world.producer.of_type(EventTypes.DELETE_RECORD.value) == []


async def _visible(w: _World, names: tuple[str, ...]) -> set[str]:
    return {n for n in names if (doc := await w.stored(n)) is not None and doc.get("isDeleted") is not True}


@pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
async def test_an_api_folder_delete_takes_the_folder_alone(world: _World, soft: bool) -> None:
    """The record DELETE route's hard path removes this vertex only, so the trash takes it only."""
    names = ("folder", "file_a", "file_b", "attachment", "outside")
    before = await _visible(world, names)
    result = await world.graph.delete_record(world.ids["folder"], world.user_id, world.org_id, soft_delete=soft)
    assert result["success"] is True, result
    assert before - await _visible(world, names) == {"folder"}


@pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
async def test_an_api_mail_delete_takes_what_the_hard_delete_takes(world: _World, soft: bool) -> None:
    """Arango's mail delete also removes the direct attachments; Neo4j's removes the mail alone."""
    expected = {"mail", "mail_attachment"} if isinstance(world.graph, ArangoHTTPProvider) else {"mail"}
    before = await _visible(world, MAIL_NAMES)
    result = await world.graph.delete_record(world.ids["mail"], world.user_id, world.org_id, soft_delete=soft)
    assert result["success"] is True, result
    assert before - await _visible(world, MAIL_NAMES) == expected


@pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
async def test_an_outlook_sync_delete_takes_what_the_hard_delete_takes(
    world: _World, monkeypatch: pytest.MonkeyPatch, soft: bool,
) -> None:
    """Outlook deletes by external id; Arango's hard path also removes direct attachments, Neo4j's does not."""
    _flag(monkeypatch, soft)
    expected = (
        {"outlook_mail", "outlook_attachment"} if isinstance(world.graph, ArangoHTTPProvider) else {"outlook_mail"}
    )
    before = await _visible(world, OUTLOOK_NAMES)
    await world.processor.delete_record_by_external_id(
        world.mail_connector_id, f"ext-{world.ids['outlook_mail']}", world.user_id,
    )
    assert before - await _visible(world, OUTLOOK_NAMES) == expected

    if not soft:
        assert world.producer.of_type(EventTypes.SOFT_DELETE_RECORDS.value) == []
        return
    docs = [await world.stored(n) for n in expected]
    assert {(d["deleteSource"], d.get("deletedByUserId")) for d in docs} == {("CONNECTOR", None)}
    assert len({d["deleteBatchId"] for d in docs}) == 1
    (event,) = world.producer.of_type(EventTypes.SOFT_DELETE_RECORDS.value)
    assert set(event["payload"]["virtualRecordIds"]) == {f"vr-{world.ids[n]}" for n in expected}
    assert world.producer.of_type(EventTypes.DELETE_RECORD.value) == []

    # The removal arriving again finds nothing live to act on.
    await world.processor.delete_record_by_external_id(
        world.mail_connector_id, f"ext-{world.ids['outlook_mail']}", world.user_id,
    )
    assert len(world.producer.of_type(EventTypes.SOFT_DELETE_RECORDS.value)) == 1


async def test_with_the_flag_off_a_cascade_still_removes_the_records(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, False)
    await world.processor.on_records_deleted_cascade([world.ids["file_b"]], world.kb_id)
    assert await world.stored("file_b") is None
    assert await world.stored("attachment") is None
    assert world.producer.of_type(EventTypes.SOFT_DELETE_RECORDS.value) == []
