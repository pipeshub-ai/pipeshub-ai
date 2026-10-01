"""Against real servers (KG-32): records enriched concurrently that share new
taxonomy nodes, including a new category -> subcategory chain, all succeed
and leave one hierarchy edge, on ArangoDB 3.12 and on Neo4j 5.26 with
explicit transactions on and off.

Enrichment runs the real ``GraphDBTransformer.save_metadata_to_db`` in its
own graph transaction per record, as the indexing service does.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_graph_write_concurrency.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import (
    CATEGORY,
    SUBCATEGORY_1,
    SUBCATEGORY_2,
    TOPIC,
    EntityResolution,
    ResolutionMode,
    ResolvedEntity,
    TaxonomyKind,
)
from app.modules.transformers.graphdb import GraphDBTransformer
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import (
    Neo4jProvider,
    collection_to_label,
    edge_collection_to_relationship,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "entity_graph_it"
WRITERS = 8
NAMES = {CATEGORY: "Finance", SUBCATEGORY_1: "Budgeting", SUBCATEGORY_2: "Forecasts", TOPIC: "Quarterly plan"}

logger = logging.getLogger("record-graph-write-it")


async def _open_neo4j(monkeypatch: pytest.MonkeyPatch, *, explicit: bool) -> Neo4jProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    monkeypatch.setenv("NEO4J_EXPLICIT_TRANSACTIONS", "true" if explicit else "false")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    await provider.ensure_schema()
    return provider


async def _open_arango() -> ArangoHTTPProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    await provider.ensure_schema()
    return provider


_TAXONOMY_COLLECTIONS = [k.collection for k in NAMES]
_EDGE_COLLECTIONS = [
    CollectionNames.BELONGS_TO_CATEGORY.value,
    CollectionNames.BELONGS_TO_TOPIC.value,
    CollectionNames.INTER_CATEGORY_RELATIONS.value,
]


async def _cleanup(provider: Neo4jProvider | ArangoHTTPProvider, org_id: str) -> None:
    if isinstance(provider, Neo4jProvider):
        await provider.client.execute_query(
            "MATCH (n) WHERE n.orgId = $org DETACH DELETE n", parameters={"org": org_id},
        )
        await provider.disconnect()
        return
    for edges in _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @org) OR CONTAINS(e._to, @org) REMOVE e IN {edges}",
            {"org": org_id},
        )
    for collection in (*_TAXONOMY_COLLECTIONS, CollectionNames.RECORDS.value):
        await provider.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id},
        )


@pytest.fixture(params=["arango", "neo4j-autocommit", "neo4j-explicit"])
async def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Any, str]]:
    open_ = {
        "arango": _open_arango,
        "neo4j-autocommit": lambda: _open_neo4j(monkeypatch, explicit=False),
        "neo4j-explicit": lambda: _open_neo4j(monkeypatch, explicit=True),
    }[request.param]
    try:
        provider = await open_()
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        await _cleanup(provider, org_id)


def _key(org_id: str, kind: TaxonomyKind) -> str:
    return taxonomy_node_key(org_id, kind.collection, NAMES[kind].casefold())


def _resolution(org_id: str, writer: int) -> EntityResolution:
    res = EntityResolution(org_id=org_id, mode=ResolutionMode.APPLY)
    for kind, name in NAMES.items():
        spelling = f"{name} {writer}"
        res.add(ResolvedEntity(
            kind=kind, key=_key(org_id, kind), name=name, normalized=name.casefold(),
            is_new=True, decision="new",
            aliases=[spelling], new_aliases=[spelling], extracted_names=[spelling],
        ))
    return res


async def _edges(provider: Neo4jProvider | ArangoHTTPProvider, collection: str, to: str) -> list[str]:
    if isinstance(provider, Neo4jProvider):
        rel = collection
        rows = await provider.client.execute_query(
            "MATCH (a)-[r]->(b {id: $to}) WHERE type(r) = $rel RETURN a.id AS id",
            parameters={"to": to.split("/", 1)[1], "rel": edge_collection_to_relationship(rel)},
        )
        return sorted(r["id"] for r in rows)
    rows = await provider.http_client.execute_aql(
        f"FOR e IN {collection} FILTER e._to == @to RETURN PARSE_IDENTIFIER(e._from).key", {"to": to},
    )
    return sorted(rows)


class TestConcurrentEnrichment:
    async def test_records_sharing_a_new_hierarchy_all_enrich_once(self, backend) -> None:
        provider, org_id = backend
        record_ids = [f"{org_id}-rec-{i}" for i in range(WRITERS)]
        await provider.batch_upsert_nodes([
            {
                "id": record_id, "orgId": org_id, "recordName": f"Doc {i}",
                "externalRecordId": record_id, "recordType": "FILE", "origin": "CONNECTOR",
                "connectorId": f"{org_id}-conn", "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            for i, record_id in enumerate(record_ids)
        ], CollectionNames.RECORDS.value)
        transformer = GraphDBTransformer(graph_provider=provider, logger=logger)

        def metadata() -> SemanticMetadata:
            return SemanticMetadata(
                categories=[NAMES[CATEGORY]], sub_category_level_1=NAMES[SUBCATEGORY_1],
                sub_category_level_2=NAMES[SUBCATEGORY_2], topics=[NAMES[TOPIC]],
                languages=[], departments=[],
            )

        results = await asyncio.gather(*(
            transformer.save_metadata_to_db(
                record_id, metadata(), f"vr-{record_id}", resolution=_resolution(org_id, i),
            )
            for i, record_id in enumerate(record_ids)
        ), return_exceptions=True)

        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, [repr(f)[:300] for f in failures]
        for kind in (CATEGORY, SUBCATEGORY_1, SUBCATEGORY_2):
            linked = await _edges(provider, CollectionNames.BELONGS_TO_CATEGORY.value, f"{kind.collection}/{_key(org_id, kind)}")
            assert linked == sorted(record_ids), kind.collection
        hierarchy = CollectionNames.INTER_CATEGORY_RELATIONS.value
        assert await _edges(provider, hierarchy, f"{CATEGORY.collection}/{_key(org_id, CATEGORY)}") == [
            _key(org_id, SUBCATEGORY_1)
        ]
        assert await _edges(provider, hierarchy, f"{SUBCATEGORY_1.collection}/{_key(org_id, SUBCATEGORY_1)}") == [
            _key(org_id, SUBCATEGORY_2)
        ]


class TestConcurrentDepartmentSeed:
    """KG-49: the indexing and connector services seed departments at start,
    often together; both must leave one global node per department."""

    async def test_two_seeds_at_once_leave_one_node_per_name(self, backend) -> None:
        provider, _ = backend
        departments = CollectionNames.DEPARTMENTS.value
        if isinstance(provider, Neo4jProvider):
            label = collection_to_label(departments)
            await provider.client.execute_query(f"MATCH (d:{label}) WHERE d.orgId IS NULL DETACH DELETE d")
            seed = provider._initialize_departments
        else:
            await provider.http_client.execute_aql(
                f"FOR d IN {departments} FILTER d.orgId == null REMOVE d IN {departments}", {},
            )
            seed = provider._ensure_departments_seed

        await asyncio.gather(seed(), seed(), seed())

        if isinstance(provider, Neo4jProvider):
            rows = await provider.client.execute_query(
                f"MATCH (d:{label}) WHERE d.orgId IS NULL RETURN d.departmentName AS name, count(*) AS n",
            )
            counts = {r["name"]: r["n"] for r in rows}
        else:
            rows = await provider.http_client.execute_aql(
                f"FOR d IN {departments} FILTER d.orgId == null "
                "COLLECT name = d.departmentName WITH COUNT INTO n RETURN {name, n}",
                {},
            )
            counts = {r["name"]: r["n"] for r in rows}
        assert counts and set(counts.values()) == {1}, counts
