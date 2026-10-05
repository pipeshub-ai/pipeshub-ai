"""Search vectors through the trash, against a real Qdrant and a real Neo4j or ArangoDB.

The indexing service's own cleanup (``IndexingPipeline.bulk_delete_embeddings``,
with the arguments its ``softDeleteRecords`` and ``deleteRecord`` handlers pass)
runs over a real Qdrant collection made by the real ``CollectionRegistry``, and
asks a real graph which records still use each virtual record. Records go to
the trash, come back and are purged through the production write path, restore
service and purge.

- A file in the trash loses its points, so search cannot match it, but its
  stored content stays: a restore indexes it again from what is kept.
- A twin (same content, so the same virtual record) keeps its points and its
  stored content through the other copy's trash and purge.
- Once every copy is in the trash the points go, and a new upload of the same
  content finds no twin to copy a finished status from.
- A purge removes what the trash kept: the stored content of a virtual record no
  record uses any more.

The blob store is a fake that records which virtual records' content it was
asked to remove; the KV store, lease and broker are those of ``test_trash_purge_e2e``.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml \
    -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait qdrant-vector-it neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_soft_delete_vectors_e2e.py -m integration

Environment: QDRANT_IT_HOST, QDRANT_IT_PORT (REST), NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
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
from app.connectors.services.trash_purge import Outcome, TrashPurger
from app.connectors.sources.localKB.handlers import kb_service as kb_service_module
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import FileRecord, RecordType
from app.modules.indexing import run as run_module
from app.modules.indexing.run import IndexingPipeline
from app.modules.indexing.stored_content_cleanup import StoredContentCleanup
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.vector_db import membership as membership_module
from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.membership import remaining_record_keys
from app.services.vector_db.models import CollectionConfig, VectorPoint
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.test_soft_delete_e2e import _connect_arango, _connect_neo4j
from tests.integration.test_trash_purge_e2e import _KV, DAY_MS, _Broker, _Lease
from tests.support.vector_db import make_config_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("soft-delete-vectors-it")

QDRANT_HOST = os.environ.get("QDRANT_IT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_IT_PORT", "6343"))
DIM = 8
RECORDS = CollectionNames.RECORDS.value
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
CHUNKS = 3


class _BlobStore:
    """Removes a virtual record's processed content, as the real store does, and remembers which."""

    def __init__(self) -> None:
        self.purged: list[str] = []

    async def purge_virtual_record_documents(self, org_id: str, virtual_record_id: str) -> int:
        self.purged.append(virtual_record_id)
        return 1

    async def purge_documents(self, org_id: str, document_ids: list[str]) -> list[str]:
        return []


@dataclass
class _World:
    graph: IGraphDBProvider
    qdrant: QdrantService
    pipeline: IndexingPipeline
    collection: str
    processor: DataSourceEntitiesProcessor
    service: KnowledgeBaseService
    broker: _Broker
    blobs: _BlobStore
    org_id: str
    user_id: str
    user_key: str
    kb_id: str
    ids: dict[str, str] = field(default_factory=dict)
    vrids: dict[str, str] = field(default_factory=dict)
    now: int = field(default_factory=get_epoch_timestamp_in_ms)

    async def points(self, name: str) -> int:
        page = await self.qdrant.scroll(
            self.collection,
            await self.qdrant.filter_collection(must={"virtualRecordId": self.vrids[name]}),
            limit=100,
        )
        return len(page.points)

    async def owners(self, name: str) -> set[tuple[str, ...]]:
        page = await self.qdrant.scroll(
            self.collection,
            await self.qdrant.filter_collection(must={"virtualRecordId": self.vrids[name]}),
            limit=100,
        )
        return {tuple(sorted(p.payload.get(CONNECTOR_IDS_FIELD) or [])) for p in page.points}

    async def mapping_kept(self, name: str) -> bool:
        return await self.graph.get_document(self.vrids[name], MAPPING) is not None

    async def trash(self, *names: str) -> None:
        """A user's delete, then the indexing service's handling of the event it sends."""
        before = len(self.broker.of_type(EventTypes.SOFT_DELETE_RECORDS.value))
        result = await self.processor.on_records_deleted_cascade(
            [self.ids[n] for n in names], self.kb_id,
            delete_source=DeleteSource.USER, deleted_by_user_id=self.user_key,
        )
        assert result["success"] is True and result["softDeleted"] is True, result
        for event in self.broker.of_type(EventTypes.SOFT_DELETE_RECORDS.value)[before:]:
            outcome = await self.pipeline.bulk_delete_embeddings(event["payload"]["virtualRecordIds"], keep_mapping=True)
            assert outcome["success"] is True, outcome

    async def purge(self) -> None:
        """A purge after the retention, then the indexing service's handling of its deleteRecord events."""
        before = len(self.broker.of_type(EventTypes.DELETE_RECORD.value))
        clock = self.now + 15 * DAY_MS
        purger = TrashPurger(logger, self.graph, _KV({"featureFlags": {"ENABLE_SOFT_DELETE": True}}),
                             self.broker, _Lease(), clock=lambda: clock, sleep=AsyncMock())
        assert await purger.tick() == Outcome.FINISHED
        for event in self.broker.of_type(EventTypes.DELETE_RECORD.value)[before:]:
            payload = event["payload"]
            outcome = await self.pipeline.bulk_delete_embeddings(
                [payload["virtualRecordId"]], org_id=payload.get("orgId") or None
            )
            assert outcome["success"] is True, outcome


