"""Unit tests for ``EntityMergeService`` (KG Clean Rebuild plan, Phase 7)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.knowledge_graph.governance.merge import EntityMergeService, MergeError
from app.modules.knowledge_graph.indexing.temporal import NodeRef


def _edge(from_id: str, from_collection: str, to_id: str, to_collection: str, edge_type: str = "SAME_AS") -> dict:
    return {
        "fromId": from_id, "fromCollection": from_collection,
        "toId": to_id, "toCollection": to_collection,
        "edgeType": edge_type, "attributes": {"k": "v"},
    }


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_bitemporal_edges = AsyncMock(return_value=[])
    provider.upsert_bitemporal_edge = AsyncMock(return_value={})
    provider.invalidate_bitemporal_edges = AsyncMock(return_value=0)
    provider.update_node = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def service(graph_provider) -> EntityMergeService:
    return EntityMergeService(graph_provider, MagicMock())


class TestMergeValidation:
    @pytest.mark.asyncio
    async def test_missing_org_id_raises(self, service) -> None:
        with pytest.raises(MergeError):
            await service.merge("", NodeRef("s1", "people"), NodeRef("d1", "people"))

    @pytest.mark.asyncio
    async def test_survivor_equals_duplicate_raises(self, service) -> None:
        with pytest.raises(MergeError):
            await service.merge("org-1", NodeRef("p1", "people"), NodeRef("p1", "people"))


class TestMergeRedirectsEdges:
    @pytest.mark.asyncio
    async def test_no_edges_still_marks_duplicate_merged(self, service, graph_provider) -> None:
        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result == {"survivorNodeId": "survivor", "duplicateNodeId": "dup", "edgesRedirected": 0}
        graph_provider.update_node.assert_awaited_once()
        _, kwargs = graph_provider.update_node.call_args
        assert kwargs["key"] == "dup"
        assert kwargs["collection"] == "people"
        assert kwargs["node_updates"]["mergedInto"] == "survivor"

    @pytest.mark.asyncio
    async def test_redirects_edge_where_duplicate_is_subject(self, service, graph_provider) -> None:
        # duplicate -[WORKS_WITH]-> other ; as-subject query returns it, as-object query returns nothing
        graph_provider.get_bitemporal_edges = AsyncMock(
            side_effect=[[_edge("dup", "people", "other", "people", "WORKS_WITH")], []]
        )

        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result["edgesRedirected"] == 1
        graph_provider.upsert_bitemporal_edge.assert_awaited_once_with(
            org_id="org-1",
            from_id="survivor", from_collection="people",
            to_id="other", to_collection="people",
            edge_type="WORKS_WITH", attributes={"k": "v"}, valid_at=None,
        )
        graph_provider.invalidate_bitemporal_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_redirects_edge_where_duplicate_is_object(self, service, graph_provider) -> None:
        # other -[MANAGES]-> duplicate ; as-object query returns it
        graph_provider.get_bitemporal_edges = AsyncMock(
            side_effect=[[], [_edge("other", "people", "dup", "people", "MANAGES")]]
        )

        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result["edgesRedirected"] == 1
        graph_provider.upsert_bitemporal_edge.assert_awaited_once_with(
            org_id="org-1",
            from_id="other", from_collection="people",
            to_id="survivor", to_collection="people",
            edge_type="MANAGES", attributes={"k": "v"}, valid_at=None,
        )

    @pytest.mark.asyncio
    async def test_self_loop_after_redirect_is_dropped(self, service, graph_provider) -> None:
        # duplicate already linked to survivor -- redirecting would create a self-loop
        graph_provider.get_bitemporal_edges = AsyncMock(
            side_effect=[[_edge("dup", "people", "survivor", "people", "SAME_AS")], []]
        )

        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result["edgesRedirected"] == 0
        graph_provider.upsert_bitemporal_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_edge_missing_endpoint_collection_is_skipped(self, service, graph_provider) -> None:
        bad_edge = {"fromId": "dup", "fromCollection": "people", "toId": "other", "edgeType": "X", "attributes": {}}
        graph_provider.get_bitemporal_edges = AsyncMock(side_effect=[[bad_edge], []])

        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result["edgesRedirected"] == 0
        graph_provider.upsert_bitemporal_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_failure_on_one_edge_does_not_abort_others(self, service, graph_provider) -> None:
        graph_provider.get_bitemporal_edges = AsyncMock(
            side_effect=[
                [
                    _edge("dup", "people", "other1", "people", "WORKS_WITH"),
                    _edge("dup", "people", "other2", "people", "WORKS_WITH"),
                ],
                [],
            ]
        )
        graph_provider.upsert_bitemporal_edge = AsyncMock(side_effect=[RuntimeError("boom"), {}])

        result = await service.merge("org-1", NodeRef("survivor", "people"), NodeRef("dup", "people"))

        assert result["edgesRedirected"] == 1
        assert graph_provider.upsert_bitemporal_edge.await_count == 2
