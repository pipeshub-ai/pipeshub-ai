"""Unit tests for ``BitemporalGraphWriter`` (KG Clean Rebuild plan, Phase 6).

These exercise the thin provider-agnostic seam only — the actual bi-temporal
write/read semantics are the graph provider's job and are covered by
``tests/unit/services/graph_db/test_bitemporal_edges.py``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.knowledge_graph.indexing.temporal import BitemporalGraphWriter, NodeRef


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.upsert_bitemporal_edge = AsyncMock(return_value={"edgeId": "e1"})
    provider.invalidate_bitemporal_edges = AsyncMock(return_value=1)
    provider.get_bitemporal_edges = AsyncMock(return_value=[{"edgeId": "e1"}])
    return provider


@pytest.fixture
def writer(graph_provider) -> BitemporalGraphWriter:
    return BitemporalGraphWriter(graph_provider, MagicMock())


class TestWriteEdge:
    @pytest.mark.asyncio
    async def test_forwards_node_refs_and_attributes(self, writer, graph_provider) -> None:
        subject = NodeRef("p1", "people")
        obj = NodeRef("p2", "people")

        result = await writer.write_edge("org-1", subject, obj, "SAME_AS", attributes={"k": "v"})

        assert result == {"edgeId": "e1"}
        graph_provider.upsert_bitemporal_edge.assert_awaited_once_with(
            org_id="org-1",
            from_id="p1", from_collection="people",
            to_id="p2", to_collection="people",
            edge_type="SAME_AS", attributes={"k": "v"}, valid_at=None,
        )

    @pytest.mark.asyncio
    async def test_propagates_provider_exception(self, writer, graph_provider) -> None:
        graph_provider.upsert_bitemporal_edge.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await writer.write_edge("org-1", NodeRef("p1", "people"), NodeRef("p2", "people"), "SAME_AS")


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_no_subject_or_obj_refuses(self, writer, graph_provider) -> None:
        result = await writer.invalidate("org-1")

        assert result == 0
        graph_provider.invalidate_bitemporal_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_subject_only_forwards_correctly(self, writer, graph_provider) -> None:
        result = await writer.invalidate("org-1", subject=NodeRef("p1", "people"), edge_type="SAME_AS")

        assert result == 1
        graph_provider.invalidate_bitemporal_edges.assert_awaited_once_with(
            org_id="org-1",
            from_id="p1", from_collection="people",
            to_id=None, to_collection=None,
            edge_type="SAME_AS", invalid_at=None,
        )


class TestGetAsOf:
    @pytest.mark.asyncio
    async def test_forwards_as_of_and_history_flags(self, writer, graph_provider) -> None:
        result = await writer.get_as_of(
            "org-1", subject=NodeRef("p1", "people"), as_of=1000, include_history=True, limit=10, offset=5,
        )

        assert result == [{"edgeId": "e1"}]
        graph_provider.get_bitemporal_edges.assert_awaited_once_with(
            org_id="org-1",
            from_id="p1", from_collection="people",
            to_id=None, to_collection=None,
            edge_type=None, as_of=1000, include_history=True, limit=10, offset=5,
        )