def _upload(w: _World, name: str, md5: str) -> FileRecord:
    return FileRecord(
        id=w.ids[name], org_id=w.org_id, record_name=f"{name}.pdf", record_type=RecordType.FILE,
        external_record_id=uuid.uuid4().hex[:24], version=1, origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE, connector_id=w.kb_id, mime_type="application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value, is_file=True, extension="pdf", md5_hash=md5,
        size_in_bytes=2048,
    )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Vector Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": w.kb_id, "name": "Collection", "type": "KB", "appGroup": "Local Storage", "scope": "personal",
          "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    md5 = {"report": "md5-report-" + w.kb_id, "report_copy": "md5-report-" + w.kb_id, "memo": "md5-memo-" + w.kb_id}
    for name in md5:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    # Same content, one virtual record: the copy shares the report's.
    w.vrids = {"report": f"vr-report-{w.kb_id}", "report_copy": f"vr-report-{w.kb_id}", "memo": f"vr-memo-{w.kb_id}"}
    await g.batch_upsert_records([_upload(w, n, m) for n, m in md5.items()])
    for name in md5:
        await g.update_node(w.ids[name], RECORDS, {"virtualRecordId": w.vrids[name]})
    await g.batch_create_edges(
        [{"from_id": w.ids[n], "from_collection": RECORDS, "to_id": w.kb_id,
          "to_collection": CollectionNames.APPS.value, "entityType": "KB",
          "createdAtTimestamp": now, "updatedAtTimestamp": now} for n in md5]
        + [],
        collection=CollectionNames.BELONGS_TO.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.user_key, "from_collection": CollectionNames.USERS.value, "to_id": w.kb_id,
          "to_collection": CollectionNames.APPS.value, "role": "OWNER", "type": "USER",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_upsert_nodes(
        [{"id": vrid, "orgId": w.org_id, "documentId": uuid.uuid4().hex[:24], "updatedAt": now}
         for vrid in set(w.vrids.values())],
        collection=MAPPING,
    )
    points = [
        VectorPoint(
            id=str(uuid.uuid4()),
            dense_vector=[float((i + j) % 5) + 1.0 for j in range(DIM)],
            payload={"page_content": f"chunk {i} of {vrid}",
                     "metadata": {"orgId": w.org_id, "virtualRecordId": vrid},
                     CONNECTOR_IDS_FIELD: [w.kb_id]},
        )
        for vrid in set(w.vrids.values()) for i in range(CHUNKS)
    ]
    await w.qdrant.upsert_points(w.collection, points)


async def _remove(w: _World) -> None:
    graph = w.graph
    ids = [*w.ids.values(), w.user_key, w.kb_id, *set(w.vrids.values())]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OPTIONAL MATCH (n)-[:IS_OF_TYPE]->(t) DETACH DELETE n, t",
            parameters={"ids": ids},
        )
    else:
        for collection in (RECORDS, CollectionNames.FILES.value, CollectionNames.USERS.value,
                           CollectionNames.APPS.value, MAPPING):
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
    await w.qdrant.delete_points(
        w.collection, await w.qdrant.filter_collection(should={"virtualRecordId": sorted(set(w.vrids.values()))})
    )


