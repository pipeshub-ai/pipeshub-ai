"""``update_indexing_status_for_record_ids`` on Neo4j: one statement, ids deduplicated, internal and placeholder records skipped."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.schema.node_validator import NodeSchemaValidator
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider() -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = logging.getLogger("t")
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    provider.validator = NodeSchemaValidator()
    return provider


async def test_the_ids_are_deduplicated_and_non_strings_dropped_and_one_statement_runs() -> None:
    provider = _provider()

    await provider.update_indexing_status_for_record_ids(["b", "a", "b", None, "", 3, "a"], "QUEUED")

    provider.client.execute_query.assert_awaited_once()
    call = provider.client.execute_query.await_args
    assert call.kwargs["parameters"]["ids"] == ["b", "a"]
    assert "isInternal" in call.args[0] and "isPlaceholder" in call.args[0]


async def test_an_empty_list_runs_nothing() -> None:
    provider = _provider()

    await provider.update_indexing_status_for_record_ids([], "QUEUED")

    provider.client.execute_query.assert_not_awaited()
