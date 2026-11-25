"""The repair of a same-bytes update the consumer gave up on, on real stores.

Real ArangoDB or Neo4j (record, permission edges), a real Qdrant collection and IndexingPipeline, and the real
RecordEventHandler. A same-bytes update is handed back until the delivery backstop while the membership sync keeps
failing; the consumer then abandons it. The record stays COMPLETED with a visible reason and the points' scope stamps
marked stale, and one syncVectorMembership is queued; delivering that event heals the points and puts back the reason
the record had before. A repair that fails too is given up once, and a real update that lands in between is not
overwritten.

  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml \
    -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait qdrant-vector-it neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_update_give_up_repair_e2e.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.core.org_context import for_org
from app.events.dedup import DedupDecision
from app.events.events import EventProcessor
from app.models.entities import FileRecord, RecordType
from app.modules.indexing import run as run_module
from app.modules.indexing.connector_off_events import SameBytesSkipPending
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
    StreamMessage,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import backstop_give_up_reason
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler
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
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support import it_backends
from tests.support.vector_db import make_config_service
from tests.unit.modules.pipeline.fakes import RecordingProducer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("qa-s7c-acl")
DIM, CHUNKS = 8, 3
BYTES = b"a,b\n1,2\n"
RECORDS, APPS, USERS, PERM = (CollectionNames.RECORDS.value, CollectionNames.APPS.value,
                              CollectionNames.USERS.value, CollectionNames.PERMISSION.value)


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
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "give_up_repair_it"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(graph.disconnect)
        registry = CollectionRegistry(vector_db_service=qdrant, strategy=SingleCollectionStrategy(),
                                      collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
                                      manifest_store=CollectionManifestStore(make_config_service(), MagicMock()), logger=logger)
        s = uuid.uuid4().hex[:10]
        w = type("W", (), {})()
        w.org, w.conn, w.rec, w.vrid = f"org-acl-{s}", f"conn-acl-{s}", f"rec-acl-{s}", f"vr-acl-{s}"
        w.removed, w.added = f"ukey-rm-{s}", f"ukey-add-{s}"
        w.conn2 = f"conn2-acl-{s}"
        w.removed_uid, w.added_uid = f"user-rm-{s}", f"user-add-{s}"
        w.graph = graph
        w.collection = await registry.ensure_collection(RecordContext(org_id=w.org), embedding_size=DIM)
        pipeline = IndexingPipeline(logger=logger, config_service=make_config_service(), graph_provider=graph,
                                    collection_registry=registry, vector_db_service=qdrant)
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes([
            {"id": k, "userId": u, "orgId": w.org, "email": f"{u}@example.com", "fullName": "QA", "isActive": True,
             "createdAtTimestamp": now, "updatedAtTimestamp": now} for k, u in ((w.removed, w.removed_uid), (w.added, w.added_uid))
        ], collection=USERS)
        await graph.batch_upsert_nodes([{"id": w.conn2, "orgId": w.org, "name": "Drive2", "type": "Drive",
                                         "appGroup": "Google Workspace", "scope": "team", "isActive": True,
                                         "createdAtTimestamp": now, "updatedAtTimestamp": now}], APPS)
        await graph.batch_upsert_nodes([{"id": w.conn, "orgId": w.org, "name": "Drive", "type": "Drive",
                                         "appGroup": "Google Workspace", "scope": "team", "isActive": True,
                                         "createdAtTimestamp": now, "updatedAtTimestamp": now}], APPS)
        await graph.batch_upsert_records([FileRecord(
            id=w.rec, org_id=w.org, record_name="data.csv", record_type=RecordType.FILE, external_record_id=w.rec,
            version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=w.conn,
            mime_type="text/csv", weburl=f"https://example.invalid/{w.rec}", indexing_status=ProgressStatus.COMPLETED.value,
            is_file=True, extension="csv", size_in_bytes=len(BYTES),
        )])
        await graph.update_node(w.rec, RECORDS, {"virtualRecordId": w.vrid, "extractionStatus": "COMPLETED", "hasIndexedCopy": True,
                                                 "contentRev": content_revision(BYTES), "lastIndexTimestamp": now})
        await graph.batch_create_edges([{"from_id": w.removed, "from_collection": USERS, "to_id": w.rec, "to_collection": RECORDS,
                                         "role": "OWNER", "type": "USER", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
                                       collection=PERM)
        await graph.batch_create_edges([
            {"from_id": k, "from_collection": USERS, "to_id": w.conn, "to_collection": APPS, "syncState": "COMPLETED",
             "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now} for k in (w.removed, w.added)
        ], collection=CollectionNames.USER_APP_RELATION.value)
        await qdrant.upsert_points(w.collection, [
            VectorPoint(id=str(uuid.uuid4()), dense_vector=[float((i + j) % 5) + 1.0 for j in range(DIM)],
                        payload={"page_content": f"chunk {i}", "metadata": {"orgId": w.org, "virtualRecordId": w.vrid},
                                 CONNECTOR_IDS_FIELD: [w.conn]}) for i in range(CHUNKS)])
        stages = StageRegistry()
        stages.register_external("embed")
        stages.register(ClassifyStage())
        stages.register(EntitiesStage())
        coordinator = Coordinator(stages, GraphStageStateStore(graph), GraphHeadlineStatusWriter(graph), RecordingProducer(),  # type: ignore[arg-type]
                                  policy_for=lambda _o: DEFAULT_POLICY, logger=logger)
        view = RecordView(org_id=w.org, virtual_record_id=w.vrid, rev=content_revision(BYTES), record_ids=(w.rec,),
                          connector_id=w.conn, tier=ParseTier.LIGHT, text_digest="t", blocks_digest="b", text_chars=10,
                          has_tables=False, has_images=False)
        await coordinator.on_external_done("embed", view, priority=Priority.BULK, trigger="t")
        w.parses = 0

        w.parse_mode = "forbid"

        async def parse(*_a: Any, **_k: Any):  # noqa: ANN202, ANN401
            w.parses += 1
            if w.parse_mode == "forbid":
                raise AssertionError("a paid re-parse ran")
            doc = await graph.get_document(w.rec, RECORDS)
            if w.parse_mode == "fail":
                raise RuntimeError("parser crashed")
            await qdrant.upsert_points(w.collection, [
                VectorPoint(id=str(uuid.uuid4()), dense_vector=[float((i + j) % 5) + 1.0 for j in range(DIM)],
                            payload={"page_content": f"new {i}", "metadata": {"orgId": w.org, "virtualRecordId": doc["virtualRecordId"]},
                                     CONNECTOR_IDS_FIELD: [doc["connectorId"]]}) for i in range(CHUNKS)])
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=w.rec))
            await graph.update_node(w.rec, RECORDS, {"indexingStatus": "COMPLETED", "hasIndexedCopy": True})
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=w.rec))

        processor = MagicMock()
        processor.indexing_pipeline = pipeline
        processor.process_delimited_document = MagicMock(side_effect=parse)
        w.ep = EventProcessor(logger, processor, graph, make_config_service(), stage_ingress=StageIngress(coordinator, graph=graph, logger=logger))
        w.ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision())
        w.ep._spawn_abandoned_vrid_cleanup = MagicMock()
        w.payload = {"recordId": w.rec, "orgId": w.org, "virtualRecordId": w.vrid, "connectorId": w.conn, "version": 2,
                     "connectorName": "DRIVE", "mimeType": "text/csv", "extension": "csv", "recordName": "data.csv", "buffer": BYTES}

        async def points(v: str | None = None) -> list[list[str]]:
            page = await qdrant.scroll(w.collection, await qdrant.filter_collection(must={"orgId": w.org, "virtualRecordId": v or w.vrid}), limit=100)
            return [list(p.payload.get(CONNECTOR_IDS_FIELD) or []) for p in page.points]

        async def container_hits(connector: str) -> int:
            # What a container-selected search fetches for a viewer whose reach is this app.
            page = await qdrant.scroll(w.collection, await qdrant.filter_collection(must={"orgId": w.org, CONNECTOR_IDS_FIELD: [connector]}), limit=100)
            return len(page.points)

        async def stamps() -> list[Any]:
            page = await qdrant.scroll(w.collection, await qdrant.filter_collection(must={"orgId": w.org, "virtualRecordId": w.vrid}), limit=100)
            return sorted({str(p.payload.get("scopeStamp")) for p in page.points})

        w.container_hits, w.stamps = container_hits, stamps

        w.points = points
        w.producer = AsyncMock()
        w.handler = RecordEventHandler(logger=logger, config_service=AsyncMock(), event_processor=w.ep, producer=w.producer)
        w.handler.config_service.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://connectors.invalid"}})
        try:
            yield w
        finally:
            await qdrant.delete_points(w.collection, await qdrant.filter_collection(must={"orgId": w.org}))
            with contextlib.suppress(Exception):
                await graph.delete_nodes([w.rec], RECORDS)
                await graph.delete_nodes([w.conn, w.conn2], APPS)
                await graph.delete_nodes([w.removed, w.added], USERS)




def _failing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_sync(*_a: object, **_k: object) -> None:
        raise RuntimeError("payload update rejected")
    monkeypatch.setattr(run_module, "_sync_vector_membership", failing_sync)


async def _give_up(world, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The same-bytes update handed back until the backstop (connector-stream path), then the real abandon."""
    backstop = messaging_env.redis_max_deliveries
    base = {k: v for k, v in world.payload.items() if k != "buffer"}
    with (
        patch.object(record_module, "generate_jwt", AsyncMock(return_value="token")),
        patch.object(record_module, "make_api_call", AsyncMock(return_value={"data": BYTES})),
    ):
        for i in range(backstop + 1):
            with for_org(world.org, job="give-up-repair-it"), pytest.raises(SameBytesSkipPending):
                _ = [e async for e in world.handler.process_event(EventTypes.UPDATE_RECORD.value, {**base, "is_final_failure": i == backstop})]
    msg = StreamMessage(eventType=EventTypes.UPDATE_RECORD.value, payload=base, timestamp=get_epoch_timestamp_in_ms())
    with for_org(world.org, job="give-up-repair-it"):  # disposition.notify_abandoned binds the message's org
        await world.handler.on_message_abandoned(msg, reason=backstop_give_up_reason(SameBytesSkipPending("rec: skip"), backstop + 1, backstop), attempts=backstop + 1)
    return base


