from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


FILTERS = {"orgId": "org-1", "recordGroupId": "repo-1"}
IN_FILTERS = {
    "indexingStatus": [
        ProgressStatus.NOT_STARTED.value,
        ProgressStatus.QUEUED.value,
        ProgressStatus.IN_PROGRESS.value,
    ]
}


@pytest.mark.asyncio
async def test_arango_existence_query_stops_after_first_match() -> None:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql.return_value = [1]

    exists = await provider.has_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters=FILTERS,
        in_filters=IN_FILTERS,
        transaction="txn-1",
    )

    assert exists is True
    query = provider.http_client.execute_aql.await_args.args[0]
    assert "LIMIT 1" in query
    assert "COLLECT WITH COUNT" not in query
    assert provider.http_client.execute_aql.await_args.kwargs == {
        "bind_vars": {
            "filter_orgId": "org-1",
            "filter_recordGroupId": "repo-1",
            "in_filter_indexingStatus": IN_FILTERS["indexingStatus"],
        },
        "txn_id": "txn-1",
    }


@pytest.mark.asyncio
async def test_neo4j_existence_query_stops_after_first_match() -> None:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query.return_value = [{"matched": 1}]

    exists = await provider.has_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters=FILTERS,
        in_filters=IN_FILTERS,
        transaction="txn-1",
    )

    assert exists is True
    query = provider.client.execute_query.await_args.args[0]
    assert "LIMIT 1" in query
    assert "count(node)" not in query
    assert provider.client.execute_query.await_args.kwargs == {
        "parameters": {
            "filter_orgId": "org-1",
            "filter_recordGroupId": "repo-1",
            "in_filter_indexingStatus": IN_FILTERS["indexingStatus"],
        },
        "txn_id": "txn-1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["arango", "neo4j"])
async def test_existence_query_returns_false_without_matches(
    provider_type: str,
) -> None:
    if provider_type == "arango":
        provider = ArangoHTTPProvider(MagicMock(), MagicMock())
        provider.http_client = AsyncMock()
        provider.http_client.execute_aql.return_value = []
    else:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        provider.client = AsyncMock()
        provider.client.execute_query.return_value = []

    assert (
        await provider.has_nodes_by_filters(
            collection=CollectionNames.RECORDS.value,
            filters=FILTERS,
            in_filters=IN_FILTERS,
        )
        is False
    )
