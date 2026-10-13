"""Edge counts can leave out the edges enrichment writes from what the LLM reads."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from helper import delete_footprint as fp
# Module imports: a class named Test* in this namespace would be collected as a test.
from helper.arango_test_provider import test_arango_provider as arango
from helper.neo4j_integration import test_neo4j_provider as neo4j

CLASSIFICATION_TYPES = ["BELONGS_TO_CATEGORY", "BELONGS_TO_DEPARTMENT", "BELONGS_TO_LANGUAGE", "BELONGS_TO_TOPIC"]


def test_the_classification_edges_are_the_ones_enrichment_writes() -> None:
    assert set(fp.CLASSIFICATION_EDGES) == {
        "belongsToDepartment", "belongsToCategory", "belongsToLanguage", "belongsToTopic",
    }


def _arango() -> arango.TestArangoHTTPProvider:
    provider = object.__new__(arango.TestArangoHTTPProvider)
    provider.http_client = AsyncMock()
    provider.http_client.has_collection.return_value = True
    provider.http_client.execute_aql.side_effect = lambda _q, binds: [f"{binds['@c']}/e1"]
    return provider


@pytest.mark.asyncio
async def test_arango_leaves_out_the_named_edge_collections() -> None:
    provider = _arango()
    every = await provider.count_edges_touching(["records/r1"])

    provider = _arango()
    without = await provider.count_edges_touching(["records/r1"], excluding=fp.CLASSIFICATION_EDGES)

    queried = {call.args[1]["@c"] for call in provider.http_client.execute_aql.await_args_list}
    assert not queried & set(fp.CLASSIFICATION_EDGES)
    assert every - without == len(fp.CLASSIFICATION_EDGES)


@pytest.mark.asyncio
async def test_neo4j_leaves_out_the_named_relationship_types() -> None:
    provider = object.__new__(neo4j.TestNeo4jProvider)
    provider.client = AsyncMock()
    provider.client.execute_query.return_value = [{"rels": ["e1", "e2"]}]

    count = await provider.count_edges_touching(["Record/r1"], excluding=fp.CLASSIFICATION_EDGES)

    query, params = provider.client.execute_query.await_args.args
    assert "NOT type(r) IN $skipped" in query
    assert params == {"ids": ["r1"], "skipped": CLASSIFICATION_TYPES}
    assert count == 2


@pytest.mark.asyncio
async def test_neo4j_counts_every_relationship_when_nothing_is_excluded() -> None:
    provider = object.__new__(neo4j.TestNeo4jProvider)
    provider.client = AsyncMock()
    provider.client.execute_query.return_value = [{"rels": ["e1"]}]

    await provider.count_edges_touching(["Record/r1"])

    assert provider.client.execute_query.await_args.args[1]["skipped"] == []
