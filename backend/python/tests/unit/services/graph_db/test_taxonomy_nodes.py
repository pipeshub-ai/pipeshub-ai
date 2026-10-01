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

    async def test_create_if_absent_retries_a_write_conflict_outside_a_transaction(self, monkeypatch) -> None:
        """Records resolving the same new name create it at once; the insert
        fails with errorNum 1200 while another record's write holds the key."""
        from app.services.graph_db.arango import arango_http_provider as module

        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        p = _arango()
        conflict = RuntimeError("Batch insert failed with 1 error(s): Item 0: [1200] write-write conflict")
        p.http_client.batch_insert_documents = AsyncMock(side_effect=[conflict, conflict, {"errors": 0}])

        await p.create_taxonomy_node_if_absent(TOPICS, {"id": "k1"})
        assert p.http_client.batch_insert_documents.await_count == 3

        p.http_client.batch_insert_documents = AsyncMock(side_effect=conflict)
        with pytest.raises(RuntimeError):
            await p.create_taxonomy_node_if_absent(TOPICS, {"id": "k1"}, transaction="t1")
        assert p.http_client.batch_insert_documents.await_count == 1

    async def test_add_aliases_is_an_atomic_union_with_cap(self) -> None:
        p = _arango()
        await p.add_taxonomy_aliases(
            TOPICS, "k1", ["A", "a", "", "B"], ["a", "a", "", "b"], max_aliases=5, transaction="t1", org_id="org-1",
        )
        query = p.execute_query.await_args.args[0]
        assert "UNION_DISTINCT" not in query and f"IN {TOPICS}" in query
        assert "FILTER incoming_normals[i] NOT IN normals" in query
        assert "APPEND(displays, (FOR i IN fresh RETURN incoming_displays[i]))" in query
        assert "APPEND(normals, (FOR i IN fresh RETURN incoming_normals[i]))" in query
        assert p.execute_query.await_args.kwargs["bind_vars"] == {
            "key": "k1", "org_id": "org-1", "aliases": ["A", "B"], "normalized": ["a", "b"],
            "max_aliases": 5,
        }

    async def test_add_aliases_skips_the_update_when_nothing_changes(self) -> None:
        """An UPDATE locks a popular node for the rest of the record's
        transaction even when it writes the same lists back."""
        p = _arango()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        query = p.execute_query.await_args.args[0]
        guard = query.index("FILTER LENGTH(fresh) > 0")
        assert guard < query.index("UPDATE doc")
        assert "paired != LENGTH(stored_displays)" in query
        assert "paired != LENGTH(stored_normals)" in query

    async def test_add_aliases_retries_a_write_conflict_outside_a_transaction(self, monkeypatch) -> None:
        """Records resolving to one popular node add aliases to it at once;
        ArangoDB rejects all but one concurrent UPDATE with errorNum 1200."""
        from app.services.graph_db.arango import arango_http_provider as module

        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        p = _arango()
        conflict = RuntimeError('Query failed (status=409): {"errorMessage":"write-write conflict","errorNum":1200}')
        p.execute_query = AsyncMock(side_effect=[conflict, conflict, None])

        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")

        assert p.execute_query.await_count == 3

    async def test_add_aliases_gives_up_after_bounded_retries(self, monkeypatch) -> None:
        from app.services.graph_db.arango import arango_http_provider as module

        monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
        p = _arango()
        p.execute_query = AsyncMock(side_effect=RuntimeError('{"errorNum":1200}'))

        with pytest.raises(RuntimeError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        assert p.execute_query.await_count == module._WRITE_CONFLICT_ATTEMPTS

    async def test_add_aliases_does_not_retry_inside_a_transaction_or_other_errors(self) -> None:
        p = _arango()
        p.execute_query = AsyncMock(side_effect=RuntimeError('{"errorNum":1200}'))
        with pytest.raises(RuntimeError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], transaction="t1", org_id="org-1")
        assert p.execute_query.await_count == 1

        p.execute_query = AsyncMock(side_effect=RuntimeError('{"errorNum":1203}'))
        with pytest.raises(RuntimeError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        assert p.execute_query.await_count == 1

    async def test_add_aliases_noop_and_validation(self) -> None:
        p = _arango()
        await p.add_taxonomy_aliases(TOPICS, "k1", [], [], org_id="org-1")
        await p.add_taxonomy_aliases(TOPICS, "", ["a"], ["a"], org_id="org-1")
        p.execute_query.assert_not_awaited()
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases("records", "k1", ["a"], ["a"], org_id="org-1")
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["a", "b"], ["a"], org_id="org-1")

    async def test_add_aliases_only_touches_the_orgs_node(self) -> None:
        """A legacy node (no org) or another org's node must not collect this
        org's spellings; those aliases surface in other tenants' search."""
        p = _arango()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        query = p.execute_query.await_args.args[0]
        assert "FILTER doc._key == @key AND doc.orgId == @org_id" in query

    async def test_add_aliases_requires_an_org(self) -> None:
        p = _arango()
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="")
        p.execute_query.assert_not_awaited()

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
        assert "UNION" in query
        # Alias matches seek TaxonomyAlias nodes; scanning a list property on
        # every org node of the label grew with the org's taxonomy.
        assert "any(alias IN" not in query
        assert "MATCH (a:TaxonomyAlias)" in query
        assert "a.orgId = $org_id AND a.collection = $collection AND a.normalized IN $names" in query
        assert "MATCH (a)-[:ALIAS_OF]->(n:Topics)" in query
        assert p.client.execute_query.await_args.kwargs["parameters"] == {
            "org_id": "org-1", "collection": TOPICS, "names": ["bug"],
        }
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
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A", "a", "B"], ["a", "a", "b"], max_aliases=7, org_id="org-1")
        query, = p.client.execute_query.await_args.args
        assert "reduce(" not in query
        assert "WHERE NOT $normalized[i] IN normals] AS fresh" in query
        assert "(displays + [i IN fresh | $aliases[i]])[0..$max_aliases]" in query
        assert "(normals + [i IN fresh | $normalized[i]])[0..$max_aliases]" in query
        assert p.client.execute_query.await_args.kwargs["parameters"] == {
            "key": "k1", "org_id": "org-1", "collection": TOPICS, "aliases": ["A", "B"],
            "normalized": ["a", "b"], "max_aliases": 7,
        }

    async def test_add_aliases_locks_the_node_before_reading_it(self) -> None:
        """Reading the lists in WITH takes no lock, so two writers read the
        same lists and the later SET drops the other's alias."""
        p = _neo4j()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        query, = p.client.execute_query.await_args.args
        lock = query.index("SET n._aliasLock = true")
        assert lock < query.index("coalesce(n.aliases, [])")
        assert "REMOVE n._aliasLock" in query

    async def test_add_aliases_writes_indexed_alias_nodes_for_org_nodes(self) -> None:
        p = _neo4j()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        query, = p.client.execute_query.await_args.args
        # The match already pins the org, so every written node has one.
        assert "WHERE n.orgId = $org_id" in query
        assert "UNWIND n.normalizedAliases AS normalized" in query
        assert (
            "MERGE (a:TaxonomyAlias {orgId: n.orgId, collection: $collection, normalized: normalized})"
            in query
        )
        assert "MERGE (a)-[:ALIAS_OF]->(n)" in query

    async def test_add_aliases_only_touches_the_orgs_node(self) -> None:
        p = _neo4j()
        await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="org-1")
        query, = p.client.execute_query.await_args.args
        assert "MATCH (n:Topics {id: $key})\n            WHERE n.orgId = $org_id" in query
        assert query.index("WHERE n.orgId = $org_id") < query.index("SET n._aliasLock = true")

    async def test_add_aliases_requires_an_org(self) -> None:
        p = _neo4j()
        with pytest.raises(ValueError):
            await p.add_taxonomy_aliases(TOPICS, "k1", ["A"], ["a"], org_id="")
        p.client.execute_query.assert_not_awaited()

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

    def test_alias_nodes_have_a_composite_uniqueness_constraint(self) -> None:
        p = _neo4j()
        statements = p._generate_unique_id_constraints()
        assert any(
            "FOR (a:TaxonomyAlias) REQUIRE (a.orgId, a.collection, a.normalized) IS UNIQUE" in s
            for s in statements
        )


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
        await store.add_taxonomy_aliases(TOPICS, "k", ["A"], ["a"], max_aliases=3, org_id="org-1")
        provider.find_taxonomy_nodes.assert_awaited_once_with(TOPICS, "o", ["x"], transaction="txn-9")
        provider.create_taxonomy_node_if_absent.assert_awaited_once_with(TOPICS, {"id": "k"}, transaction="txn-9")
        provider.add_taxonomy_aliases.assert_awaited_once_with(
            TOPICS, "k", ["A"], ["a"], org_id="org-1", max_aliases=3, transaction="txn-9",
        )
