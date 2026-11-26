"""Record links the SQL connectors move from one record to another."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.sql.graph_links import move_incoming_relations, normalize_edge


class TestStandInReplacedByItsTable:
    async def test_the_foreign_keys_that_pointed_at_it_now_point_at_the_table(self) -> None:
        graph = MagicMock()
        graph.get_edges_to_node = AsyncMock(return_value=[
            {"_key": "e1", "_id": "recordLinks/e1", "_rev": "r", "_from": "records/orders",
             "_to": "records/stand-in", "relationshipType": "FOREIGN_KEY", "constraintName": "orders_customer_fk",
             "sourceColumn": "customer_id"},
        ])
        graph.batch_upsert_node_relations = AsyncMock(return_value=True)

        assert await move_incoming_relations(graph, from_record_id="stand-in", to_record_id="clients") == 1

        graph.get_edges_to_node.assert_awaited_once_with("records/stand-in", "recordLinks", raise_on_error=True)
        assert graph.batch_upsert_node_relations.await_args.args[0] == [{
            "_from": "records/orders", "_to": "records/clients",
            "relationshipType": "FOREIGN_KEY", "constraintName": "orders_customer_fk", "sourceColumn": "customer_id",
        }]

    async def test_nothing_pointing_at_it_writes_nothing(self) -> None:
        graph = MagicMock(get_edges_to_node=AsyncMock(return_value=[]), batch_upsert_node_relations=AsyncMock())

        assert await move_incoming_relations(graph, from_record_id="stand-in", to_record_id="clients") == 0
        graph.batch_upsert_node_relations.assert_not_awaited()

    async def test_neo4j_shaped_edges_move_too(self) -> None:
        graph = MagicMock()
        graph.get_edges_to_node = AsyncMock(return_value=[
            {"from_id": "orders", "to_id": "stand-in", "from_collection": "records",
             "to_collection": "records", "relationshipType": "FOREIGN_KEY"},
        ])
        graph.batch_upsert_node_relations = AsyncMock(return_value=True)

        assert await move_incoming_relations(graph, from_record_id="stand-in", to_record_id="clients") == 1

        assert graph.batch_upsert_node_relations.await_args.args[0] == [{
            "_from": "records/orders", "_to": "records/clients", "relationshipType": "FOREIGN_KEY",
        }]

    async def test_a_link_from_the_table_itself_is_not_turned_into_a_self_link(self) -> None:
        graph = MagicMock()
        graph.get_edges_to_node = AsyncMock(return_value=[
            {"_from": "records/clients", "_to": "records/stand-in", "relationshipType": "FOREIGN_KEY"},
        ])
        graph.batch_upsert_node_relations = AsyncMock()

        assert await move_incoming_relations(graph, from_record_id="stand-in", to_record_id="clients") == 0
        graph.batch_upsert_node_relations.assert_not_awaited()

    async def test_a_failed_read_raises_before_anything_is_written(self) -> None:
        # The caller deletes the stand-in next, and its foreign keys with it.
        graph = MagicMock(
            get_edges_to_node=AsyncMock(side_effect=RuntimeError("graph down")),
            batch_upsert_node_relations=AsyncMock(),
        )

        with pytest.raises(RuntimeError):
            await move_incoming_relations(graph, from_record_id="stand-in", to_record_id="clients")
        graph.batch_upsert_node_relations.assert_not_awaited()


class TestNormalizeEdge:
    def test_arango_ids_are_split_into_collection_and_key(self) -> None:
        assert normalize_edge({"_from": "records/a", "_to": "records/b", "x": 1}) == {
            "from_collection": "records", "from_id": "a", "to_collection": "records", "to_id": "b", "x": 1,
        }

    def test_an_edge_already_in_the_generic_shape_is_returned_as_is(self) -> None:
        edge = {"from_id": "a", "to_id": "b"}
        assert normalize_edge(edge) is edge
