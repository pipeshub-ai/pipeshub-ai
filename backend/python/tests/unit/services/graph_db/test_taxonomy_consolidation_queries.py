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


def _neo4j_move(
    ids: list[str], *, target_org: str | None = "org-1", found: int = 1, stuck: bool = False,
) -> Neo4jProvider:
    """Answers the target check, then per batch the id lookup (at most a
    batch of the edges still on the node) and the move, which takes the
    moved edges off the node unless ``stuck``."""
    p = _neo4j()
    left = list(ids)

    async def answer(query: str, **kwargs: object) -> list[dict]:
        params: dict = kwargs["parameters"]  # type: ignore[assignment]
        if "RETURN count(t) AS n" in query:
            return [{"n": found, "orgId": target_org}]
        if "RETURN elementId(e) AS id" in query:
            return [{"id": i} for i in left[: params["batch"]]]
        if stuck:
            return [{"moved": 0}]
        for i in params["ids"]:
            left.remove(i)
        return [{"moved": len(params["ids"])}]

    p.client.execute_query = AsyncMock(side_effect=answer)
    return p


def _queries(p: Neo4jProvider) -> list[str]:
    return [c.args[0] for c in p.client.execute_query.await_args_list]


class TestMoveEdgesNeo4j:
    async def test_moves_org_edges_keeping_properties(self) -> None:
        p = _neo4j_move(["e1", "e2", "e3"])
        moved = await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        _, find, query, *_ = _queries(p)
        params = p.client.execute_query.await_args_list[2].kwargs["parameters"]
        assert moved == 3
        assert "MATCH (r:Record)-[e:BELONGS_TO_TOPIC]->(:Topics {id: $from_key})" in find
        assert "WHERE r.orgId = $org_id" in find
        # Each batch seeks its edges by id instead of expanding the hub again.
        assert "MATCH ()-[e:BELONGS_TO_TOPIC]->() WHERE elementId(e) IN $ids" in query
        assert "from:Topics AND from.id = $from_key AND r.orgId = $org_id" in query
        assert "($only_merged_from IS NULL OR e.mergedFrom = $only_merged_from)" in query
        assert "SET n = properties(e)" in query
        # A forward move keeps an edge's first origin; a restore sets it.
        assert "THEN coalesce(e.mergedFrom, $set_merged_from)" in query
        assert "n.mergedFrom = null" not in query  # only a legacy restore clears merge history
        assert "OPTIONAL MATCH (r)-[x:BELONGS_TO_TOPIC]->(target)" in query
        assert "WITH r, e, target, count(x) AS existing" in query
        assert params["ids"] == ["e1", "e2", "e3"]
        assert {k: params[k] for k in ("from_key", "to_key", "org_id", "set_merged_from", "only_merged_from")} == {
            "from_key": "a", "to_key": "b", "org_id": "org-1", "set_merged_from": "a", "only_merged_from": None,
        }

    async def test_a_hub_node_is_read_and_moved_a_batch_at_a_time(self) -> None:
        """Never every edge id at once: a hub's millions would sit in memory
        and in one result set."""
        p = _neo4j_move([f"e{i}" for i in range(10007)])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a") == 10007
        reads = [c for c in p.client.execute_query.await_args_list if "RETURN elementId(e) AS id" in c.args[0]]
        assert all("LIMIT $batch" in c.args[0] for c in reads)
        assert len(reads) == 4  # three batches, then the read that finds none
        moves = [c for c in p.client.execute_query.await_args_list if "WHERE elementId(e) IN $ids" in c.args[0]]
        assert [len(c.kwargs["parameters"]["ids"]) for c in moves] == [5000, 5000, 7]

    async def test_a_batch_that_moves_nothing_stops_the_loop(self) -> None:
        p = _neo4j_move(["e1", "e2"], stuck=True)
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a") == 0
        assert len(_queries(p)) == 3

    async def test_a_missing_target_is_refused_before_any_write(self) -> None:
        p = _neo4j_move(["e1"], found=0)
        with pytest.raises(ValueError, match="not found"):
            await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        assert p.client.execute_query.await_count == 1

    @pytest.mark.parametrize(("target_org", "provenance", "only"), [
        ("org-2", "mergedFrom", None),
        (None, "mergedFrom", None),
        (None, "migratedFrom", None),  # a migration goes to an org node, never a legacy one
        ("org-2", "migratedFrom", "b"),
        (None, "migratedFrom", "L"),  # migrated from another legacy node
    ])
    async def test_a_target_outside_the_org_is_refused(self, target_org, provenance, only) -> None:
        p = _neo4j_move(["e1"], target_org=target_org)
        with pytest.raises(ValueError, match="not a node of org"):
            await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from=None,
                                        only_merged_from=only, provenance=provenance)
        assert p.client.execute_query.await_count == 1

    async def test_migration_provenance_is_its_own_field(self) -> None:
        p = _neo4j_move(["e1"], target_org=None)  # back onto the legacy node
        await p.move_taxonomy_edges(TOPICS, "b", "L", "org-1", set_merged_from=None,
                                    only_merged_from="L", provenance="migratedFrom")
        _, find, query, *_ = _queries(p)
        assert "e.migratedFrom = $only_merged_from" in find
        assert "n.migratedFrom = CASE" in query and "n.mergedFrom = null" in query

    async def test_unknown_provenance_field_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            await _neo4j().move_taxonomy_edges(TOPICS, "a", "b", "o", set_merged_from="a", provenance="x}) DELETE e //")

    async def test_only_merged_from_restricts_the_edges(self) -> None:
        p = _neo4j_move(["e1"])
        await p.move_taxonomy_edges(TOPICS, "b", "a", "org-1", set_merged_from=None, only_merged_from="a")
        assert "($only_merged_from IS NULL OR e.mergedFrom = $only_merged_from)" in _queries(p)[1]

    async def test_nothing_to_move_writes_nothing(self) -> None:
        p = _neo4j_move([])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a") == 0
        assert p.client.execute_query.await_count == 2

    async def test_dry_run_only_counts(self) -> None:
        p = _neo4j([{"moved": 2}])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a", dry_run=True) == 2
        query = p.client.execute_query.await_args.args[0]
        assert "DELETE" not in query and "CREATE" not in query

    async def test_subcategory_uses_its_level_label(self) -> None:
        p = _neo4j_move(["e1"])
        await p.move_taxonomy_edges(SUB2, "a", "b", "org-1", set_merged_from="a")
        _, find, query, *_ = _queries(p)
        assert "-[e:BELONGS_TO_CATEGORY]->(:Subcategories2 {id: $from_key})" in find
        assert "from:Subcategories2" in query


