"""``get_nodes_by_field_prefix(return_fields=...)`` returns only those fields, so a
caller listing a whole repo's paths does not pull every row's full document."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ROW = {"_key": "rec-1", "filePath": "src/a.py"}


async def test_arango_returns_only_the_fields_asked_for() -> None:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(return_value=[ROW])
    rows = await p.get_nodes_by_field_prefix("codeFiles", "filePath", "src/",
                                             return_fields=["_key", "filePath"])
    assert rows == [ROW]
    query = p.http_client.execute_aql.call_args.args[0]
    assert "RETURN {_key: doc._key, filePath: doc.filePath}" in query

    await p.get_nodes_by_field_prefix("codeFiles", "filePath", "src/")
    assert "RETURN doc" in p.http_client.execute_aql.call_args.args[0]


async def test_neo4j_returns_only_the_fields_asked_for() -> None:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[ROW])
    rows = await p.get_nodes_by_field_prefix("codeFiles", "filePath", "src/",
                                             return_fields=["_key", "filePath"])
    assert [(r.get("_key"), r.get("filePath")) for r in rows] == [("rec-1", "src/a.py")]
    query = p.client.execute_query.call_args.args[0]
    assert "n.id AS _key" in query and "n.filePath AS filePath" in query
