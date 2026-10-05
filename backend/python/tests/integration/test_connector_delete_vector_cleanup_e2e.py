"""Connector deletes ask indexing to remove the deleted records' vectors: real Neo4j and ArangoDB.

Drives ``DataSourceEntitiesProcessor`` over a real ``GraphDataStore``:

- ``on_record_deleted`` (the per-record connector delete, Local FS and ~25
  others) used to read Record attributes off the stored document the store
  returns, found no ``virtualRecordId``, and published nothing.
- ``delete_record_by_external_id`` (Outlook) dropped the cleanup event the
  provider returned, and on ArangoDB the Outlook delete returned none at all.

The invariant checked: every record the delete removed that had vectors gets a
``deleteRecord`` event carrying its ``virtualRecordId``.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_connector_delete_vector_cleanup_e2e.py -m integration

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
ARANGO_DB = "connector_delete_it"

logger = logging.getLogger("connector-delete-it")


class _Producer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        self.events.append(message)
        return True

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        self.events.extend(m for _key, m in messages)
        return [True] * len(messages)

    def deleted_vrids(self) -> set[str]:
        return {e["payload"]["virtualRecordId"] for e in self.events if e.get("eventType") == "deleteRecord"}


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    producer: _Producer
    org_id: str
    user_id: str
    user_key: str
    drive_id: str
    outlook_id: str
    ids: dict[str, str] = field(default_factory=dict)

    def vrid(self, name: str) -> str:
        return f"vr-{self.ids[name]}"


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
    ids = [*w.ids.values(), w.user_key, w.drive_id, w.outlook_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value, CollectionNames.MAILS.value,
                       CollectionNames.USERS.value, CollectionNames.APPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.IS_OF_TYPE.value,
                  CollectionNames.RECORD_RELATIONS.value):
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
            graph=graph, processor=processor, producer=producer, org_id=f"org-del-{suffix}",
            user_id=f"user-del-{suffix}", user_key=f"ukey-del-{suffix}",
            drive_id=f"drive-del-{suffix}", outlook_id=f"outlook-del-{suffix}",
        )
        processor.org_id = w.org_id
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in ("drive_file", "email", "attachment"):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Delete Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": app_id, "name": name, "type": name, "appGroup": group, "scope": "team", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for app_id, name, group in ((w.drive_id, "Drive", "Google Workspace"),
                                     (w.outlook_id, "Outlook", "Microsoft 365"))],
        collection=CollectionNames.APPS.value,
    )
    common = {"org_id": w.org_id, "version": 1, "origin": OriginTypes.CONNECTOR,
              "indexing_status": ProgressStatus.COMPLETED.value}
    await g.batch_upsert_records([
        FileRecord(id=w.ids["drive_file"], record_name="report.pdf", record_type=RecordType.FILE,
                   external_record_id=f"ext-{w.ids['drive_file']}", connector_name=Connectors.GOOGLE_DRIVE,
                   connector_id=w.drive_id, is_file=True, **common),
        MailRecord(id=w.ids["email"], record_name="Quarterly numbers", record_type=RecordType.MAIL,
                   external_record_id=f"ext-{w.ids['email']}", connector_name=Connectors.OUTLOOK,
                   connector_id=w.outlook_id, subject="Quarterly numbers", **common),
        FileRecord(id=w.ids["attachment"], record_name="numbers.xlsx", record_type=RecordType.FILE,
                   external_record_id=f"ext-{w.ids['attachment']}", connector_name=Connectors.OUTLOOK,
                   connector_id=w.outlook_id, is_file=True, **common),
    ])
    for name in w.ids:
        await g.update_node(w.ids[name], CollectionNames.RECORDS.value, {"virtualRecordId": w.vrid(name)})

    records, users = CollectionNames.RECORDS.value, CollectionNames.USERS.value
    await g.batch_create_edges(
        [{"from_id": w.user_key, "from_collection": users, "to_id": w.ids[n], "to_collection": records,
          "role": "OWNER", "type": "USER", "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for n in w.ids],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.ids["email"], "from_collection": records, "to_id": w.ids["attachment"],
          "to_collection": records, "relationshipType": "ATTACHMENT",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )


async def test_a_per_record_connector_delete_publishes_its_vector_cleanup(world: _World) -> None:
    await world.processor.on_record_deleted(world.ids["drive_file"])

    assert await world.graph.get_document(world.ids["drive_file"], CollectionNames.RECORDS.value) is None
    assert world.producer.deleted_vrids() == {world.vrid("drive_file")}
    (event,) = world.producer.events
    assert event["payload"]["recordId"] == world.ids["drive_file"]
    assert event["payload"]["connectorId"] == world.drive_id


async def test_a_delete_by_external_id_publishes_cleanup_for_everything_it_removed(world: _World) -> None:
    await world.processor.delete_record_by_external_id(world.outlook_id, f"ext-{world.ids['email']}", world.user_id)

    assert await world.graph.get_document(world.ids["email"], CollectionNames.RECORDS.value) is None
    removed = {world.vrid("email")}
    if await world.graph.get_document(world.ids["attachment"], CollectionNames.RECORDS.value) is None:
        removed.add(world.vrid("attachment"))
    assert world.producer.deleted_vrids() == removed


async def _seed_personal_mailbox(w: _World) -> str:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    app_id = w.ids["personal_app"] = f"outlook-personal-del-{uuid.uuid4().hex[:10]}"
    for name in ("personal_email", "personal_attachment"):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await g.batch_upsert_nodes(
        [{"id": app_id, "name": "Outlook Personal", "type": Connectors.OUTLOOK_INDIVIDUAL.value,
          "appGroup": "Microsoft 365", "scope": "personal", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    common = {"org_id": w.org_id, "version": 1, "origin": OriginTypes.CONNECTOR,
              "indexing_status": ProgressStatus.COMPLETED.value,
              "connector_name": Connectors.OUTLOOK_INDIVIDUAL, "connector_id": app_id}
    await g.batch_upsert_records([
        MailRecord(id=w.ids["personal_email"], record_name="Trip plans", record_type=RecordType.MAIL,
                   external_record_id=f"ext-{w.ids['personal_email']}", subject="Trip plans", **common),
        FileRecord(id=w.ids["personal_attachment"], record_name="tickets.pdf", record_type=RecordType.FILE,
                   external_record_id=f"ext-{w.ids['personal_attachment']}", is_file=True, **common),
    ])
    records, users = CollectionNames.RECORDS.value, CollectionNames.USERS.value
    for name in ("personal_email", "personal_attachment"):
        await g.update_node(w.ids[name], records, {"virtualRecordId": w.vrid(name)})
    await g.batch_create_edges(
        [{"from_id": w.user_key, "from_collection": users, "to_id": w.ids[n], "to_collection": records,
          "role": "OWNER", "type": "USER", "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for n in ("personal_email", "personal_attachment")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.ids["personal_email"], "from_collection": records, "to_id": w.ids["personal_attachment"],
          "to_collection": records, "relationshipType": "ATTACHMENT",
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )
    return app_id


async def test_an_outlook_personal_delete_by_external_id_removes_the_mail(world: _World) -> None:
    app_id = await _seed_personal_mailbox(world)

    await world.processor.delete_record_by_external_id(
        app_id, f"ext-{world.ids['personal_email']}", world.user_id
    )

    records = CollectionNames.RECORDS.value
    assert await world.graph.get_document(world.ids["personal_email"], records) is None
    attachment = await world.graph.get_document(world.ids["personal_attachment"], records)
    if isinstance(world.graph, Neo4jProvider):
        # Neo4j's delete_record removes the record alone, as it does for Outlook.
        assert attachment is not None
        assert world.producer.deleted_vrids() == {world.vrid("personal_email")}
    else:
        assert attachment is None, "ArangoDB's Outlook delete removes the mail's attachments"
        assert world.producer.deleted_vrids() == {
            world.vrid("personal_email"), world.vrid("personal_attachment"),
        }
    assert {e["payload"]["connectorName"] for e in world.producer.events} == {Connectors.OUTLOOK_INDIVIDUAL.value}


async def test_an_outlook_personal_delete_by_external_id_with_the_trash_on_trashes_the_mail_and_its_attachment(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = await _seed_personal_mailbox(world)
    monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=True))

    await world.processor.delete_record_by_external_id(
        app_id, f"ext-{world.ids['personal_email']}", world.user_id
    )

    records = CollectionNames.RECORDS.value
    docs = [await world.graph.get_document(world.ids[n], records) for n in ("personal_email", "personal_attachment")]
    assert {(d["deleteSource"], d.get("deletedByUserId")) for d in docs} == {("CONNECTOR", None)}
    assert len({d["deleteBatchId"] for d in docs}) == 1
    (event,) = world.producer.events
    assert event["eventType"] == EventTypes.SOFT_DELETE_RECORDS.value
    assert set(event["payload"]["virtualRecordIds"]) == {
        world.vrid("personal_email"), world.vrid("personal_attachment"),
    }
