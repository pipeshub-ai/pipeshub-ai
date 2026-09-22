"""End-to-end: the stranded-record sweep against a real graph and broker.

Drives the production path. ``DataSourceEntitiesProcessor.on_new_records``
upserts a Jira-shaped issue, created and last edited a year ago at the source,
and publishes its event to a real Redis stream. Then
``indexing_main._republish_stranded_records`` runs against the same graph.

Before the fix the sweep aged rows on updatedAtTimestamp, which the Jira
connector fills with the issue's own edit time, so every freshly synced issue
was sent a second event on the first sweep tick (3,130 of 3,330 in one sync);
records built on the model default carried the connector process's start time
and went the same way an hour after start.

Runs on Neo4j and on ArangoDB. Arango enforces the records schema strictly, so
the Arango run is also what proves the sweep's claim marker and the queue clock
are declared there: an undeclared field is rejected and nothing is recovered.

Needs Docker services, and skips cleanly when they are not reachable:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    -f deployment/docker-compose/docker-compose.integration.messaging.yml \
    up -d --wait neo4j-graph-it arango-graph-it redis-messaging-it
  cd backend/python && pytest tests/integration/test_stranded_record_sweep_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD,
REDIS_IT_HOST, REDIS_IT_PORT.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

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
from app.indexing_main import _republish_stranded_records
from app.models.entities import RecordGroupType, RecordType, TicketRecord
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Well past the suite's 30s default: a first run creates Arango's collections.
pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "stranded_sweep_it"
REDIS_HOST = os.environ.get("REDIS_IT_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_IT_PORT", "6389"))

HOUR_MS = 3600 * 1000
A_YEAR_AGO = get_epoch_timestamp_in_ms() - 365 * 24 * HOUR_MS
ORG_ID = "org-stranded-it"

logger = logging.getLogger("stranded-sweep-it")


class _OneStreamProducer:
    """The real Redis Streams producer, with every topic sent to this test's stream."""

    def __init__(self, inner: RedisStreamsProducer, stream: str) -> None:
        self._inner = inner
        self._stream = stream

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        return await self._inner.send_messages(self._stream, messages)

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        return await self._inner.send_event(self._stream, event_type, payload, key)


@dataclass
class _Env:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    producer: _OneStreamProducer
    redis: object
    stream: str
    connector_id: str

    async def events_for(self, record_id: str) -> int:
        entries = await self.redis.xrange(self.stream)
        return sum(1 for _entry_id, fields in entries if fields.get("key") == record_id)

    async def sweep(self) -> int:
        async def run_coordination(coro: object) -> object:
            return await coro

        return await _republish_stranded_records(
            graph_provider=self.graph,
            logger=logger,
            producer=self.producer,
            run_coordination=run_coordination,
            concurrency_manager=None,
            page_size=500,
        )

    async def stored(self, record_id: str) -> dict:
        doc = await self.graph.get_document(record_id, CollectionNames.RECORDS.value)
        assert doc is not None, f"record {record_id} was not stored"
        return doc


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
    # Applies the strict collection schemas, to pre-existing collections too.
    await provider.ensure_schema()
    return provider


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
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    redis_mod = pytest.importorskip("redis.asyncio")
    monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")

    # Every resource is registered as soon as it exists, so a skip or failure
    # part-way through setup still releases whatever was acquired before it.
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        redis = redis_mod.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        cleanup.push_async_callback(redis.aclose)
        try:
            await asyncio.wait_for(redis.ping(), timeout=10)
        except Exception as exc:
            pytest.skip(f"Redis not available at {REDIS_HOST}:{REDIS_PORT}: {exc}")

        suffix = uuid.uuid4().hex[:10]
        connector_id = f"jira-it-{suffix}"
        stream = f"record-events-it-{suffix}"
        cleanup.push_async_callback(redis.delete, stream)
        cleanup.push_async_callback(_remove_connector_data, graph, connector_id)
        yield await _seeded_env(graph, redis, stream, connector_id, cleanup)


