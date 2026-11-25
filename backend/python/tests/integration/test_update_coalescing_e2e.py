"""Updates of the same bytes on real stores: k queued updates parse once; a forced reindex never skips.

Real ArangoDB or Neo4j for the record and the stage states (GraphStageStateStore, GraphHeadlineStatusWriter), a real
Qdrant with a real IndexingPipeline for the points and the membership, a real Coordinator + StageIngress with the
classify and entities stages registered as in the runtime (a recording producer stands in for the broker), and
EventProcessor.on_event with a counting parser that does what the sink does on success. The stage workers are
simulated by settling the dispatched stage states COMPLETED. The broker and download legs are not covered here.

  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml \
    -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait qdrant-vector-it neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_update_coalescing_e2e.py -m integration

1. A connector writes k updates (status reset to NOT_STARTED, as the connector does), then the k events are handled:
   exactly 1 parse; the k-1 others sync membership, write nothing to the record and claim no finished stage.
2. A membership change under the same bytes (the record's connector changes) reaches the points through the skip.
3. An update that arrives after the record completed, with the connector's reset, parses again: the skip only
   coalesces updates queued behind the one that indexed the bytes.
4. A forced reindex of a completed record with the same bytes is never skipped: it parses, and afterwards the record
   serves only its new points, with none left under the old vrid.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.core.org_context import for_org
from app.events.dedup import DedupDecision
from app.events.events import EventProcessor
from app.modules.indexing import run as run_module
from app.modules.indexing.run import IndexingPipeline
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.fingerprint import content_revision
from app.modules.pipeline.ingress import StageIngress
from app.modules.pipeline.models import Priority, RecordView
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.pipeline.stages.entities import EntitiesStage
from app.modules.pipeline.state import GraphHeadlineStatusWriter, GraphStageStateStore
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.resource_governor.models import ParseTier
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
from tests.support import it_backends
from tests.support.vector_db import make_config_service

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("update-coalescing-it")
DIM, CHUNKS = 8, 3
OLD, NEW = b"a,b\n1,2\n", b"a,b\n1,3\n"
RECORDS, APPS = CollectionNames.RECORDS.value, CollectionNames.APPS.value


class W:
    pass


class RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, object], str | None]] = []

    async def send_event(self, topic: str, event_type: str, payload: dict[str, object], key: str | None = None) -> bool:
        self.sent.append((topic, event_type, payload, key))
        return True


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(run_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    monkeypatch.setattr(membership_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    async with contextlib.AsyncExitStack() as cleanup:
        qdrant = QdrantService(QdrantConfig(host=it_backends.endpoint("QDRANT_IT_HOST"),
                                            port=int(os.environ.get("QDRANT_IT_PORT", "6343")), prefer_grpc=False, timeout=30))
        await qdrant.connect()
        cleanup.push_async_callback(qdrant.disconnect)
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "update_coalescing_it"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(graph.disconnect)
        registry = CollectionRegistry(
            vector_db_service=qdrant, strategy=SingleCollectionStrategy(),
            collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
            manifest_store=CollectionManifestStore(make_config_service(), MagicMock()), logger=logger,
        )
        s = uuid.uuid4().hex[:10]
        w = W()
        w.org, w.c1, w.c2, w.record, w.vrid = f"org-uc-{s}", f"conn-uc-{s}", f"conn2-uc-{s}", f"rec-uc-{s}", f"vr-uc-{s}"
        w.collection = await registry.ensure_collection(RecordContext(org_id=w.org), embedding_size=DIM)
        pipeline = IndexingPipeline(logger=logger, config_service=make_config_service(), graph_provider=graph,
                                    collection_registry=registry, vector_db_service=qdrant)
        for c in (w.c1, w.c2):
            await graph.batch_upsert_nodes([{"id": c, "orgId": w.org, "name": "c", "type": "SLACK", "appGroup": "Slack",
                                             "scope": "team", "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}], APPS)
        await graph.batch_upsert_nodes([{
            "id": w.record, "orgId": w.org, "recordName": "data.csv", "externalRecordId": w.record, "recordType": "FILE",
            "origin": "CONNECTOR", "connectorName": "SLACK", "connectorId": w.c1, "virtualRecordId": w.vrid,
            "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "mimeType": "text/csv",
            "indexingStatus": ProgressStatus.COMPLETED.value, "extractionStatus": ProgressStatus.COMPLETED.value,
            "hasIndexedCopy": True, "contentRev": content_revision(OLD), "lastIndexTimestamp": 1_700_000_000_000,
        }], RECORDS)

        stages = StageRegistry()
        stages.register_external("embed")
        stages.register(ClassifyStage())
        stages.register(EntitiesStage())
        w.states = GraphStageStateStore(graph)
        w.producer = RecordingProducer()
        coordinator = Coordinator(stages, w.states, GraphHeadlineStatusWriter(graph), w.producer,  # type: ignore[arg-type]
                                  policy_for=lambda _o: DEFAULT_POLICY, logger=logger)
        ingress = StageIngress(coordinator, graph=graph, logger=logger)
        w.parses = 0

        async def write_points(v: str, n: int) -> None:
            await qdrant.upsert_points(w.collection, [
                VectorPoint(id=str(uuid.uuid4()), dense_vector=[float((i + j) % 5) + 1.0 for j in range(DIM)],
                            payload={"page_content": f"{v} chunk {i}", "metadata": {"orgId": w.org, "virtualRecordId": v},
                                     CONNECTOR_IDS_FIELD: [w.c1]})
                for i in range(n)
            ])

        async def settle_stages(v: str, rev: str) -> None:
            for stage in ("classify", "entities"):
                key = f"{v}:{rev}:{stage}"
                state = await w.states.get(key)
                if state is not None and state.status is ProgressStatus.QUEUED:
                    assert await w.states.cas(key, expected=ProgressStatus.QUEUED, new=ProgressStatus.COMPLETED)

        async def parse(*_a: Any, **_k: Any):  # noqa: ANN202, ANN401
            w.parses += 1
            doc = await graph.get_document(w.record, RECORDS)
            v, rev = doc["virtualRecordId"], doc["contentRev"]
            await write_points(v, CHUNKS)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=w.record))
            await graph.update_node(w.record, RECORDS, {"indexingStatus": "COMPLETED", "hasIndexedCopy": True,
                                                         "extractionStatus": "QUEUED", "lastIndexTimestamp": 1_700_000_000_000 + w.parses})
            view = RecordView(org_id=w.org, virtual_record_id=v, rev=rev, record_ids=(w.record,), connector_id=w.c1,
                              tier=ParseTier.LIGHT, text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False)
            await coordinator.on_external_done("embed", view, priority=Priority.BULK, trigger="updateRecord")
            await settle_stages(v, rev)
            await graph.update_node(w.record, RECORDS, {"extractionStatus": "COMPLETED"})
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=w.record))

        processor = MagicMock()
        processor.indexing_pipeline = pipeline
        processor.process_delimited_document = MagicMock(side_effect=parse)
        ep = EventProcessor(logger, processor, graph, make_config_service(), stage_ingress=ingress)
        ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision())
        ep._spawn_abandoned_vrid_cleanup = MagicMock()
        w.sync_calls = 0
        real_sync = pipeline.sync_vector_membership

        async def counting_sync(v: str) -> None:
            w.sync_calls += 1
            await real_sync(v)

        pipeline.sync_vector_membership = counting_sync  # type: ignore[method-assign]

        async def handle(buffer: bytes, *, forced: bool = False) -> None:
            payload = {**({"forceReindex": True} if forced else {}), "recordId": w.record, "orgId": w.org, "virtualRecordId": w.vrid, "connectorId": w.c1, "version": 2,
                       "connectorName": "SLACK", "mimeType": "text/csv", "extension": "csv", "recordName": "data.csv", "buffer": buffer}
            with for_org(w.org, job="update-coalescing-it"):
                _ = [e async for e in ep.on_event({"eventType": EventTypes.UPDATE_RECORD.value, "payload": payload})]

        async def all_vrids() -> set[str]:
            page = await qdrant.scroll(w.collection, await qdrant.filter_collection(must={"orgId": w.org}), limit=1000)
            return {p.payload["metadata"]["virtualRecordId"] for p in page.points}

        w.all_vrids = all_vrids

        async def connector_writes_update() -> None:
            # What _process_record does for a real content change of a COMPLETED record.
            await graph.update_node(w.record, RECORDS, {"indexingStatus": ProgressStatus.NOT_STARTED.value})

        async def point_connectors(v: str) -> list[list[str]]:
            page = await qdrant.scroll(w.collection, await qdrant.filter_collection(must={"orgId": w.org, "virtualRecordId": v}), limit=100)
            return [list(p.payload.get(CONNECTOR_IDS_FIELD) or []) for p in page.points]

        w.graph, w.handle, w.connector_writes_update, w.point_connectors = graph, handle, connector_writes_update, point_connectors
        await write_points(w.vrid, CHUNKS)
        try:
            yield w
        finally:
            await qdrant.delete_points(w.collection, await qdrant.filter_collection(must={"orgId": w.org}))
            await graph.delete_nodes([w.record], RECORDS)
            await graph.delete_nodes([w.c1, w.c2], APPS)


@pytest.mark.parametrize("k", [2, 5, 20])
async def test_k_queued_updates_parse_once(world, k: int) -> None:
    for _ in range(k):
        await world.connector_writes_update()
    # The first write reset the status; the rest find it NOT_STARTED already.
    for _ in range(k):
        await world.handle(NEW)
    doc = await world.graph.get_document(world.record, RECORDS)
    sent_after_first = len(world.producer.sent)
    logger.info(f"JOURNEY k={k} parses={world.parses} syncs={world.sync_calls} stage_jobs={sent_after_first} "
          f"status={doc['indexingStatus']} extraction={doc.get('extractionStatus')}")
    assert world.parses == 1
    assert doc["contentRev"] == content_revision(NEW)
    assert doc["extractionStatus"] == "COMPLETED"
    # 2 stage jobs from the one full run; the k-1 skips claim nothing that finished.
    assert sent_after_first == 2, [p[0] for p in world.producer.sent]


async def test_a_membership_change_under_the_same_bytes_reaches_the_points(world) -> None:
    await world.connector_writes_update()
    await world.handle(NEW)
    doc = await world.graph.get_document(world.record, RECORDS)
    v = doc["virtualRecordId"]
    await world.graph.update_node(world.record, RECORDS, {"connectorId": world.c2})
    await world.handle(NEW)  # same bytes, status still COMPLETED: the skip
    assert world.parses == 1
    arrays = await world.point_connectors(v)

    assert arrays and all(a == [world.c2] for a in arrays)


async def test_an_update_after_completion_with_the_connector_reset_parses_again(world) -> None:
    await world.connector_writes_update()
    await world.handle(NEW)
    await world.connector_writes_update()  # a later edit with the same bytes (e.g. metadata-only), reset by the connector
    await world.handle(NEW)

    assert world.parses == 2


async def test_a_forced_reindex_of_the_same_bytes_is_never_skipped(world) -> None:
    await world.connector_writes_update()
    await world.handle(NEW)
    assert world.parses == 1
    await world.handle(NEW, forced=True)

    assert world.parses == 2, "a forced reindex goes through the full run"
    doc = await world.graph.get_document(world.record, RECORDS)
    assert await world.all_vrids() == {doc["virtualRecordId"]}, "only the new points are served, none are left behind"
