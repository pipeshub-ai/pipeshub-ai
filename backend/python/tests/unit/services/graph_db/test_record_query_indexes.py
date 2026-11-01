"""The index the record query's seed of records not classified yet reads
(``docs/record-query.md``) is declared on both backends."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def test_neo4j_declares_org_and_extraction_status() -> None:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    assert any("FOR (n:Record) ON (n.orgId, n.extractionStatus)" in s
               for s in provider._generate_performance_indexes())


async def test_arango_declares_org_and_extraction_status() -> None:
    provider = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.http_client = AsyncMock()
    await provider._ensure_indexes()
    fields = [tuple(c.args[1]) for c in provider.http_client.ensure_persistent_index.await_args_list
              if c.args[0] == "records"]
    assert ("orgId", "extractionStatus") in fields
