"""Turning a connector off and on again, against a real Neo4j and a real ArangoDB.

Records are written by the production ``DataSourceEntitiesProcessor`` and moved
by the production ``EntityEventService`` handlers, so the queries under test are
the providers' own: the narrowed, reason-stamping, trash-skipping disable
sweep (``reset_indexing_status_for_connector``), and the resume's
equality-filtered, key-cursor page read and conditional write
(``get_documents_paginated`` with ``after_key``, and
``update_nodes_fields_if_match``) with a null ``reason``.

Before the fix the pause turned every QUEUED (and FAILED, EMPTY, ...) record
into AUTO_INDEX_OFF with no reason, and turning the connector on queued none of
them again. Records the connector's filters keep out of auto-indexing are
AUTO_INDEX_OFF with no reason too, and must stay so.

Arango enforces the records schema strictly, so its run also proves every field
the resume writes is declared there.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_connector_resume_requeue_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import RecordGroupType, RecordType, TicketRecord
from app.modules.indexing.connector_off_events import connector_off_updates
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_errors import CONNECTOR_OFF
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ORG_ID = "org-resume-it"
ARANGO_DB = "resume_requeue_it"

logger = logging.getLogger("resume-requeue-it")


class _RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        self.sent.extend(messages)
        return [True] * len(messages)

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        self.sent.append((key, message))
        return True


async def _remove_connector_data(graph: IGraphDBProvider, connector_id: str) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id = $c DETACH DELETE n",
            parameters={"c": connector_id},
        )
        return
    for collection, field in (
        (CollectionNames.RECORDS.value, "connectorId"),
        (CollectionNames.RECORD_GROUPS.value, "connectorId"),
        (CollectionNames.APPS.value, "_key"),
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.{field} == @c REMOVE d IN {collection}",
            {"c": connector_id},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch)
                if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


def _issue(connector_id: str, name: str) -> TicketRecord:
    now = get_epoch_timestamp_in_ms()
    return TicketRecord(
        org_id=ORG_ID,
        record_name=name,
        record_type=RecordType.TICKET,
        external_record_id=f"{name}-{uuid.uuid4().hex[:8]}",
        external_revision_id="1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id=connector_id,
        mime_type="application/blocks",
        external_record_group_id="10039",
        record_group_type=RecordGroupType.PROJECT,
        created_at=now,
        updated_at=now,
    )


async def _set_active(graph: IGraphDBProvider, connector_id: str, active: bool) -> None:
    await graph.update_node(connector_id, CollectionNames.APPS.value, {"isActive": active})


async def test_pause_and_resume_requeue_only_what_the_pause_parked(graph: IGraphDBProvider) -> None:
    connector_id = f"resume-it-{uuid.uuid4().hex[:10]}"
    async with contextlib.AsyncExitStack() as cleanup:
        cleanup.push_async_callback(_remove_connector_data, graph, connector_id)
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{
                "id": connector_id,
                "name": "Jira",
                "type": "Jira",
                "appGroup": "Atlassian",
                "scope": "team",
                "isActive": True,
                "createdAtTimestamp": now,
                "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = ORG_ID
        processor.messaging_producer = _RecordingProducer()
        names = ["queued-1", "queued-2", "in-flight", "manual-only", "failed", "trashed"]
        issues = {name: _issue(connector_id, name) for name in names}
        await processor.on_new_records([(issue, []) for issue in issues.values()])
        ids = {name: issue.id for name, issue in issues.items()}

        async def stored(name: str) -> dict:
            doc = await graph.get_document(ids[name], CollectionNames.RECORDS.value)
            assert doc is not None, name
            return doc

        for name in names:
            assert (await stored(name))["indexingStatus"] == ProgressStatus.QUEUED.value, name
        await graph.update_node(ids["in-flight"], CollectionNames.RECORDS.value, {
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
            "parsingStatus": ProgressStatus.IN_PROGRESS.value,
        })
        # What a manual-indexing filter leaves behind: AUTO_INDEX_OFF, no reason.
        await graph.update_node(ids["manual-only"], CollectionNames.RECORDS.value, {
            "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
        })
        await graph.update_node(ids["failed"], CollectionNames.RECORDS.value, {
            "indexingStatus": ProgressStatus.FAILED.value,
            "reason": "parse error",
        })
        # Soft delete leaves a queued record's indexingStatus as it was.
        await graph.update_node(ids["trashed"], CollectionNames.RECORDS.value, {"isDeleted": True})

        producer = _RecordingProducer()
        container = MagicMock()
        container.messaging_producer = producer
        service = EntityEventService(logger, graph, container)

        await _set_active(graph, connector_id, False)
        with patch.object(entity_module, "get_coordinator", return_value=None):
            assert await service.process_event(
                "appDisabled", {"orgId": ORG_ID, "apps": ["jira"], "connectorId": connector_id}
            )

        for name in ("queued-1", "queued-2"):
            doc = await stored(name)
            assert doc["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value, name
            assert doc["reason"] == CONNECTOR_OFF, name
        assert (await stored("in-flight"))["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        assert (await stored("manual-only")).get("reason") is None
        failed = await stored("failed")
        assert (failed["indexingStatus"], failed["reason"]) == (ProgressStatus.FAILED.value, "parse error")
        trashed = await stored("trashed")
        assert trashed["indexingStatus"] == ProgressStatus.QUEUED.value
        assert trashed.get("reason") is None

        # The indexing handler parks the in-flight record while the connector is off.
        in_flight = await stored("in-flight")
        await graph.update_node(
            ids["in-flight"], CollectionNames.RECORDS.value, connector_off_updates(in_flight)
        )

        await _set_active(graph, connector_id, True)
        # Two pages, so the key cursor is what reaches the third record.
        with patch.object(entity_module, "_REQUEUE_PAGE_SIZE", 2):
            assert await service._requeue_records_parked_while_off(connector_id) == 3

        assert {key for key, _ in producer.sent} == {ids["queued-1"], ids["queued-2"], ids["in-flight"]}
        for name in ("queued-1", "queued-2", "in-flight"):
            doc = await stored(name)
            assert doc["indexingStatus"] == ProgressStatus.QUEUED.value, name
            assert doc.get("reason") is None, name
            assert doc["queuedAtTimestamp"] >= now, name
        assert (await stored("in-flight"))["parsingStatus"] == ProgressStatus.NOT_STARTED.value

        manual = await stored("manual-only")
        assert manual["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
        assert manual.get("reason") is None
        failed = await stored("failed")
        assert (failed["indexingStatus"], failed["reason"]) == (ProgressStatus.FAILED.value, "parse error")

        assert (await stored("trashed"))["indexingStatus"] == ProgressStatus.QUEUED.value

        # Nothing is left parked, so a second enable sends nothing more.
        assert await service._requeue_records_parked_while_off(connector_id) == 0
