"""A replayed reindex of an indexed record leaves its vectors alone: a real Qdrant and a real Neo4j or ArangoDB.

``RecordEventHandler`` over a real graph and a real ``IndexingPipeline`` on a real Qdrant collection. The record
is COMPLETED and has points; a non-forced ``reindexRecord`` for it (the replay of a delivery whose acknowledgement
was lost) is acknowledged. Before the decision moved ahead of the reconciliation the handler first asked the
vector store to delete or rewrite the record's points: wasted I/O on every replay, and a window in which a failure
there left the event unacknowledged. The vector store is not asked anything now.

A forced reindex of the same record still reconciles, so the guard does not turn a real reindex into a no-op.

  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml \
    -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait qdrant-vector-it neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_replay_guard_e2e.py -m integration

Environment: QDRANT_IT_HOST, QDRANT_IT_PORT (REST), NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.modules.indexing import run as run_module
from app.modules.indexing.run import IndexingPipeline
from app.services.messaging.config import IndexingEvent
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.vector_db import membership as membership_module
from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.models import CollectionConfig, VectorPoint
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.vector_db import make_config_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("replay-guard-it")

QDRANT_HOST = os.environ.get("QDRANT_IT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_IT_PORT", "6343"))
DIM = 8
CHUNKS = 3
RECORDS = CollectionNames.RECORDS.value
APPS = CollectionNames.APPS.value
#: A type that is not reconciled block by block, so a reindex clears the record's embeddings first.
CSV = {"mimeType": "text/csv", "extension": "csv"}


class _World:
    def __init__(self, graph: IGraphDBProvider, qdrant: QdrantService, collection: str, pipeline: IndexingPipeline,
                 org: str, connector: str, record: str, vrid: str) -> None:
        self.graph, self.qdrant, self.collection, self.pipeline = graph, qdrant, collection, pipeline
        self.org, self.connector, self.record, self.vrid = org, connector, record, vrid

    async def points(self) -> int:
        page = await self.qdrant.scroll(
            self.collection, await self.qdrant.filter_collection(must={"virtualRecordId": self.vrid}), limit=100
        )
        return len(page.points)

    def handler(self) -> RecordEventHandler:
        event_processor = MagicMock()
        event_processor.graph_provider = self.graph
        event_processor.processor.indexing_pipeline = self.pipeline
        event_processor.sink_orchestrator = None
        event_processor.stage_ingress = None
        return RecordEventHandler(
            logger=logger, config_service=AsyncMock(), event_processor=event_processor, producer=AsyncMock()
        )

    def payload(self, **extra: object) -> dict[str, object]:
        return {
            "recordId": self.record, "virtualRecordId": self.vrid, "orgId": self.org, "connectorId": self.connector,
            "origin": "CONNECTOR", **CSV, **extra,
        }


async def _connect_qdrant() -> QdrantService:
    service = QdrantService(QdrantConfig(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False, timeout=30))
    await service.connect()
    await service.get_collections()
    return service


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    monkeypatch.setattr(run_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    monkeypatch.setattr(membership_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            qdrant = await _connect_qdrant()
        except Exception as exc:
            if os.environ.get("QDRANT_IT_HOST") or os.environ.get("QDRANT_IT_PORT"):
                pytest.fail(f"Qdrant is configured (QDRANT_IT_HOST/PORT) but not reachable: {exc!r}")
            pytest.skip(f"Qdrant not configured and not reachable locally: {exc!r}")
        cleanup.push_async_callback(qdrant.disconnect)
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "replay_guard_it")
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(graph.disconnect)

        registry = CollectionRegistry(
            vector_db_service=qdrant, strategy=SingleCollectionStrategy(),
            collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
            manifest_store=CollectionManifestStore(make_config_service(), MagicMock()), logger=logger,
        )
        suffix = uuid.uuid4().hex[:10]
        org = f"org-rg-{suffix}"
        collection = await registry.ensure_collection(RecordContext(org_id=org), embedding_size=DIM)
        pipeline = IndexingPipeline(
            logger=logger, config_service=make_config_service(), graph_provider=graph,
            collection_registry=registry, vector_db_service=qdrant,
        )
        w = _World(graph, qdrant, collection, pipeline, org, f"conn-rg-{suffix}", f"rec-rg-{suffix}", f"vr-rg-{suffix}")
        await graph.batch_upsert_nodes(
            [{"id": w.connector, "orgId": org, "name": "c", "type": "SLACK", "appGroup": "Slack", "scope": "team",
              "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}], APPS,
        )
        await graph.batch_upsert_nodes(
            [{"id": w.record, "orgId": org, "recordName": "data.csv", "externalRecordId": w.record,
              "recordType": "FILE", "origin": "CONNECTOR", "connectorName": "SLACK", "connectorId": w.connector,
              "virtualRecordId": w.vrid, "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
              "indexingStatus": ProgressStatus.COMPLETED.value, "extractionStatus": ProgressStatus.COMPLETED.value,
              "mimeType": CSV["mimeType"]}], RECORDS,
        )
        await qdrant.upsert_points(collection, [
            VectorPoint(
                id=str(uuid.uuid4()), dense_vector=[float((i + j) % 5) + 1.0 for j in range(DIM)],
                payload={"page_content": f"chunk {i}", "metadata": {"orgId": org, "virtualRecordId": w.vrid},
                         CONNECTOR_IDS_FIELD: [w.connector]},
            )
            for i in range(CHUNKS)
        ])
        try:
            yield w
        finally:
            await qdrant.delete_points(
                collection, await qdrant.filter_collection(must={"orgId": org, "virtualRecordId": [w.vrid]})
            )
            await graph.delete_nodes([w.record], RECORDS)
            await graph.delete_nodes([w.connector], APPS)


async def test_replayed_reindex_leaves_points_untouched(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await world.points() == CHUNKS
    set_payload = AsyncMock(wraps=world.qdrant.set_payload)
    delete_points = AsyncMock(wraps=world.qdrant.delete_points)
    monkeypatch.setattr(world.qdrant, "set_payload", set_payload)
    monkeypatch.setattr(world.qdrant, "delete_points", delete_points)

    events = [e.event async for e in world.handler().process_event(EventTypes.REINDEX_RECORD.value, world.payload())]

    assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert await world.points() == CHUNKS
    assert set_payload.await_count == 0, "a replay rewrites no point"
    assert delete_points.await_count == 0, "a replay deletes no point"


async def test_a_forced_reindex_still_reconciles(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    set_payload = AsyncMock(wraps=world.qdrant.set_payload)
    monkeypatch.setattr(world.qdrant, "set_payload", set_payload)

    # The run goes on to download the file, which this world does not have; what matters is what it did first.
    with contextlib.suppress(Exception):
        _ = [
            e async for e in world.handler().process_event(
                EventTypes.REINDEX_RECORD.value, world.payload(forceReindex=True)
            )
        ]

    assert set_payload.await_count >= 1, "a forced reindex goes through the reconciliation"
    assert await world.points() == CHUNKS, "the points of a referenced virtual record are kept"
