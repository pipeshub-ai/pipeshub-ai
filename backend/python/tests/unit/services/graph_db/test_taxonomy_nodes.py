"""Provider contract for canonical taxonomy nodes, on Arango and Neo4j, plus parity."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.taxonomy import TAXONOMY_COLLECTIONS, subcategory_level

TOPICS = CollectionNames.TOPICS.value
SUB2 = CollectionNames.SUBCATEGORIES2.value


def _arango(rows=None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.batch_insert_documents = AsyncMock(return_value={"created": 1, "errors": 0})
    p.execute_query = AsyncMock(return_value=rows or [])
    return p


def _neo4j(rows=None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows or [])
    return p


class TestArango:
    async def test_find_binds_org_and_deduped_names(self) -> None:
        p = _arango([{"id": "k1", "name": "Bug bash testing", "normalizedName": "bug bash testing", "aliases": []}])
        rows = await p.find_taxonomy_nodes(TOPICS, "org-1", ["bug bash testing", "bug bash testing", "x y"], transaction="t1")
        query = p.execute_query.await_args.args[0]
        assert "doc.orgId == @org_id" in query and "doc.normalizedName IN @names" in query
        assert "name IN doc.normalizedAliases" in query and "UNION_DISTINCT" in query
        assert "normalizedAliases: NOT_NULL(doc.normalizedAliases, [])" in query
        assert p.execute_query.await_args.kwargs["bind_vars"] == {"org_id": "org-1", "names": ["bug bash testing", "x y"]}
        assert p.execute_query.await_args.kwargs["transaction"] == "t1"
        assert rows[0]["id"] == "k1"

    @pytest.mark.parametrize(("collection", "org", "names"), [
        ("records", "org-1", ["x"]), (TOPICS, "", ["x"]), (TOPICS, "org-1", []),
    ])
    async def test_find_short_circuits(self, collection, org, names) -> None:
        p = _arango()
        assert await p.find_taxonomy_nodes(collection, org, names) == []
        p.execute_query.assert_not_awaited()

    async def test_find_propagates_errors(self) -> None:
        p = _arango()
        p.execute_query = AsyncMock(side_effect=RuntimeError("down"))
        with pytest.raises(RuntimeError):
            await p.find_taxonomy_nodes(TOPICS, "org-1", ["x y"])

    async def test_create_if_absent_uses_ignore_mode_and_strips_aliases(self) -> None:
        p = _arango()
        await p.create_taxonomy_node_if_absent(
            TOPICS, {"id": "k1", "name": "Bug", "normalizedName": "bug", "orgId": "o", "aliases": ["x"]}, transaction="t1",
        )
        args, kwargs = p.http_client.batch_insert_documents.await_args
        assert args[0] == TOPICS
        (doc,) = args[1]
        assert doc["_key"] == "k1" and "id" not in doc and "aliases" not in doc
        assert kwargs == {"txn_id": "t1", "overwrite": True, "overwrite_mode": "ignore"}

    async def test_create_if_absent_rejects_bad_input_and_errors(self) -> None:
        p = _arango()
        with pytest.raises(ValueError):
            await p.create_taxonomy_node_if_absent("records", {"id": "k1"})
        with pytest.raises(ValueError):
            await p.create_taxonomy_node_if_absent(TOPICS, {"name": "no id"})
        p.http_client.batch_insert_documents = AsyncMock(return_value={"errors": 1})
        with pytest.raises(RuntimeError):
            await p.create_taxonomy_node_if_absent(TOPICS, {"id": "k1"})

    async def test_add_aliases_is_an_atomic_union_with_cap(self) -> None:
        p = _arango()
        await p.add_taxonomy_aliases(
            TOPICS, "k1", ["A", "a", "", "B"], ["a", "a", "", "b"], max_aliases=5, transaction="t1",
        )
        query = p.execute_query.await_args.args[0]
        assert "UNION_DISTINCT" in query and "SLICE" in query and f"IN {TOPICS}" in query
        assert "normalizedAliases: SLICE" in query
        assert p.execute_query.await_args.kwargs["bind_vars"] == {
            "key": "k1", "aliases": ["A", "B"], "normalized": ["a", "b"], "max_aliases": 5,
        }

    async def test_add_aliases_noop_and_validation(self) -> None:
        p = _arango()
        await p.add_taxonomy_aliases(TOPICS, "k1", [], [])
        await p.add_taxonomy_aliases(TOPICS, "", ["a"], ["a"])
        p.execute_query.assert_not_awaited()
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases("records", "k1", ["a"], ["a"])
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["a", "b"], ["a"])

    async def test_ensure_indexes_covers_every_taxonomy_collection(self) -> None:
        p = _arango()
        p.http_client.ensure_persistent_index = AsyncMock()
        await p._ensure_indexes()
        by_fields = {}
        for call in p.http_client.ensure_persistent_index.await_args_list:
            by_fields.setdefault(tuple(call.args[1]), set()).add(call.args[0])
        assert by_fields[("orgId", "normalizedName")] == set(TAXONOMY_COLLECTIONS)
        assert by_fields[("orgId", "normalizedAliases[*]")] == set(TAXONOMY_COLLECTIONS)


class TestNeo4j:
    async def test_find_query_shape_and_binds(self) -> None:
        p = _neo4j([{"id": "k1", "name": "Bug", "normalizedName": "bug", "aliases": ["b"]}])
        rows = await p.find_taxonomy_nodes(TOPICS, "org-1", ["bug", "bug"], transaction="t1")
        query, = p.client.execute_query.await_args.args
        assert "MATCH (n:Topics)" in query
        assert "n.orgId = $org_id AND n.normalizedName IN $names" in query
        assert "UNION" in query and "any(alias IN coalesce(n.normalizedAliases, [])" in query
        assert p.client.execute_query.await_args.kwargs["parameters"] == {"org_id": "org-1", "names": ["bug"]}
        assert p.client.execute_query.await_args.kwargs["txn_id"] == "t1"
        assert rows == [{"id": "k1", "name": "Bug", "normalizedName": "bug", "aliases": ["b"]}]

    async def test_create_if_absent_merges_on_id_and_sets_only_on_create(self) -> None:
        p = _neo4j()
        await p.create_taxonomy_node_if_absent(TOPICS, {"id": "k1", "name": "Bug", "normalizedName": "bug", "orgId": "o", "aliases": ["x"]})
        query, = p.client.execute_query.await_args.args
        assert "MERGE (n:Topics {id: $id})" in query and "ON CREATE SET n += $props" in query
        params = p.client.execute_query.await_args.kwargs["parameters"]
        assert params["id"] == "k1"
        assert params["props"] == {"name": "Bug", "normalizedName": "bug", "orgId": "o"}

    async def test_add_aliases_dedupes_in_cypher_with_cap(self) -> None:
        p = _neo4j()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A", "a", "B"], ["a", "a", "b"], max_aliases=7)
        query, = p.client.execute_query.await_args.args
        assert "reduce(" in query and "[0..$max_aliases]" in query
        assert "n.normalizedAliases = reduce(" in query
        assert p.client.execute_query.await_args.kwargs["parameters"] == {
            "key": "k1", "aliases": ["A", "B"], "normalized": ["a", "b"], "max_aliases": 7,
        }

    async def test_missing_client_raises_and_bad_collection_rejected(self) -> None:
        p = _neo4j()
        p.client = None
        with pytest.raises(RuntimeError):
            await p.find_taxonomy_nodes(TOPICS, "org-1", ["x"])
        with pytest.raises(ValueError):
            await p.create_taxonomy_node_if_absent("records", {"id": "k"})

    def test_performance_indexes_cover_taxonomy_labels(self) -> None:
        p = _neo4j()
        statements = p._generate_performance_indexes()
        assert any("FOR (n:Topics) ON (n.orgId, n.normalizedName)" in s for s in statements)
        assert any("FOR (n:Subcategories3) ON (n.orgId, n.normalizedName)" in s for s in statements)
        assert any("FOR (n:Topics) ON (n.orgId)" in s for s in statements)


class TestParityAndPassthrough:
    async def test_both_providers_return_the_same_row_shape(self) -> None:
        row = {"id": "k1", "name": "Bug", "normalizedName": "bug", "aliases": []}
        assert await _arango([row]).find_taxonomy_nodes(TOPICS, "o", ["bug"]) == \
            await _neo4j([row]).find_taxonomy_nodes(TOPICS, "o", ["bug"])

    def test_subcategory_levels(self) -> None:
        assert subcategory_level(CollectionNames.SUBCATEGORIES1.value) == "1"
        assert subcategory_level(TOPICS) is None
        assert subcategory_level(None) is None

    async def test_transaction_store_forwards_the_transaction_id(self) -> None:
        provider = AsyncMock()
        provider.logger = MagicMock()
        store = GraphTransactionStore(provider, "txn-9")
        await store.find_taxonomy_nodes(TOPICS, "o", ["x"])
        await store.create_taxonomy_node_if_absent(TOPICS, {"id": "k"})
        await store.add_taxonomy_aliases(TOPICS, "k", ["A"], ["a"], max_aliases=3)
        provider.find_taxonomy_nodes.assert_awaited_once_with(TOPICS, "o", ["x"], transaction="txn-9")
        provider.create_taxonomy_node_if_absent.assert_awaited_once_with(TOPICS, {"id": "k"}, transaction="txn-9")
        provider.add_taxonomy_aliases.assert_awaited_once_with(TOPICS, "k", ["A"], ["a"], max_aliases=3, transaction="txn-9")
