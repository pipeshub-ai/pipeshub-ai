"""Stage states against real Neo4j and ArangoDB (UNIT-ST-01..05 on the engines, RACE-01/02).

Exactly-once dispatch rests on two database guarantees: a unique insert succeeds for one
concurrent caller, and a compare-and-set succeeds for one. Neo4j can lose updates when a
status predicate is read before the write lock, so both guarantees are checked on the real
engines, with the real Arango schema, rather than on mocks.

Uses the same throwaway engines as test_ensure_nodes_it.py; each test skips when its
engine is not reachable.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.neo4j import collection_to_label
from app.modules.pipeline.models import Priority, StageJob, StageState
from app.schema.arango.documents import stage_state_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.resource_governor.models import ParseTier

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("GRAPH_IT_NEO4J_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("GRAPH_IT_NEO4J_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("GRAPH_IT_ARANGO_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("GRAPH_IT_ARANGO_PASSWORD", "ensure-it-pass")

STAGES = CollectionNames.STAGE_STATES.value
RECORDS = CollectionNames.RECORDS.value
ORG = "stage-it"
CONCURRENT = 30
_logger = logging.getLogger("stage-states-it")


def _document(vrid: str, *, status: ProgressStatus = ProgressStatus.QUEUED, updated_at_ms: int = 1) -> dict[str, object]:
    job = StageJob(
        stage="classify", stage_version=1, org_id=ORG, virtual_record_id=vrid, rev="rev-a",
        record_ids=("rec-1",), connector_id="conn-1", tier=ParseTier.LIGHT, priority=Priority.BULK, trigger="embed",
        text_digest="t", blocks_digest="b", text_chars=10, has_tables=False, has_images=False,
    )
    document = StageState.from_job(job, status, updated_at_ms).model_dump(mode="json", by_alias=True)
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
    for label in (collection_to_label(STAGES), collection_to_label(RECORDS)):
        await provider.client.execute_query(
            f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )
    yield provider
    await provider.client.execute_query(
        f"MATCH (n) WHERE (n:{collection_to_label(STAGES)} OR n:{collection_to_label(RECORDS)}) "
        "AND n.orgId = $org DETACH DELETE n",
        parameters={"org": ORG},
    )
    await provider.disconnect()


@pytest.fixture
async def arango() -> AsyncIterator[ArangoHTTPProvider]:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": "ensure_it",
    })
    provider = ArangoHTTPProvider(_logger, config)
    if not await provider.connect():
        pytest.skip(f"ArangoDB not reachable at {ARANGO_URL}")
    client = provider.http_client
    assert client is not None
    if await client.has_collection(STAGES):
        await client.update_collection_schema(STAGES, stage_state_schema)
    else:
        await client.create_collection(STAGES, edge=False, schema=stage_state_schema)
    if not await client.has_collection(RECORDS):
        await client.create_collection(RECORDS, edge=False, schema=None)
    yield provider
    for collection in (STAGES, RECORDS):
        await client.execute_aql(
            "FOR d IN @@c FILTER d.orgId == @org REMOVE d IN @@c", {"@c": collection, "org": ORG}
        )
    await provider.disconnect()


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> IGraphDBProvider:
    return request.getfixturevalue(request.param)


async def _insert_record(provider: IGraphDBProvider, record_id: str, rev: str) -> None:
    if isinstance(provider, Neo4jProvider):
        assert provider.client is not None
        await provider.client.execute_query(
            f"CREATE (n:{collection_to_label(RECORDS)} {{id: $id, orgId: $org, contentRev: $rev}})",
            parameters={"id": record_id, "org": ORG, "rev": rev},
        )
    else:
        assert isinstance(provider, ArangoHTTPProvider) and provider.http_client is not None
        await provider.http_client.execute_aql(
            "INSERT {_key: @id, orgId: @org, contentRev: @rev} INTO @@c",
            {"@c": RECORDS, "id": record_id, "org": ORG, "rev": rev},
        )


@pytest.mark.asyncio
async def test_concurrent_creates_of_one_key_succeed_exactly_once(provider: IGraphDBProvider) -> None:
    document = _document(str(uuid.uuid4()))
    results = await asyncio.gather(*(provider.stage_state_create(dict(document)) for _ in range(CONCURRENT)))
    assert results.count(True) == 1
    stored = await provider.stage_state_get(str(document["id"]))
    assert stored is not None and stored["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_concurrent_compare_and_sets_succeed_exactly_once(provider: IGraphDBProvider) -> None:
    document = _document(str(uuid.uuid4()))
    assert await provider.stage_state_create(document)
    key = str(document["id"])
    results = await asyncio.gather(
        *(
            provider.stage_state_compare_and_set(key, "QUEUED", "IN_PROGRESS", {"workerId": f"w{i}", "updatedAtMs": 2})
            for i in range(CONCURRENT)
        )
    )
    assert results.count(True) == 1
    stored = await provider.stage_state_get(key)
    assert stored is not None and stored["status"] == "IN_PROGRESS"
    winner = results.index(True)
    assert stored["workerId"] == f"w{winner}"


@pytest.mark.asyncio
async def test_a_stored_state_round_trips_through_the_model(provider: IGraphDBProvider) -> None:
    document = _document(str(uuid.uuid4()))
    assert await provider.stage_state_create(document)
    stored = await provider.stage_state_get(str(document["id"]))
    assert stored is not None
    stored["key"] = stored.pop("id")
    state = StageState.model_validate(stored)
    assert state.tier is ParseTier.LIGHT and state.record_ids == ("rec-1",)


@pytest.mark.asyncio
async def test_stale_scan_returns_the_oldest_first(provider: IGraphDBProvider) -> None:
    vrid = str(uuid.uuid4())
    for stage, updated in (("classify", 30), ("entities", 10), ("summary-embed", 20)):
        document = _document(vrid, updated_at_ms=updated)
        document["id"] = f"{vrid}:rev-a:{stage}"
        document["stage"] = stage
        assert await provider.stage_state_create(document)
    stale = await provider.stage_states_stale(["QUEUED"], 25, 10)
    mine = [s["stage"] for s in stale if s["virtualRecordId"] == vrid]
    assert mine == ["entities", "summary-embed"]
    assert {s["stage"] for s in await provider.stage_states_for_revision(vrid, "rev-a")} == {
        "classify", "entities", "summary-embed",
    }


@pytest.mark.asyncio
async def test_record_fields_are_written_only_on_the_current_revision(provider: IGraphDBProvider) -> None:
    current, moved_on = f"rec-{uuid.uuid4()}", f"rec-{uuid.uuid4()}"
    await _insert_record(provider, current, "rev-a")
    await _insert_record(provider, moved_on, "rev-b")
    updated = await provider.compare_and_set_record_fields(
        [current, moved_on], "rev-a", {"extractionStatus": "COMPLETED"}
    )
    assert updated == [current]
