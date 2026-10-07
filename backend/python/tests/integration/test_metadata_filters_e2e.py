"""Search metadata filters against a real Neo4j and a real ArangoDB.

A record is classified through the indexing path's own writer
(``GraphDBTransformer.save_metadata_to_db``), then each metadata filter the
search API accepts must find that record and not its neighbour. Both backends
run the same assertions, so a query that names the wrong node label on one of
them shows up as a missing record rather than an empty result nobody notices.

Needs Docker services, and skips cleanly when they are not reachable:

  docker run -d --name neo4j-it -p 17687:7687 -e NEO4J_AUTH=neo4j/ensure-it-pass neo4j:5.26.0
  docker run -d --name arango-it -p 18529:8529 -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12.4

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    DepartmentNames,
    ProgressStatus,
)
from app.models.blocks import SemanticMetadata
from app.modules.transformers.graphdb import GraphDBTransformer
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "metadata_filters_it"

logger = logging.getLogger("metadata-filters-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    org_id: str
    user_id: str
    connector_id: str
    suffix: str


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    # Seeds the default departments, as a fresh install does.
    await provider.ensure_schema()
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


async def _remove_test_data(graph: IGraphDBProvider, env: _Env) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id = $c OR n.orgId = $o "
            "OR (n.name IS NOT NULL AND n.name ENDS WITH $s) DETACH DELETE n",
            parameters={"c": env.connector_id, "o": env.org_id, "s": env.suffix},
        )
        return
    for edges in (
        CollectionNames.PERMISSION.value,
        CollectionNames.BELONGS_TO_DEPARTMENT.value,
        CollectionNames.BELONGS_TO_CATEGORY.value,
        CollectionNames.BELONGS_TO_LANGUAGE.value,
        CollectionNames.BELONGS_TO_TOPIC.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR r IN {CollectionNames.RECORDS.value} FILTER r.connectorId == @c "
            f"FOR e IN {edges} FILTER e._from == r._id OR e._to == r._id REMOVE e IN {edges}",
            {"c": env.connector_id},
        )
    classification = (
        CollectionNames.CATEGORIES.value,
        CollectionNames.SUBCATEGORIES1.value,
        CollectionNames.SUBCATEGORIES2.value,
        CollectionNames.SUBCATEGORIES3.value,
        CollectionNames.LANGUAGES.value,
        CollectionNames.TOPICS.value,
    )
    for collection in classification:
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER LIKE(d.name, CONCAT('%', @s)) "
            f"FOR e IN {CollectionNames.INTER_CATEGORY_RELATIONS.value} FILTER e._from == d._id "
            f"REMOVE e IN {CollectionNames.INTER_CATEGORY_RELATIONS.value}",
            {"s": env.suffix},
        )
    for collection in classification:
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER LIKE(d.name, CONCAT('%', @s)) REMOVE d IN {collection}",
            {"s": env.suffix},
        )
    for collection, field in (
        (CollectionNames.RECORDS.value, "connectorId"),
        (CollectionNames.APPS.value, "_key"),
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.{field} == @c REMOVE d IN {collection}",
            {"c": env.connector_id},
        )
    await graph.http_client.execute_aql(
        f"FOR d IN {CollectionNames.USERS.value} FILTER d.orgId == @o REMOVE d IN {CollectionNames.USERS.value}",
        {"o": env.org_id},
    )
    await graph.http_client.execute_aql(
        f"FOR d IN {CollectionNames.DEPARTMENTS.value} FILTER d.orgId == @o REMOVE d IN {CollectionNames.DEPARTMENTS.value}",
        {"o": env.org_id},
    )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        suffix = uuid.uuid4().hex[:10]
        environment = _Env(
            graph=graph,
            org_id=f"org-mdf-{suffix}",
            user_id=f"user-mdf-{suffix}",
            connector_id=f"jira-mdf-{suffix}",
            suffix=suffix,
        )
        cleanup.push_async_callback(_remove_test_data, graph, environment)
        await _seed_connector_and_user(environment)
        yield environment


async def _seed_connector_and_user(env: _Env) -> None:
    now = get_epoch_timestamp_in_ms()
    await env.graph.batch_upsert_nodes(
        [{
            "id": env.connector_id,
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
    await env.graph.batch_upsert_nodes(
        [{
            "id": env.user_id,
            "userId": env.user_id,
            "orgId": env.org_id,
            "email": f"{env.user_id}@example.com",
            "isActive": True,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.USERS.value,
    )


async def _readable_record(env: _Env, name: str) -> tuple[str, str]:
    """A COMPLETED Jira record the test user can read directly. Returns (record id, virtual id)."""
    now = get_epoch_timestamp_in_ms()
    record_id = str(uuid.uuid4())
    virtual_id = str(uuid.uuid4())
    await env.graph.batch_upsert_nodes(
        [{
            "id": record_id,
            "orgId": env.org_id,
            "recordName": name,
            "externalRecordId": f"issue-{record_id[:8]}",
            "recordType": "TICKET",
            "origin": "CONNECTOR",
            "connectorName": "JIRA",
            "connectorId": env.connector_id,
            "version": 0,
            "virtualRecordId": virtual_id,
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.RECORDS.value,
    )
    await env.graph.batch_create_edges(
        [{
            "from_id": env.user_id,
            "from_collection": CollectionNames.USERS.value,
            "to_id": record_id,
            "to_collection": CollectionNames.RECORDS.value,
            "type": "USER",
            "role": "READER",
            "createdAtTimestamp": now,
            "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.PERMISSION.value,
    )
    return record_id, virtual_id


def _classification(env: _Env, tag: str, department: DepartmentNames) -> SemanticMetadata:
    # Names carry the run's suffix so parallel runs on one database never share a node.
    return SemanticMetadata(
        departments=[department.value],
        categories=[f"category-{tag}-{env.suffix}"],
        sub_category_level_1=f"sub1-{tag}-{env.suffix}",
        sub_category_level_2=f"sub2-{tag}-{env.suffix}",
        sub_category_level_3=f"sub3-{tag}-{env.suffix}",
        languages=[f"language-{tag}-{env.suffix}"],
        topics=[f"topic-{tag}-{env.suffix}"],
    )


def _filters_matching(env: _Env, tag: str, department: DepartmentNames) -> dict[str, list[str]]:
    return {
        "departments": [department.value],
        "categories": [f"category-{tag}-{env.suffix}"],
        "subcategories1": [f"sub1-{tag}-{env.suffix}"],
        "subcategories2": [f"sub2-{tag}-{env.suffix}"],
        "subcategories3": [f"sub3-{tag}-{env.suffix}"],
        "languages": [f"language-{tag}-{env.suffix}"],
        "topics": [f"topic-{tag}-{env.suffix}"],
    }


async def test_each_metadata_filter_finds_the_classified_record_and_not_its_neighbour(env: _Env) -> None:
    legal_id, legal_vid = await _readable_record(env, "NDA with Acme")
    sales_id, sales_vid = await _readable_record(env, "Q3 pipeline review")
    transformer = GraphDBTransformer(env.graph, logger)
    await transformer.save_metadata_to_db(legal_id, _classification(env, "a", DepartmentNames.LEGAL), legal_vid)
    await transformer.save_metadata_to_db(sales_id, _classification(env, "b", DepartmentNames.SALES), sales_vid)

    unfiltered = await env.graph._get_virtual_ids_for_connector(env.user_id, env.org_id, env.connector_id, None)
    assert unfiltered == {legal_vid: legal_id, sales_vid: sales_id}, "the user can read both records"

    for key, values in _filters_matching(env, "a", DepartmentNames.LEGAL).items():
        found = await env.graph._get_virtual_ids_for_connector(
            env.user_id, env.org_id, env.connector_id, {key: values}
        )
        assert found == {legal_vid: legal_id}, f"filter {key}={values} should find only the Legal record"

    everything_at_once = await env.graph._get_virtual_ids_for_connector(
        env.user_id, env.org_id, env.connector_id, _filters_matching(env, "b", DepartmentNames.SALES)
    )
    assert everything_at_once == {sales_vid: sales_id}


async def test_departments_offered_for_classification_include_the_seeded_defaults_and_the_orgs_own(env: _Env) -> None:
    custom = f"Field Operations {env.suffix}"
    await env.graph.batch_upsert_nodes(
        [{"id": str(uuid.uuid4()), "departmentName": custom, "orgId": env.org_id}],
        collection=CollectionNames.DEPARTMENTS.value,
    )

    departments = await env.graph.get_departments(env.org_id)

    assert {d.value for d in DepartmentNames} <= set(departments)
    assert custom in departments
    assert custom not in await env.graph.get_departments(f"another-org-{env.suffix}")
