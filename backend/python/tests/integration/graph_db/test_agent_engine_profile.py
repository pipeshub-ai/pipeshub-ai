"""AG1 ST-4 / ST-8: an engine agent's settings on a real Neo4j and a real ArangoDB.

Requires the servers of ``tests/integration/compose/graph-db.yml`` (or any Neo4j 5 and ArangoDB 3.12
at ``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); each backend is skipped while its variable is unset.
Run: NEO4J_TEST_URI=bolt://... ARANGO_TEST_URL=http://... pytest tests/integration/graph_db/test_agent_engine_profile.py -m integration

What only the real servers show: Neo4j refuses a map as a node property, and ArangoDB's PATCH merges a
stored object with the new one and enforces the collection schema, so a narrowing that drops a
dimension can only be written as text.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.utils import encode_agent_engine_profile
from tests.support import it_backends

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

AGENTS = CollectionNames.AGENT_INSTANCES.value


def _log():  # noqa: ANN202
    from app.utils.logger import create_logger

    return create_logger("graph_db_integration_test")


@pytest.fixture(scope="module")
async def neo4j_provider() -> AsyncIterator[Neo4jProvider]:
    pytest.importorskip("neo4j", reason="neo4j driver not installed")
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    uri = it_backends.endpoint("NEO4J_TEST_URI")
    password = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")
    client = Neo4jClient(uri=uri, username="neo4j", password=password, database="neo4j", logger=_log())
    try:
        if not await client.connect():
            pytest.skip(f"Neo4j not available at {uri}")
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {uri} — {exc}")
    provider = Neo4jProvider(logger=_log(), config_service=MagicMock())
    provider.client = client
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    yield provider
    await client.disconnect()


@pytest.fixture(scope="module")
async def arango_provider() -> AsyncIterator[ArangoHTTPProvider]:
    pytest.importorskip("aiohttp", reason="aiohttp not installed")
    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    url = it_backends.endpoint("ARANGO_TEST_URL")
    password = os.environ.get("ARANGO_TEST_PASSWORD", "testpassword")
    db = f"ag1_{uuid.uuid4().hex[:8]}"
    system = ArangoHTTPClient(base_url=url, username="root", password=password, database="_system", logger=_log())
    try:
        if not await system.connect() or not await system.create_database(db):
            pytest.skip(f"ArangoDB not available at {url}")
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {url} — {exc}")
    provider = ArangoHTTPProvider(logger=_log(), config_service=MagicMock())
    provider.http_client = ArangoHTTPClient(base_url=url, username="root", password=password, database=db, logger=_log())
    # The production path: every collection, with its schema, as the connector service creates them.
    assert await provider.ensure_schema()
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    yield provider
    await provider.http_client.disconnect()
    await system.disconnect()


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> IGraphDBProvider:
    return request.getfixturevalue(f"{request.param}_provider")


def _agent(key: str, narrowing: dict[str, Any] | None, account: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_key": key,
        "name": "Engine agent",
        "description": "d",
        "startMessage": "s",
        "systemPrompt": "p",
        "models": [],
        "createdBy": "u1",
        "createdAtTimestamp": 1,
        "updatedAtTimestamp": 1,
        "isDeleted": False,
    }
    if narrowing is not None:
        document["engineProfile"] = encode_agent_engine_profile(narrowing)
    if account is not None:
        document["modelAccountId"] = account
    return document


async def test_st8_a_created_engine_agent_reads_back_as_written(provider: IGraphDBProvider) -> None:
    key = f"ag1-{uuid.uuid4().hex[:10]}"
    narrowing = {"accountKinds": ["org_cloud"], "limits": {"maxSteps": 20}, "mode": "plan"}
    await provider.batch_upsert_nodes([_agent(key, narrowing, "cloud/acc-1")], AGENTS)
    agent = await provider.get_agent(key)
    assert agent is not None
    assert (agent["engineProfile"], agent["modelAccountId"]) == (narrowing, "cloud/acc-1")
    listed = await provider.get_all_agents("nobody", "no-org")
    assert all(not isinstance(a.get("engineProfile"), str) for a in listed if isinstance(a, dict))


async def test_st4_a_narrowing_that_drops_a_dimension_replaces_the_stored_one(provider: IGraphDBProvider) -> None:
    key = f"ag1-{uuid.uuid4().hex[:10]}"
    await provider.batch_upsert_nodes([_agent(key, {"accountKinds": ["user_api_key"], "mode": "plan"})], AGENTS)
    assert await provider.update_agent(key, {"engineProfile": {"mode": "plan"}}, "u1", "o1")
    assert (await provider.get_agent(key))["engineProfile"] == {"mode": "plan"}
    assert await provider.update_agent(key, {"engineProfile": None, "modelAccountId": None}, "u1", "o1")
    agent = await provider.get_agent(key)
    assert (agent.get("engineProfile"), agent.get("modelAccountId")) == (None, None)


async def test_st5_a_loop_agent_is_untouched_by_an_unrelated_update(provider: IGraphDBProvider) -> None:
    key = f"ag1-{uuid.uuid4().hex[:10]}"
    await provider.batch_upsert_nodes([_agent(key, None)], AGENTS)
    assert await provider.update_agent(key, {"name": "Renamed"}, "u1", "o1")
    agent = await provider.get_agent(key)
    assert agent["name"] == "Renamed" and "engineProfile" not in agent


async def test_mu6_neo4j_cannot_hold_the_narrowing_as_a_map(neo4j_provider: Neo4jProvider) -> None:
    key = f"ag1-{uuid.uuid4().hex[:10]}"
    await neo4j_provider.batch_upsert_nodes([_agent(key, None)], AGENTS)
    with pytest.raises(Exception, match="(?i)map|property values"):
        await neo4j_provider.client.execute_query(
            "MATCH (n:AgentInstance {id: $key}) SET n.engineProfile = $value RETURN n",
            parameters={"key": key, "value": {"mode": "plan"}},
        )


async def test_mu6_arango_refuses_the_narrowing_as_an_object_and_would_merge_it(arango_provider: ArangoHTTPProvider) -> None:
    key = f"ag1-{uuid.uuid4().hex[:10]}"
    await arango_provider.batch_upsert_nodes([_agent(key, None)], AGENTS)
    with pytest.raises(Exception, match="(?i)schema"):
        await arango_provider.http_client.update_document(AGENTS, key, {"engineProfile": {"mode": "plan"}})