async def _seeded_env(
    graph: IGraphDBProvider,
    redis: object,
    stream: str,
    connector_id: str,
    cleanup: contextlib.AsyncExitStack,
) -> _Env:
    """Seed a live Jira connector and wire the real processor to this test's stream."""
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

    inner = RedisStreamsProducer(logger, RedisStreamsConfig(host=REDIS_HOST, port=REDIS_PORT, client_id="stranded-it"))
    cleanup.push_async_callback(inner.cleanup)
    await inner.initialize()
    producer = _OneStreamProducer(inner, stream)
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
    processor.org_id = ORG_ID
    processor.messaging_producer = producer

    return _Env(graph, processor, producer, redis, stream, connector_id)


def _jira_issue(connector_id: str, **overrides: object) -> TicketRecord:
    """Shaped like the Jira connector's issues: its clocks are the source system's."""
    fields: dict = {
        "org_id": ORG_ID,
        "record_name": "[PA-722] UploadNextVersion + Upload doc issues",
        "record_type": RecordType.TICKET,
        "external_record_id": f"issue-{uuid.uuid4().hex[:8]}",
        "external_revision_id": str(A_YEAR_AGO),
        "version": 0,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.JIRA,
        "connector_id": connector_id,
        "mime_type": "application/blocks",
        "external_record_group_id": "10039",
        "record_group_type": RecordGroupType.PROJECT,
        "created_at": A_YEAR_AGO,
        "updated_at": A_YEAR_AGO,
        "source_created_at": A_YEAR_AGO,
        "source_updated_at": A_YEAR_AGO,
    }
    fields.update(overrides)
    return TicketRecord(**fields)


async def test_a_freshly_synced_issue_with_old_source_clocks_is_left_alone(env: _Env) -> None:
    issue = _jira_issue(env.connector_id)
    before = get_epoch_timestamp_in_ms()

    await env.processor.on_new_records([(issue, [])])

    stored = await env.stored(issue.id)
    assert stored["indexingStatus"] == ProgressStatus.QUEUED.value
    assert stored["updatedAtTimestamp"] == A_YEAR_AGO, "the connector's own clock is kept"
    assert stored["queuedAtTimestamp"] >= before
    assert await env.events_for(issue.id) == 1

    await env.sweep()

    assert await env.events_for(issue.id) == 1, "its event is still on the broker; no duplicate"


async def test_an_issue_whose_event_was_lost_is_resent_exactly_once(env: _Env) -> None:
    issue = _jira_issue(env.connector_id)
    await env.processor.on_new_records([(issue, [])])
    # Lose the event: the row stays QUEUED while its queue clock passes the threshold.
    await env.graph.update_node(
        issue.id,
        CollectionNames.RECORDS.value,
        {"queuedAtTimestamp": get_epoch_timestamp_in_ms() - 2 * HOUR_MS},
    )

    await env.sweep()

    assert await env.events_for(issue.id) == 2
    assert (await env.stored(issue.id)).get("lastRepublishedAt"), "the claim marker was stored"

    await env.sweep()

    assert await env.events_for(issue.id) == 2, "at most one resend per threshold window"


async def test_a_metadata_refresh_does_not_postpone_recovering_a_lost_event(env: _Env) -> None:
    """A rename publishes no event, so it must not restart the sweep's clock."""
    issue = _jira_issue(env.connector_id)
    await env.processor.on_new_records([(issue, [])])
    await env.graph.update_node(
        issue.id,
        CollectionNames.RECORDS.value,
        {"queuedAtTimestamp": get_epoch_timestamp_in_ms() - 2 * HOUR_MS},
    )

    await env.processor.on_record_metadata_update(
        _jira_issue(
            env.connector_id,
            external_record_id=issue.external_record_id,
            record_name="[PA-722] UploadNextVersion (renamed)",
        )
    )
    await env.sweep()

    assert (await env.stored(issue.id))["recordName"].endswith("(renamed)")
    assert await env.events_for(issue.id) == 2, "the lost event is still recovered"


async def test_a_record_on_default_timestamps_is_not_aged_to_process_start(env: _Env) -> None:
    issue = _jira_issue(env.connector_id)
    # Rebuilt without explicit clocks, as most connectors build their records.
    issue = TicketRecord(**issue.model_dump(exclude={"created_at", "updated_at"}))
    before = get_epoch_timestamp_in_ms()

    await env.processor.on_new_records([(issue, [])])

    stored = await env.stored(issue.id)
    assert stored["createdAtTimestamp"] >= before
    assert stored["updatedAtTimestamp"] >= before