def _arango_move(keys: list[str], *, target: dict | None = None) -> ArangoHTTPProvider:
    """Answers the target check, the key lookup, then dedupe and repoint per
    batch; dedupe drops the first key of each batch."""
    p = _arango()
    target = {"orgId": "org-1"} if target is None else target
    left = list(keys)

    async def answer(query: str, **kwargs: object) -> list:
        binds: dict = kwargs["bind_vars"]  # type: ignore[assignment]
        if "LET t = DOCUMENT(@to_id)" in query:
            return [target or None]
        if "RETURN e._key" in query:
            return left[: binds["batch"]]
        if "REMOVE e" in query:
            return [1]
        for key in binds["keys"]:  # the repoint takes the batch off the node
            left.remove(key)
        return [1] * (len(binds["keys"]) - 1)

    p.http_client.execute_aql = AsyncMock(side_effect=answer)
    return p


class TestMoveEdgesArango:
    async def test_drops_duplicates_then_repoints_the_rest(self) -> None:
        p = _arango_move(["k1", "k2", "k3"])
        moved = await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a")
        calls = p.http_client.execute_aql.await_args_list
        (_, find, dedupe, repoint, again) = (c.args[0] for c in calls)
        assert again == find
        assert moved == 3
        assert "FILTER e._to == @from_id" in find
        assert "FILTER rec != null AND rec.orgId == @org_id" in find
        assert "FILTER @only_merged_from == null OR e[@provenance] == @only_merged_from" in find
        assert "REMOVE e IN @@edges" in dedupe
        assert "d._from == e._from AND d._to == @to_id" in dedupe
        assert "NOT_NULL(e[@provenance], @set_merged_from)" in repoint
        for query in (dedupe, repoint):
            # By primary key, re-checked against the node the edge left.
            assert "FILTER e._key IN @keys AND e._to == @from_id" in query
            assert "DOCUMENT(e._from)" not in query
        assert "LIMIT @batch" in find
        binds = calls[3].kwargs["bind_vars"]
        assert binds["provenance"] == "mergedFrom" and binds["keys"] == ["k1", "k2", "k3"]
        assert binds["@edges"] == "belongsToTopic"
        assert binds["from_id"] == "topics/a" and binds["to_id"] == "topics/b"

    async def test_a_hub_node_is_read_and_moved_a_batch_at_a_time(self) -> None:
        p = _arango_move([f"k{i}" for i in range(10007)])
        assert await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from="a") == 10007
        calls = p.http_client.execute_aql.await_args_list
        assert sum("RETURN e._key" in c.args[0] for c in calls) == 4
        writes = [c for c in calls if "@keys" in c.args[0]]
        assert [len(c.kwargs["bind_vars"]["keys"]) for c in writes] == [5000, 5000, 5000, 5000, 7, 7]

    @pytest.mark.parametrize(("target", "provenance", "only", "error"), [
        ({}, "mergedFrom", None, "not found"),
        ({"orgId": "org-2"}, "mergedFrom", None, "not a node of org"),
        ({"orgId": None}, "mergedFrom", None, "not a node of org"),
    ])
    async def test_a_bad_target_is_refused_before_any_write(self, target, provenance, only, error) -> None:
        p = _arango_move(["k1"], target=target)
        with pytest.raises(ValueError, match=error):
            await p.move_taxonomy_edges(TOPICS, "a", "b", "org-1", set_merged_from=None,
                                        only_merged_from=only, provenance=provenance)
        assert p.http_client.execute_aql.await_count == 1

    async def test_a_migration_restore_may_land_on_the_legacy_node(self) -> None:
        p = _arango_move(["k1"], target={"orgId": None})
        assert await p.move_taxonomy_edges(TOPICS, "t", "L", "org-1", set_merged_from=None,
                                           only_merged_from="L", provenance="migratedFrom") == 1
        with pytest.raises(ValueError, match="not a node of org"):
            await p.move_taxonomy_edges(TOPICS, "t", "L2", "org-1", set_merged_from=None,
                                        only_merged_from="L", provenance="migratedFrom")

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
    """Legacy nodes (no orgId) predate per-org nodes and no longer grow, so
    pages walk them in key order and stop at the page size; walking the
    org's records instead repeats every record on every page."""

    async def test_neo4j(self) -> None:
        p = _neo4j([{"_key": "L", "name": "Pricing", "records": 4}])
        rows = await p.find_legacy_taxonomy_nodes(TOPICS, "org-1", 50)
        query = p.client.execute_query.await_args.args[0]
        params = p.client.execute_query.await_args.kwargs["parameters"]
        assert "MATCH (n:Topics) WHERE n.orgId IS NULL AND n.id > $after_key" in query
        assert "MATCH (r:Record {orgId: $org_id})-[:BELONGS_TO_TOPIC]->(n)" in query
        assert query.index("ORDER BY n.id") < query.index("WHERE records > 0") < query.index("LIMIT $limit")
        assert params["after_key"] == ""
        assert rows == [{"_key": "L", "name": "Pricing", "records": 4}]

    async def test_arango(self) -> None:
        p = _arango([{"_key": "L", "name": "Pricing", "records": 4}])
        await p.find_legacy_taxonomy_nodes(TOPICS, "org-1", 50, after_key="K")
        query = p.http_client.execute_aql.await_args.args[0]
        binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert "FOR node IN @@nodes" in query
        assert "FILTER node.orgId == null AND node._key > @after_key" in query
        assert "INBOUND node @@edges" in query and "rec.orgId == @org_id" in query
        assert query.index("SORT node._key") < query.index("FILTER records > 0") < query.index("LIMIT @limit")
        # Never null: a constant filter would let the optimizer drop the key bound.
        assert binds["after_key"] == "K" and binds["@nodes"] == TOPICS


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
