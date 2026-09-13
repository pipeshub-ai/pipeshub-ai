"""ensure_pipeline_schema against real Neo4j and ArangoDB.

The indexing service runs it before consuming, beside the connector service's bootstrap, so it
has to be idempotent on a live database, wait for the collections the connector service
creates, and repair duplicate stage states that a missing unique constraint let through.

Uses the same throwaway engines as test_ensure_nodes_it.py; each test skips when its engine
is not reachable.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.neo4j import collection_to_label
from app.modules.pipeline.models import Priority, StageJob, StageState
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.resource_governor.models import ParseTier

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("GRAPH_IT_NEO4J_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("GRAPH_IT_NEO4J_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("GRAPH_IT_ARANGO_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("GRAPH_IT_ARANGO_PASSWORD", "ensure-it-pass")

STAGES = CollectionNames.STAGE_STATES.value
RECORDS = CollectionNames.RECORDS.value
LABEL = collection_to_label(STAGES)
ORG = "pipeline-schema-it"
_logger = logging.getLogger("pipeline-schema-it")


def _stage_state(vrid: str) -> dict[str, object]:
    job = StageJob(
        stage="classify", stage_version=1, org_id=ORG, virtual_record_id=vrid, rev="rev-a",
        record_ids=("rec-1",), connector_id="conn-1", tier=ParseTier.LIGHT, priority=Priority.BULK, trigger="embed",
        text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
    )
    state = StageState.from_job(job, ProgressStatus.COMPLETED, 1).model_copy(update={"output": '{"summary": "s"}'})
    document = state.model_dump(mode="json", by_alias=True)
    document["id"] = document.pop("key")
    return document


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jProvider]:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(_logger, AsyncMock())
    if not await provider.connect():
        pytest.skip(f"Neo4j not reachable at {NEO4J_URI}")
    assert provider.client is not None
    yield provider
    await provider.client.execute_query(f"MATCH (n:{LABEL}) WHERE n.orgId = $org DETACH DELETE n", {"org": ORG})
    # A test drops the database-wide constraint; put it back however the test ended.
    await provider.client.execute_query(
        f"CREATE CONSTRAINT {LABEL.lower()}_id_unique IF NOT EXISTS FOR (n:{LABEL}) REQUIRE n.id IS UNIQUE"
    )
    await provider.disconnect()


@pytest.fixture
async def arango() -> AsyncIterator[tuple[ArangoHTTPProvider, str]]:
    # A database of its own, so the test can start before records exists, as a first start does.
    database = f"pipeline_schema_it_{uuid.uuid4().hex[:8]}"
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": database,
    })
    provider = ArangoHTTPProvider(_logger, config)
    if not await provider.connect():
        pytest.skip(f"ArangoDB not reachable at {ARANGO_URL}")
    yield provider, database
    await provider.disconnect()
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", ARANGO_PASSWORD)) as session:
        async with session.delete(f"{ARANGO_URL}/_db/_system/_api/database/{database}") as response:
            assert response.status in (200, 404)


async def _index_fields(database: str, collection: str) -> list[list[str]]:
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", ARANGO_PASSWORD)) as session:
        async with session.get(f"{ARANGO_URL}/_db/{database}/_api/index", params={"collection": collection}) as response:
            assert response.status == 200
            body = await response.json()
    return [index["fields"] for index in body["indexes"] if index["type"] == "persistent"]


async def test_arango_waits_for_records_then_enforces_both_schemas(
    arango: tuple[ArangoHTTPProvider, str],
) -> None:
    provider, database = arango
    client = provider.http_client
    assert client is not None

    # A first start: the connector service has not created records yet.
    with pytest.raises(RuntimeError, match=RECORDS):
        await provider.ensure_pipeline_schema()
    assert await client.create_collection(RECORDS)

    await provider.ensure_pipeline_schema()
    await provider.ensure_pipeline_schema()

    assert await provider.stage_state_create(_stage_state(f"vr-{uuid.uuid4()}"))
    with pytest.raises(Exception):  # noqa: B017 - records validates against the current schema now
        await client.create_document(RECORDS, {"_key": "not-a-record"})
    fields = await _index_fields(database, STAGES)
    assert ["status", "updatedAtMs"] in fields
    assert ["virtualRecordId", "rev"] in fields


async def test_neo4j_repairs_duplicates_left_without_the_constraint(neo4j: Neo4jProvider) -> None:
    client = neo4j.client
    assert client is not None
    await client.execute_query(f"DROP CONSTRAINT {LABEL.lower()}_id_unique IF EXISTS")
    key = f"{ORG}:{uuid.uuid4()}"
    for updated in (1, 5, 3):
        await client.execute_query(
            f"CREATE (n:{LABEL} {{id: $id, orgId: $org, updatedAtMs: $updated}})",
            {"id": key, "org": ORG, "updated": updated},
        )

    await neo4j.ensure_pipeline_schema()
    await neo4j.ensure_pipeline_schema()

    rows = await client.execute_query(f"MATCH (n:{LABEL} {{id: $id}}) RETURN n.updatedAtMs AS updated", {"id": key})
    assert [row["updated"] for row in rows] == [5]
    with pytest.raises(Exception):  # noqa: B017 - the unique constraint rejects a second node
        await client.execute_query(f"CREATE (n:{LABEL} {{id: $id, orgId: $org}})", {"id": key, "org": ORG})
