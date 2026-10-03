"""Provider pieces of taxonomy consolidation (KG-33, B7), on both backends:
moving one org's record edges between taxonomy nodes, listing the legacy
nodes an org uses, and keeping merged-away nodes out of every lookup.

Clients are mocked; ``tests/integration/graph_db/test_taxonomy_consolidation_real_backends.py``
runs the same operations against real servers.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema.arango.edges import taxonomy_edge_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.entity_index_queries import (
    build_entity_index_source_page_aql,
    build_entity_index_source_page_cypher,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.taxonomy import (
    TAXONOMY_COLLECTIONS,
    TAXONOMY_EDGE_COLLECTIONS,
)

TOPICS = CollectionNames.TOPICS.value
SUB2 = CollectionNames.SUBCATEGORIES2.value


def _neo4j(rows: list | None = None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows or [])
    return p


def _arango(rows: list | None = None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(return_value=rows or [])
    p.execute_query = AsyncMock(return_value=rows or [])
    return p


def test_every_taxonomy_collection_has_its_record_edge() -> None:
    assert set(TAXONOMY_EDGE_COLLECTIONS) == set(TAXONOMY_COLLECTIONS)
    assert TAXONOMY_EDGE_COLLECTIONS[SUB2] == CollectionNames.BELONGS_TO_CATEGORY.value
    assert TAXONOMY_EDGE_COLLECTIONS[TOPICS] == CollectionNames.BELONGS_TO_TOPIC.value


def test_edge_schema_declares_merged_from() -> None:
    assert taxonomy_edge_schema["rule"]["properties"]["mergedFrom"] == {"type": ["string", "null"]}


class TestMoveEdgesNeo4j:
    async def test_moves_org_edges_keeping_properties(self) -> None:
        p = _neo4j([{"moved": 3, "n": 1}])
        moved = await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        query = p.client.execute_query.await_args.args[0]
        params = p.client.execute_query.await_args.kwargs["parameters"]
        assert moved == 3
        assert "MATCH (r:Record)-[e:BELONGS_TO_TOPIC]->(:Topics {id: $from_key})" in query
        assert "WHERE r.orgId = $org_id" in query
        assert "SET n = properties(e)" in query
        # A forward move keeps an edge's first origin; a restore sets it.
        assert "THEN coalesce(e.mergedFrom, $set_merged_from)" in query
        assert "n.mergedFrom = null" not in query  # only a legacy restore clears merge history
        assert "OPTIONAL MATCH (r)-[x:BELONGS_TO_TOPIC]->(target)" in query
        assert "WITH r, e, target, count(x) AS existing" in query
        assert query.index("WITH r, e LIMIT $batch") < query.index("DELETE e")
        assert params["batch"] == 5000
        assert {k: params[k] for k in ("from_key", "to_key", "org_id", "set_merged_from", "only_merged_from")} == {
            "from_key": "a", "to_key": "b", "org_id": "org-1", "set_merged_from": "a", "only_merged_from": None,
        }

    async def test_a_hub_node_is_moved_in_batches(self) -> None:
        p = _neo4j()
        p.client.execute_query = AsyncMock(
            side_effect=[[{"n": 1}], [{"moved": 5000}], [{"moved": 5000}], [{"moved": 7}]],
        )
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a") == 10007
        assert p.client.execute_query.await_count == 4

    async def test_a_missing_target_is_refused_before_any_write(self) -> None:
        p = _neo4j([{"n": 0}])
        with pytest.raises(ValueError, match="not found"):
            await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        assert p.client.execute_query.await_count == 1

    async def test_migration_provenance_is_its_own_field(self) -> None:
        p = _neo4j([{"moved": 1, "n": 1}])
        await p.move_taxonomy_edges(TOPICS, "b", "L", "org-1", set_merged_from=None,
                                    only_merged_from="L", provenance="migratedFrom")
        query = p.client.execute_query.await_args.args[0]
        assert "e.migratedFrom = $only_merged_from" in query
        assert "n.migratedFrom = CASE" in query and "n.mergedFrom = null" in query

    async def test_unknown_provenance_field_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            await _neo4j().move_taxonomy_edges(TOPICS, "a", "b", "o", set_merged_from="a", provenance="x}) DELETE e //")

    async def test_only_merged_from_restricts_the_edges(self) -> None:
        p = _neo4j([{"moved": 1, "n": 1}])
        await p.move_taxonomy_edges(TOPICS, "b", "a", "org-1", set_merged_from=None, only_merged_from="a")
        query = p.client.execute_query.await_args.args[0]
        assert "($only_merged_from IS NULL OR e.mergedFrom = $only_merged_from)" in query

    async def test_dry_run_only_counts(self) -> None:
        p = _neo4j([{"moved": 2}])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a", dry_run=True) == 2
        query = p.client.execute_query.await_args.args[0]
        assert "DELETE" not in query and "CREATE" not in query

    async def test_subcategory_uses_its_level_label(self) -> None:
        p = _neo4j([{"moved": 0, "n": 1}])
        await p.move_taxonomy_edges(SUB2, "a", "b", "org-1", set_merged_from="a")
        query = p.client.execute_query.await_args.args[0]
        assert "-[e:BELONGS_TO_CATEGORY]->(:Subcategories2 {id: $from_key})" in query


class TestMoveEdgesArango:
    async def test_drops_duplicates_then_repoints_the_rest(self) -> None:
        p = _arango()
        p.http_client.execute_aql = AsyncMock(side_effect=[[True], [1], [1, 1]])
        moved = await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        _, (dedupe, *_), (repoint, *_) = (c.args for c in p.http_client.execute_aql.await_args_list)
        assert moved == 3
        assert "REMOVE e IN @@edges" in dedupe
        assert "d._from == e._from AND d._to == @to_id" in dedupe
        assert "NOT_NULL(e[@provenance], @set_merged_from)" in repoint
        assert "LIMIT @batch" in dedupe and "LIMIT @batch" in repoint
        for query in (dedupe, repoint):
            assert "FILTER e._to == @from_id" in query
            assert "FILTER rec != null AND rec.orgId == @org_id" in query
            assert "FILTER @only_merged_from == null OR e[@provenance] == @only_merged_from" in query
        binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert binds["provenance"] == "mergedFrom"
        assert binds["@edges"] == "belongsToTopic"
        assert binds["from_id"] == "topics/a" and binds["to_id"] == "topics/b"

    async def test_dry_run_only_counts(self) -> None:
        p = _arango([2])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a", dry_run=True) == 2
        query = p.http_client.execute_aql.await_args.args[0]
        assert "REMOVE" not in query and "UPDATE" not in query and "COLLECT WITH COUNT" in query


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestMoveEdgesValidation:
    async def test_rejects_non_taxonomy_collection(self, make) -> None:
        with pytest.raises(ValueError):
            await make().move_taxonomy_edges("records", "a", "b", "org-1", set_merged_from="a")

    @pytest.mark.parametrize(("src", "dst", "org"), [("", "b", "o"), ("a", "", "o"), ("a", "b", ""), ("a", "a", "o")])
    async def test_rejects_missing_or_same_keys(self, make, src: str, dst: str, org: str) -> None:
        with pytest.raises(ValueError):
            await make().move_taxonomy_edges(TOPICS, src, dst, org, set_merged_from="a")


class TestLegacyNodes:
    async def test_neo4j(self) -> None:
        p = _neo4j([{"_key": "L", "name": "Pricing", "records": 4}])
        rows = await p.find_legacy_taxonomy_nodes(TOPICS, "org-1", 50)
        query = p.client.execute_query.await_args.args[0]
        assert "MATCH (r:Record {orgId: $org_id})-[:BELONGS_TO_TOPIC]->(n:Topics)" in query
        assert "WHERE n.orgId IS NULL AND ($after_key IS NULL OR n.id > $after_key)" in query
        assert rows == [{"_key": "L", "name": "Pricing", "records": 4}]

    async def test_arango(self) -> None:
        p = _arango([{"_key": "L", "name": "Pricing", "records": 4}])
        await p.find_legacy_taxonomy_nodes(TOPICS, "org-1", 50)
        query = p.http_client.execute_aql.await_args.args[0]
        # From the org's records out, not a scan of every org's edges.
        assert query.index("FILTER rec.orgId == @org_id") < query.index("OUTBOUND rec @@edges")
        assert "FILTER node.orgId == null" in query
        assert "AGGREGATE records = COUNT_DISTINCT(rec._key)" in query
        assert "FILTER @after_key == null OR node._key > @after_key" in query


class TestMergedNodesAreNotTargets:
    async def test_neo4j_tier0_lookup(self) -> None:
        p = _neo4j()
        await p.find_taxonomy_nodes(TOPICS, "org-1", ["x"])
        query = p.client.execute_query.await_args.args[0]
        assert query.count("n.mergedInto IS NULL") == 2

    async def test_arango_tier0_lookup(self) -> None:
        p = _arango()
        await p.find_taxonomy_nodes(TOPICS, "org-1", ["x"])
        query = p.execute_query.await_args.args[0]
        assert query.count("doc.mergedInto == null") == 2

    def test_entity_index_pages_skip_merged_nodes(self) -> None:
        assert "n.mergedInto == null" in build_entity_index_source_page_aql(TOPICS, has_after_key=False)
        assert "n.mergedInto IS NULL" in build_entity_index_source_page_cypher(TOPICS, has_after_key=False)
        assert "createdAtTimestamp" in build_entity_index_source_page_aql(TOPICS, has_after_key=False)
