"""Knowledge-base duplicate-name checks see live files stored without ``isDeleted``.

Older file records were written before ``isDeleted`` was set on every record, so
some live records have no such property. In Cypher, ``r.isDeleted <> true`` is
null for those, and WHERE drops them, so Neo4j's name checks did not see the
file at all: a rename or a move could put a second file with the same name next
to it, and an upload did not skip the duplicate. ArangoDB's ``!= true`` is true
for a missing field, so the same cases run there to keep both backends in step.

Both file nodes carry ``mimeType`` as older file documents do, since the checks
match on name and mime type together.

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
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import FileRecord, RecordType
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

ARANGO_DB = "kb_duplicate_name_checks_it"
FOLDER_MIME = "application/vnd.folder"
FILE_MIME = "text/plain"

logger = logging.getLogger("kb-duplicate-name-checks-it")


@dataclass
class _Kb:
    graph: IGraphDBProvider
    service: KnowledgeBaseService
    kb_id: str
    user_id: str
    legacy_file: str
    folder: str
    file_in_folder: str
    legacy_file_in_folder: str
    other_root_file: str


def _record(kb_id: str, org_id: str, name: str, *, folder: bool = False) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    return FileRecord(
        org_id=org_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=f"{name}-{uuid.uuid4().hex[:8]}",
        version=0,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id=kb_id,
        mime_type=FOLDER_MIME if folder else FILE_MIME,
        is_file=not folder,
        created_at=now,
        updated_at=now,
        source_created_at=now,
        source_updated_at=now,
    )


async def _store(graph: IGraphDBProvider, kb_id: str, record: FileRecord, *, legacy: bool = False) -> str:
    node = record.to_arango_base_record()
    if legacy:
        del node["isDeleted"]
    file_node = record.to_arango_record()
    if record.is_file:
        file_node["mimeType"] = record.mime_type
    assert await graph.batch_upsert_nodes([node], CollectionNames.RECORDS.value)
    assert await graph.batch_upsert_nodes([file_node], CollectionNames.FILES.value)
    now = get_epoch_timestamp_in_ms()
    edge = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    assert await graph.batch_create_edges(
        [{**edge, "from_id": record.id, "from_collection": CollectionNames.RECORDS.value,
          "to_id": record.id, "to_collection": CollectionNames.FILES.value}],
        collection=CollectionNames.IS_OF_TYPE.value,
    )
    assert await graph.batch_create_edges(
        [{**edge, "from_id": record.id, "from_collection": CollectionNames.RECORDS.value,
          "to_id": kb_id, "to_collection": CollectionNames.APPS.value, "entityType": "KB"}],
        collection=CollectionNames.BELONGS_TO.value,
    )
    return record.id


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
        org_id, user_id, user_key = f"org-kbnames-{run}", f"user-kbnames-{run}", f"ukey-kbnames-{run}"
        seeded: dict[str, list[str]] = {
            CollectionNames.ORGS.value: [org_id], CollectionNames.USERS.value: [user_key],
            CollectionNames.APPS.value: [], CollectionNames.RECORDS.value: [], CollectionNames.FILES.value: [],
        }
        cleanup.push_async_callback(_remove, graph, seeded)
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb names"}],
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
        kb_id = created["id"]
        seeded[CollectionNames.APPS.value].append(kb_id)

        legacy_file = await _store(graph, kb_id, _record(kb_id, org_id, "report.txt"), legacy=True)
        folder = await _store(graph, kb_id, _record(kb_id, org_id, "drafts", folder=True))
        file_in_folder = await _store(graph, kb_id, _record(kb_id, org_id, "Report.txt"))
        legacy_file_in_folder = await _store(graph, kb_id, _record(kb_id, org_id, "Notes.txt"), legacy=True)
        other_root_file = await _store(graph, kb_id, _record(kb_id, org_id, "notes.txt"))
        ids = [legacy_file, folder, file_in_folder, legacy_file_in_folder, other_root_file]
        seeded[CollectionNames.RECORDS.value] += ids
        seeded[CollectionNames.FILES.value] += ids
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_create_edges(
            [{"from_id": folder, "from_collection": CollectionNames.RECORDS.value,
              "to_id": child, "to_collection": CollectionNames.RECORDS.value,
              "relationshipType": "PARENT_CHILD", "createdAtTimestamp": now, "updatedAtTimestamp": now}
             for child in (file_in_folder, legacy_file_in_folder)],
            collection=CollectionNames.RECORD_RELATIONS.value,
        )

        for legacy in (legacy_file, legacy_file_in_folder):
            stored = await graph.get_document(legacy, CollectionNames.RECORDS.value)
            assert stored is not None and "isDeleted" not in stored, (
                f"the legacy file was meant to be stored with no isDeleted property: {stored}"
            )
        yield _Kb(
            graph, service, kb_id, user_id, legacy_file, folder, file_in_folder, legacy_file_in_folder,
            other_root_file,
        )


async def test_the_name_lookup_finds_a_live_file_with_no_deleted_flag(kb: _Kb) -> None:
    found = await kb.graph.find_file_by_name_in_parent(
        kb_id=kb.kb_id, file_name="REPORT.txt", mime_type=FILE_MIME, parent_folder_id=None,
    )

    assert found is not None and found["_key"] == kb.legacy_file, found


async def test_a_rename_onto_its_name_is_refused(kb: _Kb) -> None:
    result = await kb.service.update_record(
        user_id=kb.user_id, record_id=kb.other_root_file, updates={"recordName": "report.txt"},
    )

    assert result["success"] is False and result["code"] == 409, result
    renamed = await kb.graph.get_document(kb.other_root_file, CollectionNames.RECORDS.value)
    assert renamed["recordName"] == "notes.txt", renamed


async def test_a_rename_onto_its_name_inside_a_folder_is_refused(kb: _Kb) -> None:
    result = await kb.service.update_record(
        user_id=kb.user_id, record_id=kb.file_in_folder, updates={"recordName": "notes.txt"},
    )

    assert result["success"] is False and result["code"] == 409, result
    renamed = await kb.graph.get_document(kb.file_in_folder, CollectionNames.RECORDS.value)
    assert renamed["recordName"] == "Report.txt", renamed


async def test_a_move_into_its_folder_is_refused(kb: _Kb) -> None:
    result = await kb.service.move_record(
        kb_id=kb.kb_id, record_id=kb.other_root_file, new_parent_id=kb.folder, user_id=kb.user_id,
    )

    assert result["success"] is False and result["code"] == 409, result
    parent = await kb.graph.get_record_parent_info(kb.other_root_file)
    assert not parent, f"the refused move still moved the file: {parent}"


async def test_a_move_next_to_it_is_refused(kb: _Kb) -> None:
    result = await kb.service.move_record(
        kb_id=kb.kb_id, record_id=kb.file_in_folder, new_parent_id=None, user_id=kb.user_id,
    )

    assert result["success"] is False and result["code"] == 409, result
    parent = await kb.graph.get_record_parent_info(kb.file_in_folder)
    assert parent and parent.get("id") == kb.folder, f"the refused move still moved the file: {parent}"


async def test_an_upload_counts_it_as_an_existing_name(kb: _Kb) -> None:
    names = await kb.graph._fetch_existing_file_names_in_parent(kb_id=kb.kb_id, parent_folder_id=None)

    assert ("report.txt", FILE_MIME) in names, names
