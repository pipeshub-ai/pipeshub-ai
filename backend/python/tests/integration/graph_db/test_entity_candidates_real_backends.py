"""Against real servers: ``get_entity_candidate_records`` reports a
capped scan, and sorts and pages only within it, on Neo4j 5.26 and ArangoDB
3.12. The unit tests pin the query text; only a server shows the queries
parse and return one row per ref.

The cap is lowered for the test so a handful of records exercises it.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_entity_candidates_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import Record, RecordType
from app.services.graph_db.arango import arango_http_provider as arango_module
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j import neo4j_provider as neo4j_module
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.common.utils import EntityCandidateRows

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "entity_graph_it"
TEST_CAP = 5
CONNECTOR = "conn-it"

logger = logging.getLogger("entity-candidates-it")


async def _open_neo4j(monkeypatch: pytest.MonkeyPatch) -> Neo4jProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    monkeypatch.setattr(neo4j_module, "ENTITY_CANDIDATE_SCAN_CAP", TEST_CAP)
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    return provider


async def _close_neo4j(provider: Neo4jProvider, org_id: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.orgId = $org DETACH DELETE n", parameters={"org": org_id},
    )
    await provider.disconnect()


async def _open_arango(monkeypatch: pytest.MonkeyPatch) -> ArangoHTTPProvider:
    monkeypatch.setattr(arango_module, "ENTITY_CANDIDATE_SCAN_CAP", TEST_CAP)
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    await provider.ensure_schema()
    return provider


async def _close_arango(provider: ArangoHTTPProvider, org_id: str) -> None:
    edges = CollectionNames.BELONGS_TO_TOPIC.value
    await provider.http_client.execute_aql(
        f"FOR e IN {edges} FILTER STARTS_WITH(e._from, CONCAT('records/', @org)) "
        f"REMOVE e IN {edges}",
        {"org": org_id},
    )
    for collection in (CollectionNames.TOPICS.value, CollectionNames.RECORDS.value):
        await provider.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}",
            {"org": org_id},
        )


async def _seed_neo4j(provider: Neo4jProvider, org_id: str, records: int) -> str:
    topic = f"{org_id}-topic"
    await provider.client.execute_query(
        """
        CREATE (t:Topics {id: $topic, name: 'Security', orgId: $org})
        WITH t
        UNWIND range(1, $n) AS i
        CREATE (r:Record {id: $org + '-r' + toString(i), orgId: $org, connectorId: $conn,
                          recordName: 'doc ' + toString(i), recordType: 'FILE',
                          isDeleted: false, indexingStatus: 'COMPLETED',
                          sourceLastModifiedTimestamp: i * 1000})
        CREATE (r)-[:BELONGS_TO_TOPIC]->(t)
        """,
        parameters={"topic": topic, "org": org_id, "n": records, "conn": CONNECTOR},
    )
    return topic


async def _seed_arango(provider: ArangoHTTPProvider, org_id: str, records: int) -> str:
    topic = f"{org_id}-topic"
    await provider.create_taxonomy_node_if_absent(CollectionNames.TOPICS.value, {
        "id": topic, "name": "Security", "normalizedName": "security", "orgId": org_id,
    })
    docs = []
    for i in range(1, records + 1):
        doc = Record(
            id=f"{org_id}-r{i}",
            org_id=org_id,
            record_name=f"doc {i}",
            record_type=RecordType.FILE,
            external_record_id=f"ext-{i}",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.KNOWLEDGE_BASE,
            connector_id=CONNECTOR,
            indexing_status=ProgressStatus.COMPLETED.value,
            source_updated_at=i * 1000,
        ).to_arango_base_record()
        docs.append(doc)
    await provider.http_client.execute_aql(
        f"""
        FOR doc IN @docs
            INSERT doc INTO {CollectionNames.RECORDS.value}
            INSERT {{_from: CONCAT('records/', doc._key), _to: CONCAT('topics/', @topic),
                    createdAtTimestamp: DATE_NOW()}}
                INTO {CollectionNames.BELONGS_TO_TOPIC.value}
        """,
        {"docs": docs, "topic": topic},
    )
    return topic


async def _candidates(
    provider: Neo4jProvider | ArangoHTTPProvider, org_id: str, topic: str, **kwargs: int,
) -> EntityCandidateRows:
    out = await provider.get_entity_candidate_records(
        [{"id": topic, "type": "topic", "connectorIds": [CONNECTOR]}], org_id, **kwargs,
    )
    return out[("topic", topic)]


@pytest.fixture(params=["neo4j", "arango"])
async def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Any, str, Any]]:
    opened = {"neo4j": (_open_neo4j, _close_neo4j, _seed_neo4j),
              "arango": (_open_arango, _close_arango, _seed_arango)}[request.param]
    open_, close, seed = opened
    try:
        provider = await open_(monkeypatch)
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id, seed
    finally:
        await close(provider, org_id)


class TestCappedCandidates:
    async def test_under_the_cap_is_not_capped_and_is_newest_first(self, backend) -> None:
        provider, org_id, seed = backend
        topic = await seed(provider, org_id, TEST_CAP - 2)
        rows = await _candidates(provider, org_id, topic, limit_per_entity=20)
        assert rows.capped is False
        assert [r["_key"] for r in rows] == [f"{org_id}-r{i}" for i in range(TEST_CAP - 2, 0, -1)]

    async def test_over_the_cap_is_capped_and_bounded(self, backend) -> None:
        provider, org_id, seed = backend
        topic = await seed(provider, org_id, TEST_CAP + 4)
        rows = await _candidates(provider, org_id, topic, limit_per_entity=20)
        assert rows.capped is True
        assert len(rows) == TEST_CAP
        stamps = [r["sourceLastModifiedTimestamp"] for r in rows]
        assert stamps == sorted(stamps, reverse=True)

    async def test_paging_past_the_window_is_empty_but_still_capped(self, backend) -> None:
        provider, org_id, seed = backend
        topic = await seed(provider, org_id, TEST_CAP + 4)
        rows = await _candidates(provider, org_id, topic, limit_per_entity=20, offset=TEST_CAP)
        assert rows == []
        assert rows.capped is True

    async def test_unknown_entity_returns_one_empty_uncapped_row(self, backend) -> None:
        provider, org_id, _ = backend
        rows = await _candidates(provider, org_id, f"{org_id}-missing")
        assert rows == []
        assert rows.capped is False


async def test_rows_carry_the_hide_url_flag(backend) -> None:
    """The listing tool must see ``hideWeburl`` to withhold a hidden link."""
    provider, org_id, seed = backend
    topic = await seed(provider, org_id, 2)
    assert await provider.update_node(f"{org_id}-r2", CollectionNames.RECORDS.value, {"hideWeburl": True})
    rows = {r["_key"]: r for r in await _candidates(provider, org_id, topic)}
    assert rows[f"{org_id}-r2"]["hideWeburl"] is True
    assert not rows[f"{org_id}-r1"].get("hideWeburl")
