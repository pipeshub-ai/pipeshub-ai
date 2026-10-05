"""Links between records live in their own collection on Arango:
nodeRelations carries hierarchy only, while shared code keeps addressing links
through it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

RELATIONS = CollectionNames.NODE_RELATIONS.value
LINKS = CollectionNames.RECORD_LINKS.value


def _provider(rows=None) -> tuple[ArangoHTTPProvider, list]:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    calls: list = []

    async def execute(query, bind_vars=None, txn_id=None) -> list:
        calls.append((query, bind_vars or {}))
        return rows.pop(0) if rows else []

    provider.http_client = MagicMock()
    provider.http_client.execute_aql = execute
    provider.http_client.has_collection = AsyncMock(return_value=True)
    return provider, calls


def _edge(to_id: str, relation_type: str | None) -> dict:
    edge = {"from_id": "a", "from_collection": "records", "to_id": to_id, "to_collection": "records"}
    if relation_type is not None:
        edge["relationshipType"] = relation_type
    return edge


def _targets(calls: list) -> dict[str, str]:
    """Which collection each written edge went to, by its target key."""
    return {
        e["_to"].split("/")[1]: bind.get("@collection") or bind.get("@links")
        for _, bind in calls for e in bind.get("edges", [])
    }


class TestWritesRouteByType:
    @pytest.mark.asyncio
    async def test_a_link_goes_to_record_links_and_hierarchy_stays(self) -> None:
        provider, calls = _provider()
        await provider.batch_create_edges(
            [_edge("child", "PARENT_CHILD"), _edge("blocked", "BLOCKS"), _edge("untyped", None)], RELATIONS,
        )
        assert _targets(calls) == {"child": RELATIONS, "untyped": RELATIONS, "blocked": LINKS}

    @pytest.mark.asyncio
    async def test_a_link_is_keyed_on_its_type_and_hierarchy_never_matches_a_link(self) -> None:
        provider, calls = _provider()
        await provider.batch_create_edges([_edge("child", "PARENT_CHILD"), _edge("blocked", "BLOCKS")], RELATIONS)
        hierarchy_query = next(q for q, b in calls if b.get("@collection") == RELATIONS)
        link_query = next(q for q, b in calls if b.get("@links") == LINKS)
        assert "CURRENT.relationshipType IN @hierarchy" in hierarchy_query
        assert "relationshipType: edge.relationshipType" in link_query

    @pytest.mark.asyncio
    async def test_other_collections_are_untouched(self) -> None:
        provider, calls = _provider()
        await provider.batch_create_edges([_edge("g", "BLOCKS")], CollectionNames.BELONGS_TO.value)
        assert _targets(calls) == {"g": CollectionNames.BELONGS_TO.value}

    @pytest.mark.asyncio
    async def test_foreign_keys_are_links(self) -> None:
        provider, calls = _provider()
        await provider.batch_upsert_node_relations([
            {"from_id": "t1", "from_collection": "records", "to_id": "t2", "to_collection": "records",
             "relationshipType": "FOREIGN_KEY", "constraintName": "fk"},
        ])
        assert _targets(calls) == {"t2": LINKS}


class TestReadsCoverBoth:
    def test_node_relations_cover_links(self) -> None:
        assert ArangoHTTPProvider._logical_edge_collections(RELATIONS) == [RELATIONS, LINKS]
        assert ArangoHTTPProvider._logical_edge_collections(CollectionNames.BELONGS_TO.value) == ["belongsTo"]

    @pytest.mark.asyncio
    async def test_link_cleanup_reaches_record_links(self) -> None:
        provider, calls = _provider(rows=[[{}], [{}, {}]])
        deleted = await provider.delete_edges_by_relationship_types("a", "records", RELATIONS, ["BLOCKS"])
        assert deleted == 3
        assert [q.split("FOR edge IN ")[1].split()[0] for q, _ in calls] == [RELATIONS, LINKS]

    @pytest.mark.asyncio
    async def test_the_edges_of_a_node_include_its_links(self) -> None:
        provider, calls = _provider(rows=[[{"relationshipType": "PARENT_CHILD"}], [{"relationshipType": "BLOCKS"}]])
        edges = await provider.get_edges_from_node("records/a", RELATIONS)
        assert [e["relationshipType"] for e in edges] == ["PARENT_CHILD", "BLOCKS"]


class TestMigration:
    @pytest.mark.asyncio
    async def test_one_scan_then_batches_keyed_like_the_upsert_then_a_check(self) -> None:
        provider, calls = _provider(rows=[["k1", "k2", "k3"], [1, 1], [1], [0]])
        assert await provider.split_link_edges(batch_size=2) == {"migrated": 3}
        scan, first, second, check = calls
        assert "NOT_NULL(e.relationshipType, e.relationType)" in scan[0]
        assert first[1]["keys"] == ["k1", "k2"] and second[1]["keys"] == ["k3"]
        assert "constraintName: cn" in first[0] and "REMOVE e IN @@nr" in first[0]
        assert "LIMIT 1" in check[0]

    @pytest.mark.asyncio
    async def test_a_link_left_behind_keeps_the_flag_unset(self) -> None:
        provider, _ = _provider(rows=[[], [1]])
        with pytest.raises(RuntimeError):
            await provider.split_link_edges()

    @pytest.mark.asyncio
    async def test_it_waits_for_the_collection(self) -> None:
        provider, calls = _provider()
        provider.http_client.has_collection = AsyncMock(return_value=False)
        with pytest.raises(RuntimeError):
            await provider.split_link_edges()
        assert not calls