def _sync_events(world) -> list[dict[str, Any]]:
    return [c.kwargs for c in world.producer.send_event.await_args_list if c.kwargs.get("event_type") == EventTypes.SYNC_VECTOR_MEMBERSHIP.value]


async def _deliver_sync(world, payload: dict[str, Any]) -> None:
    with for_org(world.org, job="give-up-repair-it"):
        _ = [e async for e in world.handler.process_event(EventTypes.SYNC_VECTOR_MEMBERSHIP.value, dict(payload))]


@pytest.mark.parametrize("scope_fields", [False, True])
async def test_give_up_then_the_queued_sync_heals_an_app_grant(world, monkeypatch: pytest.MonkeyPatch, scope_fields: bool) -> None:
    if scope_fields:
        monkeypatch.setenv("ACL_SCOPE_FIELDS", "true")
    g = world.graph
    # The grant: the record moves to an app the viewer can read; its same-bytes update must carry it to the points.
    await g.update_node(world.rec, RECORDS, {"connectorId": world.conn2})
    _failing(monkeypatch)
    await _give_up(world, monkeypatch)
    doc = await g.get_document(world.rec, RECORDS)
    events = _sync_events(world)
    after_give_up = {"status": doc["indexingStatus"], "reason": doc.get("reason"), "points": await world.points(),
                     "hits_new_app": await world.container_hits(world.conn2), "stamps": await world.stamps(), "sync_events": len(events)}
    monkeypatch.undo()  # the store is healthy again
    if scope_fields:
        monkeypatch.setenv("ACL_SCOPE_FIELDS", "true")
    await _deliver_sync(world, events[0]["payload"])
    doc = await g.get_document(world.rec, RECORDS)
    after_sync = {"reason": doc.get("reason"), "points": await world.points(), "hits_new_app": await world.container_hits(world.conn2),
                  "hits_old_app": await world.container_hits(world.conn), "stamps": await world.stamps()}
    assert world.parses == 0
    assert after_give_up["status"] == "COMPLETED" and after_give_up["reason"] and after_give_up["sync_events"] == 1
    assert after_give_up["hits_new_app"] == 0
    assert after_sync["hits_new_app"] == CHUNKS and after_sync["hits_old_app"] == 0
    assert after_sync["reason"] is None