async def _connect_qdrant() -> QdrantService:
    service = QdrantService(QdrantConfig(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30))
    await service.connect()
    await service.get_collections()
    return service


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
    monkeypatch.setenv("SOFT_DELETE_PURGE_INTERVAL_SECONDS", "0")
    monkeypatch.delenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", raising=False)
    # The deletes re-read the graph after a pause meant for lagging followers; one server here.
    monkeypatch.setattr(run_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    monkeypatch.setattr(membership_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            qdrant = await _connect_qdrant()
        except Exception as exc:
            if os.environ.get("QDRANT_IT_HOST") or os.environ.get("QDRANT_IT_PORT"):
                pytest.fail(f"Qdrant is configured (QDRANT_IT_HOST/PORT) but not reachable: {exc!r}")
            pytest.skip(f"Qdrant not configured (QDRANT_IT_HOST unset) and not reachable locally: {exc!r}")
        cleanup.push_async_callback(qdrant.disconnect)
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
            # The indexes ensure_schema creates on a real install: restore reads batches
            # by one, and the purge waits until its walk index is online.
            for statement in graph._generate_performance_indexes():
                await graph.client.execute_query(statement)
            await graph.client.execute_query("CALL db.awaitIndexes(300)")
        flag = AsyncMock(return_value=True)
        for module in (processor_module, kb_service_module):
            monkeypatch.setattr(module, "is_soft_delete_enabled", flag)
        monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())

        registry = CollectionRegistry(
            vector_db_service=qdrant,
            strategy=SingleCollectionStrategy(),
            collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
            manifest_store=CollectionManifestStore(make_config_service(), MagicMock()),
            logger=logger,
        )
        suffix = uuid.uuid4().hex[:10]
        org_id = f"org-vec-{suffix}"
        collection = await registry.ensure_collection(RecordContext(org_id=org_id), embedding_size=DIM)
        blobs = _BlobStore()
        pipeline = IndexingPipeline(
            logger=logger, config_service=make_config_service(), graph_provider=graph,
            collection_registry=registry, vector_db_service=qdrant,
            stored_content=StoredContentCleanup(logger, graph, blobs),
        )
        broker = _Broker()
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = broker
        processor.org_id = org_id
        monkeypatch.setattr(processor, "_get_storage_cleanup", lambda: None)
        service = KnowledgeBaseService(
            logger, graph, AsyncMock(), processor_for_kb=AsyncMock(return_value=processor),
            config_service=MagicMock(),
        )
        w = _World(
            graph=graph, qdrant=qdrant, pipeline=pipeline, collection=collection, processor=processor,
            service=service, broker=broker, blobs=blobs, org_id=org_id, user_id=f"user-vec-{suffix}",
            user_key=f"ukey-vec-{suffix}", kb_id=f"kb-vec-{suffix}",
        )
        monkeypatch.setattr(TrashPurger, "_org_ids", AsyncMock(return_value=[org_id]))
        cleanup.push_async_callback(_remove, w)
        await _seed(w)
        yield w


# ---------------------------------------------------------------------------


async def test_the_trash_takes_the_points_and_keeps_what_a_restore_needs(world: _World) -> None:
    assert await world.points("memo") == CHUNKS

    await world.trash("memo")

    assert await world.points("memo") == 0, "search can no longer match it"
    assert await world.mapping_kept("memo"), "the stored content stays for a restore"
    assert world.blobs.purged == []
    assert await world.points("report") == CHUNKS, "another record's points are untouched"

    restored = await world.service.restore_record(world.ids["memo"], world.user_id, world.org_id)

    assert restored["success"] is True, restored
    reindex = [e["payload"] for e in world.broker.of_type(EventTypes.REINDEX_RECORD.value)]
    assert [p["recordId"] for p in reindex] == [world.ids["memo"]]
    # Indexing reads the record's virtual record id from the graph when the event has none.
    assert (await world.graph.get_document(world.ids["memo"], RECORDS))["virtualRecordId"] == world.vrids["memo"]
    assert await world.mapping_kept("memo"), "re-indexed from the content the trash kept"
    live = await world.graph.get_records_by_virtual_record_id(world.vrids["memo"], raise_on_error=True)
    assert remaining_record_keys(live) == [world.ids["memo"]], "indexing finds it live again"


async def test_a_twin_keeps_its_points_and_content_through_the_other_copys_trash_and_purge(world: _World) -> None:
    await world.trash("report")

    assert await world.points("report_copy") == CHUNKS
    assert await world.owners("report_copy") == {(world.kb_id,)}
    assert await world.mapping_kept("report_copy")

    await world.purge()

    assert await world.graph.get_document(world.ids["report"], RECORDS) is None
    assert await world.points("report_copy") == CHUNKS, "the live copy is still searchable"
    assert await world.mapping_kept("report_copy") and world.blobs.purged == [], "and its content stays"


async def test_with_every_copy_in_the_trash_the_points_go_and_a_new_upload_finds_no_twin(world: _World) -> None:
    await world.trash("report", "report_copy")

    assert await world.points("report") == 0
    assert await world.mapping_kept("report"), "kept for the purge, or for a restore"
    md5 = (await world.graph.get_document(world.ids["report"], RECORDS))["md5Checksum"]
    assert await world.graph.find_duplicate_records("a-new-upload", md5, world.org_id) == [], (
        "a new upload must index itself, not copy COMPLETED from a copy with no vectors"
    )

    await world.purge()

    for name in ("report", "report_copy"):
        assert await world.graph.get_document(world.ids[name], RECORDS) is None, name
    # Each copy's deleteRecord asks; the second finds nothing left, which is harmless.
    assert set(world.blobs.purged) == {world.vrids["report"]}, "the purge releases the content no record uses"
    assert not await world.mapping_kept("report")
    assert await world.points("memo") == CHUNKS
