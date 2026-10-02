"""Graph-side reads of the entity index rebuild, on both providers: which
app or org needs a pass, and keyset pages of each projected source.

The clients are mocked, so these pin query shape, parameters and the row
shape; ``tests/integration/graph_db/test_entity_index_sources_real_backends.py``
runs the same queries against real servers.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.entity_index_queries import ENTITY_INDEX_SOURCES
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
TOPICS = CollectionNames.TOPICS.value
DEPARTMENTS = CollectionNames.DEPARTMENTS.value


def _neo4j(rows: list | Exception) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


def _arango(rows: list | Exception) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


def _neo4j_call(p: Neo4jProvider) -> tuple[str, dict]:
    call = p.client.execute_query.await_args
    return call.args[0], call.kwargs.get("parameters") or {}


def _arango_call(p: ArangoHTTPProvider) -> tuple[str, dict]:
    call = p.http_client.execute_aql.await_args
    return call.args[0], call.kwargs.get("bind_vars") or {}


def test_sources_are_a_fixed_set() -> None:
    assert set(ENTITY_INDEX_SOURCES) == {
        RECORDS, GROUPS, DEPARTMENTS, TOPICS,
        CollectionNames.CATEGORIES.value, CollectionNames.LANGUAGES.value,
        CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value,
        CollectionNames.SUBCATEGORIES3.value, CollectionNames.USERS.value,
    }


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestValidation:
    async def test_unknown_source_is_rejected_before_querying(self, make) -> None:
        p = make([])
        with pytest.raises(ValueError):
            await p.page_entity_index_source("permissions", "org-1", None, 10)
        with pytest.raises(ValueError):
            await p.page_entity_index_source("topics) DETACH DELETE n //", "org-1", None, 10)

    async def test_unknown_candidate_collection_is_rejected(self, make) -> None:
        p = make([])
        with pytest.raises(ValueError):
            await p.get_entity_index_candidate("users", "v1:x")

    async def test_empty_scope_returns_nothing_without_querying(self, make) -> None:
        p = make([])
        assert await p.page_entity_index_source(TOPICS, "", None, 10) == []

    async def test_an_unknown_source_is_rejected_whatever_the_scope(self, make) -> None:
        p = make([])
        with pytest.raises(ValueError):
            await p.page_entity_index_source("users", "", None, 10)

    async def test_query_failure_raises(self, make) -> None:
        p = make(RuntimeError("db down"))
        with pytest.raises(RuntimeError, match="db down"):
            await p.page_entity_index_source(TOPICS, "org-1", None, 10)
        with pytest.raises(RuntimeError, match="db down"):
            await p.get_entity_index_candidate(APPS, "v1:x")


class TestNeo4j:
    async def test_app_candidate_skips_deleting_and_current_marker(self) -> None:
        p = _neo4j([{"n": {"id": "app-1", "name": "Drive"}}])
        doc = await p.get_entity_index_candidate(APPS, "v1:x")
        query, params = _neo4j_call(p)
        assert "MATCH (n:App)" in query and "DELETING" in query
        assert "coalesce(n.entityIndexState, '') <> $marker" in query
        assert params["marker"] == "v1:x"
        assert doc["_key"] == "app-1"

    async def test_org_candidate_includes_due_sweeps(self) -> None:
        p = _neo4j([])
        assert await p.get_entity_index_candidate(ORGS, "v1:x", sweep_before=123) is None
        query, params = _neo4j_call(p)
        assert "MATCH (n:Organization)" in query
        assert "n.entityIndexSweptAt IS NULL OR n.entityIndexSweptAt < $sweep_before" in query
        assert params["sweep_before"] == 123

    async def test_records_page_is_keyset_on_connector(self) -> None:
        p = _neo4j([{"_key": "r2", "name": "Doc", "orgId": "o", "recordGroupId": None,
                     "indexingStatus": "COMPLETED", "isDeleted": False}])
        rows = await p.page_entity_index_source(RECORDS, "app-1", "r1", 50)
        query, params = _neo4j_call(p)
        assert "MATCH (n:Record)" in query
        assert "n.connectorId = $scope_id" in query and "n.id > $after_key" in query
        assert query.index("ORDER BY n.id") < query.index("LIMIT $limit")
        assert params == {"scope_id": "app-1", "after_key": "r1", "limit": 50}
        assert rows[0]["_key"] == "r2"

    async def test_first_page_has_no_cursor_predicate(self) -> None:
        p = _neo4j([])
        await p.page_entity_index_source(RECORDS, "app-1", None, 50)
        query, params = _neo4j_call(p)
        assert "$after_key" not in query and "after_key" not in params

    async def test_taxonomy_page_is_canonical_nodes_of_the_org(self) -> None:
        p = _neo4j([])
        await p.page_entity_index_source(TOPICS, "org-1", None, 50)
        query, _ = _neo4j_call(p)
        assert "MATCH (n:Topics)" in query
        assert "n.orgId = $scope_id" in query and "n.normalizedName IS NOT NULL" in query
        assert "coalesce(n.aliases, [])" in query

    async def test_departments_include_global_ones(self) -> None:
        p = _neo4j([])
        await p.page_entity_index_source(DEPARTMENTS, "org-1", None, 50)
        query, _ = _neo4j_call(p)
        assert "(n.orgId = $scope_id OR n.orgId IS NULL)" in query
        assert "n.departmentName AS name" in query
        assert "normalizedName" not in query


class TestArango:
    async def test_app_candidate_skips_deleting_and_current_marker(self) -> None:
        p = _arango([{"_key": "app-1"}])
        doc = await p.get_entity_index_candidate(APPS, "v1:x")
        query, binds = _arango_call(p)
        assert f"FOR doc IN {APPS}" in query and "DELETING" in query
        assert "doc.entityIndexState != @marker" in query
        assert binds["marker"] == "v1:x"
        assert doc == {"_key": "app-1"}

    async def test_org_candidate_includes_due_sweeps(self) -> None:
        p = _arango([])
        await p.get_entity_index_candidate(ORGS, "v1:x", sweep_before=123)
        query, binds = _arango_call(p)
        assert f"FOR doc IN {ORGS}" in query
        assert "doc.entityIndexSweptAt == null OR doc.entityIndexSweptAt < @sweep_before" in query
        assert binds["sweep_before"] == 123

    async def test_records_page_is_keyset_on_connector(self) -> None:
        p = _arango([{"_key": "r2", "name": "Doc"}])
        rows = await p.page_entity_index_source(RECORDS, "app-1", "r1", 50)
        query, binds = _arango_call(p)
        assert f"FOR n IN {RECORDS}" in query
        assert "n.connectorId == @scope_id" in query and "n._key > @after_key" in query
        assert query.index("SORT n._key") < query.index("LIMIT @limit")
        assert binds == {"scope_id": "app-1", "after_key": "r1", "limit": 50}
        assert rows == [{"_key": "r2", "name": "Doc"}]

    async def test_taxonomy_page_is_canonical_nodes_of_the_org(self) -> None:
        p = _arango([])
        await p.page_entity_index_source(TOPICS, "org-1", None, 50)
        query, binds = _arango_call(p)
        assert f"FOR n IN {TOPICS}" in query
        assert "n.orgId == @scope_id" in query and "n.normalizedName != null" in query
        assert "after_key" not in binds

    async def test_departments_include_global_ones(self) -> None:
        p = _arango([])
        await p.page_entity_index_source(DEPARTMENTS, "org-1", None, 50)
        query, _ = _arango_call(p)
        assert "(n.orgId == @scope_id OR n.orgId == null)" in query
        assert "name: n.departmentName" in query


async def test_record_group_name_falls_back_like_the_index_path() -> None:
    """Index time names a group by ``groupName or name``; a different name
    would make the rebuild re-embed every group point."""
    neo, arango = _neo4j([]), _arango([])
    await neo.page_entity_index_source(GROUPS, "app-1", None, 10)
    await arango.page_entity_index_source(GROUPS, "app-1", None, 10)
    # ``or`` skips an empty groupName too, so the Cypher must not stop at "".
    assert "CASE WHEN coalesce(n.groupName, '') <> '' THEN n.groupName ELSE n.name END AS name" in _neo4j_call(neo)[0]
    assert "name: n.groupName || n.name" in _arango_call(arango)[0]


class TestKeysetIndexes:
    """Every source is paged by scope then key; without an index on both,
    each page filters and re-sorts the whole scope."""

    async def test_arango_indexes_every_source_on_scope_then_key(self) -> None:
        p = _arango([])
        p.http_client.ensure_persistent_index = AsyncMock()
        await p._ensure_indexes()
        made = {
            (c.args[0], tuple(c.args[1]))
            for c in p.http_client.ensure_persistent_index.await_args_list
        }
        missing = [
            (s.collection, s.scope_field) for s in ENTITY_INDEX_SOURCES.values()
            if (s.collection, (s.scope_field, "_key")) not in made
        ]
        assert missing == []

    def test_neo4j_indexes_every_source_on_scope_then_id(self) -> None:
        from app.config.constants.neo4j import collection_to_label

        made = " ".join(_neo4j([])._generate_performance_indexes())
        missing = [
            (s.collection, s.scope_field) for s in ENTITY_INDEX_SOURCES.values()
            if f"FOR (n:{collection_to_label(s.collection)}) ON (n.{s.scope_field}, n.id)" not in made
        ]
        assert missing == []