async def test_a_repair_that_fails_too_is_given_up_once_and_leaves_the_reason(world, monkeypatch: pytest.MonkeyPatch) -> None:
    await world.graph.update_node(world.rec, RECORDS, {"connectorId": world.conn2})
    _failing(monkeypatch)
    await _give_up(world, monkeypatch)
    [event] = _sync_events(world)
    for _ in range(3):  # the sync job's own delivery attempts, store still down
        with pytest.raises(Exception):  # noqa: B017
            await _deliver_sync(world, event["payload"])
    msg = StreamMessage(eventType=EventTypes.SYNC_VECTOR_MEMBERSHIP.value, payload=event["payload"], timestamp=get_epoch_timestamp_in_ms())
    with for_org(world.org, job="give-up-repair-it"):
        await world.handler.on_message_abandoned(msg, reason="failed 3 times", attempts=3)
    doc = await world.graph.get_document(world.rec, RECORDS)
    assert len(_sync_events(world)) == 1
    assert doc["indexingStatus"] == "COMPLETED" and doc.get("reason")


@pytest.mark.parametrize("outcome", ["completes", "fails"])
async def test_a_real_update_between_give_up_and_repair_is_not_clobbered(world, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    _failing(monkeypatch)
    await _give_up(world, monkeypatch)
    [event] = _sync_events(world)
    monkeypatch.undo()
    # A real content change lands before the queued sync runs: full path, new vrid (CSV is not reconciled).
    world.parse_mode = "write" if outcome == "completes" else "fail"
    payload = {**world.payload, "buffer": b"a,b\n9,9\n"}
    with for_org(world.org, job="give-up-repair-it"):
        if outcome == "completes":
            _ = [e async for e in world.ep.on_event({"eventType": EventTypes.UPDATE_RECORD.value, "payload": payload})]
        else:
            with pytest.raises(Exception):  # noqa: B017
                _ = [e async for e in world.ep.on_event({"eventType": EventTypes.UPDATE_RECORD.value, "payload": payload})]
            await world.graph.update_node(world.rec, RECORDS, {"indexingStatus": "FAILED", "reason": "parser crashed"})
    before = await world.graph.get_document(world.rec, RECORDS)
    new_vrid = before["virtualRecordId"]
    new_points_before = await world.points(new_vrid)
    await _deliver_sync(world, event["payload"])
    after = await world.graph.get_document(world.rec, RECORDS)
    assert {k: after.get(k) for k in ("indexingStatus", "reason", "virtualRecordId")} == {k: before.get(k) for k in ("indexingStatus", "reason", "virtualRecordId")}
    assert len(await world.points(new_vrid)) == len(new_points_before)


async def test_an_existing_reason_on_the_record_survives_the_repair(world, monkeypatch: pytest.MonkeyPatch) -> None:
    await world.graph.update_node(world.rec, RECORDS, {"reason": "Classification skipped: no LLM configured"})
    _failing(monkeypatch)
    await _give_up(world, monkeypatch)
    [event] = _sync_events(world)
    monkeypatch.undo()
    await _deliver_sync(world, event["payload"])
    end = (await world.graph.get_document(world.rec, RECORDS)).get("reason")
    assert end == "Classification skipped: no LLM configured"
