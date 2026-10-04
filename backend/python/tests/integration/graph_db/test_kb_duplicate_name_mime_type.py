"""Knowledge-base duplicate-name checks read a file's type from its record.

A file counts as a duplicate when another file in the same folder has the same
name and the same type (MIME type). Files uploaded through the shared processor
are stored with the type on the record only; the file node has none. The checks
read it from the file node, so for those files the rename, move and folder
upload checks never found a duplicate. These tests upload files the way the
product does today and check both backends. On Neo4j the rename checks also
searched the collection root for a file inside a folder, because the parent
lookup returned the folder's recordType instead of "record".

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
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

ARANGO_DB = "kb_duplicate_name_mime_it"
TEXT = "text/plain"
PDF = "application/pdf"
PNG = "image/png"

logger = logging.getLogger("kb-duplicate-name-mime-it")


@dataclass
class _Kb:
    graph: IGraphDBProvider
    service: KnowledgeBaseService
    kb_id: str
    org_id: str
    user_id: str
    folder: str
    records: list[str] = field(default_factory=list)

    async def upload(self, name: str, mime_type: str, folder: str | None = None) -> dict:
        """Upload one file with the payload shape the Node API sends."""
        key = str(uuid.uuid4())
        now = get_epoch_timestamp_in_ms()
        record = {
            "_key": key, "orgId": self.org_id, "recordName": name, "externalRecordId": key,
            "recordType": "FILE", "origin": "UPLOAD", "connectorId": self.kb_id,
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
            "sourceCreatedAtTimestamp": now, "sourceLastModifiedTimestamp": now,
            "isDeleted": False, "isArchived": False, "indexingStatus": "QUEUED", "version": 1,
            "webUrl": f"/record/{key}", "mimeType": mime_type, "sizeInBytes": 10,
        }
        file_record = {
            "_key": key, "orgId": self.org_id, "name": name, "isFile": True, "extension": None,
            "mimeType": mime_type, "sizeInBytes": 10, "webUrl": f"/record/{key}",
        }
        files = [{"record": record, "fileRecord": file_record, "filePath": name, "lastModified": now}]
        self.records.append(key)
        if folder is None:
            result = await self.service.upload_records_to_kb(self.kb_id, self.user_id, self.org_id, files)
        else:
            result = await self.service.upload_records_to_folder(
                self.kb_id, folder, self.user_id, self.org_id, files
            )
        assert result.get("success") is True, result
        return {"id": key, **result}


async def _remove(graph: IGraphDBProvider, ids: dict[str, list[str]]) -> None:
    for collection, keys in ids.items():
        if keys:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges(keys, collection)


@pytest.fixture(params=["neo4j", "arango"])
async def kb(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Kb]:
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

        run = uuid.uuid4().hex[:10]
        org_id, user_id, user_key = f"org-kbmime-{run}", f"user-kbmime-{run}", f"ukey-kbmime-{run}"
        records: list[str] = []
        apps: list[str] = []
        cleanup.push_async_callback(_remove, graph, {
            CollectionNames.ORGS.value: [org_id], CollectionNames.USERS.value: [user_key],
            CollectionNames.APPS.value: apps, CollectionNames.RECORDS.value: records,
            CollectionNames.FILES.value: records,
        })
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb mime"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": user_key, "userId": user_id, "orgId": org_id, "email": f"{run}@example.com",
              "isActive": True}],
            CollectionNames.USERS.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)

        async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
            return processor

        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=processor_for_kb)
        created = await service.create_knowledge_base(user_id=user_id, org_id=org_id, name=f"kb {run}")
        assert created and created.get("success") is not False, created
        apps.append(created["id"])
        folder = await service.create_folder_in_kb(created["id"], "drafts", user_id, org_id)
        assert folder and folder.get("success") is not False, folder
        records.append(folder["id"])

        yield _Kb(graph, service, created["id"], org_id, user_id, folder["id"], records)


async def _assert_type_only_on_record(kb: _Kb, record_id: str, mime_type: str) -> None:
    record = await kb.graph.get_document(record_id, CollectionNames.RECORDS.value)
    file_node = await kb.graph.get_document(record_id, CollectionNames.FILES.value)
    assert record["mimeType"] == mime_type, record
    assert file_node is not None and file_node.get("mimeType") is None, (
        f"an uploaded file was meant to carry its type on the record only: {file_node}"
    )


async def test_a_rename_onto_a_same_type_name_is_refused(kb: _Kb) -> None:
    report = (await kb.upload("report", TEXT))["id"]
    notes = (await kb.upload("notes", TEXT))["id"]
    await _assert_type_only_on_record(kb, report, TEXT)

    result = await kb.service.update_record(
        user_id=kb.user_id, record_id=notes, updates={"recordName": "report"},
    )

    assert result["success"] is False and result["code"] == 409, result
    kept = await kb.graph.get_document(notes, CollectionNames.RECORDS.value)
    assert kept["recordName"] == "notes", kept


async def test_a_move_next_to_a_same_type_name_is_refused(kb: _Kb) -> None:
    await kb.upload("report", TEXT)
    in_folder = (await kb.upload("report", TEXT, folder=kb.folder))["id"]
    await _assert_type_only_on_record(kb, in_folder, TEXT)

    result = await kb.service.move_record(
        kb_id=kb.kb_id, record_id=in_folder, new_parent_id=None, user_id=kb.user_id,
    )

    assert result["success"] is False and result["code"] == 409, result
    parent = await kb.graph.get_record_parent_info(in_folder)
    assert parent and parent.get("id") == kb.folder, f"the refused move still moved the file: {parent}"


async def test_an_upload_into_a_folder_skips_a_same_type_name(kb: _Kb) -> None:
    await kb.upload("report", TEXT, folder=kb.folder)

    result = await kb.upload("report", TEXT, folder=kb.folder)

    assert result["totalCreated"] == 0, result
    assert [s["reason"] for s in result["skippedFiles"]] == ["DUPLICATE_NAME"], result


async def test_the_same_name_with_a_different_type_is_still_allowed(kb: _Kb) -> None:
    # Guards the rule from the other side, so it passes on the old code too.
    await kb.upload("report", TEXT)
    chart = (await kb.upload("chart", PDF))["id"]
    picture = (await kb.upload("report", PNG, folder=kb.folder))["id"]

    renamed = await kb.service.update_record(
        user_id=kb.user_id, record_id=chart, updates={"recordName": "report"},
    )
    uploaded = await kb.upload("report", TEXT, folder=kb.folder)
    moved = await kb.service.move_record(
        kb_id=kb.kb_id, record_id=picture, new_parent_id=None, user_id=kb.user_id,
    )

    assert renamed.get("success") is not False, renamed
    assert uploaded["totalCreated"] == 1, uploaded
    assert moved.get("success") is not False, moved


async def test_a_file_in_a_folder_reports_the_folder_as_a_record_parent(kb: _Kb) -> None:
    # The rename checks read type == "record" as "the parent is a folder". Neo4j
    # returned the folder's recordType (FILE), so renames searched the root.
    in_folder = (await kb.upload("report", TEXT, folder=kb.folder))["id"]
    at_root = (await kb.upload("notes", TEXT))["id"]

    assert await kb.graph.get_record_parent_info(in_folder) == {"id": kb.folder, "type": "record"}
    assert await kb.graph.get_record_parent_info(at_root) is None


async def test_a_rename_inside_a_folder_onto_a_same_type_sibling_is_refused(kb: _Kb) -> None:
    await kb.upload("report", TEXT, folder=kb.folder)
    notes = (await kb.upload("notes", TEXT, folder=kb.folder))["id"]

    result = await kb.service.update_record(
        user_id=kb.user_id, record_id=notes, updates={"recordName": "report"},
    )

    assert result["success"] is False and result["code"] == 409, result
    kept = await kb.graph.get_document(notes, CollectionNames.RECORDS.value)
    assert kept["recordName"] == "notes", kept


async def test_a_rename_inside_a_folder_onto_a_root_file_name_is_allowed(kb: _Kb) -> None:
    await kb.upload("report", TEXT)
    notes = (await kb.upload("notes", TEXT, folder=kb.folder))["id"]

    result = await kb.service.update_record(
        user_id=kb.user_id, record_id=notes, updates={"recordName": "report"},
    )

    assert result.get("success") is not False, result
    renamed = await kb.graph.get_document(notes, CollectionNames.RECORDS.value)
    assert renamed["recordName"] == "report", renamed


async def test_a_subfolder_rename_onto_a_sibling_folder_name_is_refused(kb: _Kb) -> None:
    # Same parent lookup as the file rename, so the same Neo4j gap applied.
    for name in ("2025", "2026"):
        created = await kb.service.create_nested_folder(kb.kb_id, kb.folder, name, kb.user_id, kb.org_id)
        assert created and created.get("success") is not False, created
        kb.records.append(created["id"])

    result = await kb.service.updateFolder(kb.records[-1], kb.kb_id, kb.user_id, "2025")

    assert result["success"] is False and result["code"] == 409, result


async def test_a_root_upload_skips_a_legacy_file_typed_only_on_its_file_node(kb: _Kb) -> None:
    # Older files carry the type on the file node and none on the record.
    legacy = (await kb.upload("report", TEXT))["id"]
    assert await kb.graph.update_node(legacy, CollectionNames.RECORDS.value, {"mimeType": None})
    assert await kb.graph.update_node(legacy, CollectionNames.FILES.value, {"mimeType": TEXT})
    record = await kb.graph.get_document(legacy, CollectionNames.RECORDS.value)
    assert record.get("mimeType") is None, record

    result = await kb.upload("report", TEXT)

    assert result["totalCreated"] == 0, result
    assert [s["reason"] for s in result["skippedFiles"]] == ["DUPLICATE_NAME"], result
