"""Connect the production graph providers to the integration graph stores.

The backend-matrix workflow starts Neo4j and ArangoDB from
deployment/docker-compose/docker-compose.integration.graph-db.yml and sets
NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL and ARANGO_IT_PASSWORD; the
defaults below match that file, so a local run needs nothing exported.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

REQUIRED_WHEN_SET = {"neo4j": "NEO4J_IT_URI", "arango": "ARANGO_IT_URL"}

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")


async def connect_neo4j(logger: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    await provider.ensure_schema()
    return provider


async def connect_arango(logger: logging.Logger, database: str) -> IGraphDBProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": database}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    # Applies the strict collection schemas, so a document the product could not
    # write is refused here too.
    await provider.ensure_schema()
    return provider


def backend_unavailable(backend: str, error: BaseException) -> None:
    """Fail when the job said this backend is there (backend-matrix sets its URL); skip otherwise.

    The unit job collects these files on a runner with no graph, where a skip is
    right. Where the backend was configured, a skip would turn a broken backend
    into a green run.
    """
    variable = REQUIRED_WHEN_SET[backend]
    if os.environ.get(variable):
        pytest.fail(f"{backend} is configured ({variable}={os.environ[variable]}) but could not be used: {error!r}")
    pytest.skip(f"{backend} not available (set {variable} to require it): {error}")
