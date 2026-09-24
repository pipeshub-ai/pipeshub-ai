"""One Cypher query for every record's edges, in both directions.

The per-record methods cost a query each per relation type per direction, so a
turn enriching its search hits spent 4x its hit count on round trips. What
matters here is that the batched form still splits parents from children
correctly -- getting that backwards mislabels every relation shown to the model.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows: list[dict] | Exception) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    if isinstance(rows, Exception):
        p.client.execute_query = AsyncMock(side_effect=rows)
    else:
        p.client.execute_query = AsyncMock(return_value=rows)
    return p


def _row(anchor: str, other: str, outgoing: bool, rel: str = "PARENT_CHILD") -> dict:
    return {
        "anchor_id": anchor, "record_id": other, "outgoing": outgoing,
        "relationType": rel, "parentTable": "", "childTable": "",
        "sourceColumn": "", "targetColumn": "",
    }


class TestBatchShape:
    @pytest.mark.asyncio
    async def test_all_records_cost_a_single_query(self) -> None:
        p = _provider([])
        await p.get_record_relations_batch(["a", "b", "c"], ["PARENT_CHILD", "ATTACHMENT"])
        assert p.client.execute_query.await_count == 1

    @pytest.mark.asyncio
    async def test_ids_and_relations_are_bound_as_parameters(self) -> None:
        p = _provider([])
        await p.get_record_relations_batch(["a", "b"], ["PARENT_CHILD"])
        params = p.client.execute_query.call_args.kwargs["parameters"]
        assert params == {"record_ids": ["a", "b"], "relation_types": ["PARENT_CHILD"]}

    @pytest.mark.asyncio
    async def test_every_requested_record_is_present_even_with_no_edges(self) -> None:
        """Callers index the result by record id; a missing key would KeyError."""
        p = _provider([])
        out = await p.get_record_relations_batch(["a", "b"], ["PARENT_CHILD"])
        assert out == {
            "a": {"parents": [], "children": []},
            "b": {"parents": [], "children": []},
        }

    @pytest.mark.asyncio
    async def test_empty_input_issues_no_query(self) -> None:
        p = _provider([])
        assert await p.get_record_relations_batch([], ["PARENT_CHILD"]) == {}
        assert await p.get_record_relations_batch(["a"], []) == {
            "a": {"parents": [], "children": []}
        }
        assert p.client.execute_query.await_count == 0


class TestDirection:
    @pytest.mark.asyncio
    async def test_outgoing_edges_become_parents_incoming_become_children(self) -> None:
        p = _provider([_row("a", "out", True), _row("a", "in", False)])
        out = await p.get_record_relations_batch(["a"], ["PARENT_CHILD"])
        assert [e["record_id"] for e in out["a"]["parents"]] == ["out"]
        assert [e["record_id"] for e in out["a"]["children"]] == ["in"]

    @pytest.mark.asyncio
    async def test_each_endpoint_of_a_shared_edge_is_anchored_separately(self) -> None:
        """When both ends are in the batch Neo4j returns the edge once per
        anchor; each must land in the opposite bucket."""
        p = _provider([_row("a", "b", True), _row("b", "a", False)])
        out = await p.get_record_relations_batch(["a", "b"], ["PARENT_CHILD"])
        assert out["a"]["parents"][0]["record_id"] == "b"
        assert out["b"]["children"][0]["record_id"] == "a"

    @pytest.mark.asyncio
    async def test_relation_type_is_carried_so_callers_can_label_edges(self) -> None:
        p = _provider([_row("a", "x", True, rel="ATTACHMENT")])
        out = await p.get_record_relations_batch(["a"], ["ATTACHMENT"])
        assert out["a"]["parents"][0]["relationType"] == "ATTACHMENT"

    @pytest.mark.asyncio
    async def test_rows_for_unrequested_anchors_are_ignored(self) -> None:
        p = _provider([_row("zzz", "x", True)])
        out = await p.get_record_relations_batch(["a"], ["PARENT_CHILD"])
        assert out == {"a": {"parents": [], "children": []}}


class TestFailure:
    @pytest.mark.asyncio
    async def test_query_failure_falls_back_to_the_per_record_path(self) -> None:
        """Losing relation context silently would strip metadata from answers,
        so a failed batch retries the way it worked before."""
        p = _provider(RuntimeError("boom"))
        p.get_parent_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "p1"}]
        )
        p.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])

        out = await p.get_record_relations_batch(["a"], ["PARENT_CHILD"])

        assert out["a"]["parents"] == [
            {"record_id": "p1", "relationType": "PARENT_CHILD"}
        ]
        assert p.get_parent_record_ids_by_relation_type.await_count == 1

    @pytest.mark.asyncio
    async def test_default_batch_skips_non_mapping_rows(self) -> None:
        from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

        p = _provider([])
        p.get_parent_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "p1"}, "bad-row", None]
        )
        p.get_child_record_ids_by_relation_type = AsyncMock(return_value=[42])

        # Call the interface default, not Neo4j's specialised batch query.
        out = await IGraphDBProvider.get_record_relations_batch(
            p, ["a"], ["PARENT_CHILD"]
        )

        assert out["a"]["parents"] == [
            {"record_id": "p1", "relationType": "PARENT_CHILD"}
        ]
        assert out["a"]["children"] == []
