"""Connect the production graph providers to the integration graph stores.

The backend-matrix workflow starts Neo4j and ArangoDB from
deployment/docker-compose/docker-compose.integration.graph-db.yml and sets
NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL and ARANGO_IT_PASSWORD. A local
run exports the URL of each backend it means to use (the ports in that file are
bolt://localhost:17687 and http://localhost:18529); an unset one is skipped.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, NoReturn
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import GraphNames
from app.services.graph_db.arango.arango_http_provider import (
    EDGE_COLLECTIONS,
    NODE_COLLECTIONS,
    ArangoHTTPProvider,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support import it_backends

if TYPE_CHECKING:
    import logging

    import pytest

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

REQUIRED_WHEN_SET = {"neo4j": "NEO4J_IT_URI", "arango": "ARANGO_IT_URL"}

NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")


async def connect_neo4j(logger: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    neo4j_uri = it_backends.endpoint("NEO4J_IT_URI")
    monkeypatch.setenv("NEO4J_URI", neo4j_uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    if not await _neo4j_schema_present(provider):
        await provider.ensure_schema()
    return provider


async def _neo4j_schema_present(provider: Neo4jProvider) -> bool:
    """Whether the id and business-key constraints already exist.

    ensure_schema runs CREATE ... IF NOT EXISTS, which takes the schema lock even when it creates
    nothing. With two test workers on one store, that request queues behind the other worker's open
    transaction and blocks every later index read of that worker, and with it the transaction that is
    waiting for the read: a stall until the 120 s transaction timeout. A read of the existing schema
    takes no lock, so the DDL runs only against a store that lacks it.
    """
    wanted = {
        m.group(1)
        for q in provider._generate_unique_id_constraints() + provider._generate_business_key_constraints()
        if (m := re.search(r"CREATE CONSTRAINT (\w+)", q))
    }
    rows = await provider.client.execute_query("SHOW CONSTRAINTS YIELD name RETURN name")
    have = {r["name"] for r in rows}
    # A sync point key constraint is skipped while another connector's duplicates exist; do not wait for it.
    return (wanted - {"syncpoint_key_unique"}) <= have


async def connect_arango(logger: logging.Logger, database: str) -> IGraphDBProvider:
    arango_url = it_backends.endpoint("ARANGO_IT_URL")
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": arango_url, "username": "root", "password": ARANGO_PASSWORD, "db": database}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    # Applies the strict collection schemas, so a document the product could not
    # write is refused here too.
    if not await _arango_schema_present(provider):
        await provider.ensure_schema()
    return provider


async def _arango_schema_present(provider: ArangoHTTPProvider) -> bool:
    """Whether every collection exists with the validation rule the product applies.

    ensure_schema rewrites each collection's properties, which takes the collection's lock even when
    nothing changes. With two test workers on one store that request waits for the other worker's open
    transaction and holds every new transaction behind it: a stall until the stream transaction's idle
    timeout (60 s). Reading the properties takes no such lock, so the DDL runs only against a store that
    lacks the schema.
    """
    client = provider.http_client
    session = await client._get_session()
    for name, schema in [*NODE_COLLECTIONS, *EDGE_COLLECTIONS]:
        async with session.get(f"{client.base_url}/_db/{client.database}/_api/collection/{name}/properties") as resp:
            if resp.status != 200:
                return False
            have = (await resp.json()).get("schema") or {}
        if schema and have.get("rule") != schema.get("rule"):
            return False
    return await client.has_graph(GraphNames.KNOWLEDGE_GRAPH.value)


def backend_unavailable(backend: str, error: BaseException) -> NoReturn:
    """Fail when the job said this backend is there (backend-matrix sets its URL); skip otherwise."""
    it_backends.unavailable(REQUIRED_WHEN_SET[backend], error)
