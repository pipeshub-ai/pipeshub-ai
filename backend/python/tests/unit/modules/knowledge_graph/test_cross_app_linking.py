"""Unit tests for ``CrossAppEntityLinker`` (KG Clean Rebuild plan, Phase 6 /
Part H: "hard-key bridges on connector enable").
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.knowledge_graph.indexing.cross_app_linking import CrossAppEntityLinker


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.find_nodes_by_hard_key = AsyncMock(return_value=[])
    provider.upsert_bitemporal_edge = AsyncMock(return_value={"edgeId": "e1"})
    provider.get_users = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def linker(graph_provider) -> CrossAppEntityLinker:
    return CrossAppEntityLinker(graph_provider, MagicMock())


class TestLinkByHardKey:
    @pytest.mark.asyncio
    async def test_empty_org_id_or_value_short_circuits(self, linker, graph_provider) -> None:
        assert await linker.link_by_hard_key("", "a@b.com") == 0
        assert await linker.link_by_hard_key("org-1", "") == 0
        graph_provider.find_nodes_by_hard_key.assert_not_called()

    @pytest.mark.asyncio
    async def test_fewer_than_two_matches_writes_nothing(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
        ]

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        assert result == 0
        graph_provider.upsert_bitemporal_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_two_matches_writes_one_edge(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
            {"_key": "c1", "_collection": "contacts"},
        ]

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        assert result == 1
        graph_provider.upsert_bitemporal_edge.assert_awaited_once()
        kwargs = graph_provider.upsert_bitemporal_edge.call_args.kwargs
        assert kwargs["org_id"] == "org-1"
        assert kwargs["edge_type"] == "SAME_AS"
        assert kwargs["attributes"]["hardKeyValue"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_three_matches_writes_all_pairs(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
            {"_key": "c1", "_collection": "contacts"},
            {"_key": "c2", "_collection": "contacts2"},
        ]

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        # 3 nodes -> 3 pairs (u1-c1, u1-c2, c1-c2)
        assert result == 3
        assert graph_provider.upsert_bitemporal_edge.await_count == 3

    @pytest.mark.asyncio
    async def test_nodes_missing_id_or_collection_are_skipped(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
            {"_collection": "contacts"},  # missing _key/id
            {"_key": "c2"},  # missing _collection
        ]

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        assert result == 0
        graph_provider.upsert_bitemporal_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_lookup_failure_returns_zero(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.side_effect = RuntimeError("boom")

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        assert result == 0

    @pytest.mark.asyncio
    async def test_one_edge_write_failure_does_not_abort_remaining_pairs(self, linker, graph_provider) -> None:
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
            {"_key": "c1", "_collection": "contacts"},
            {"_key": "c2", "_collection": "contacts2"},
        ]
        graph_provider.upsert_bitemporal_edge.side_effect = [RuntimeError("boom"), {"edgeId": "e2"}, {"edgeId": "e3"}]

        result = await linker.link_by_hard_key("org-1", "a@b.com")

        assert result == 2


class TestLinkBatch:
    @pytest.mark.asyncio
    async def test_sums_results_across_values(self, linker, graph_provider) -> None:
        def _side_effect(org_id, collections, field, value, **kwargs) -> list[dict]:
            if value == "a@b.com":
                return [{"_key": "u1", "_collection": "users"}, {"_key": "c1", "_collection": "contacts"}]
            return []

        graph_provider.find_nodes_by_hard_key.side_effect = _side_effect

        result = await linker.link_batch("org-1", ["a@b.com", "nomatch@x.com"])

        assert result == 1

    @pytest.mark.asyncio
    async def test_one_bad_value_does_not_abort_batch(self, linker, graph_provider) -> None:
        async def _side_effect(org_id, collections, field, value, **kwargs) -> list[dict]:
            if value == "bad":
                raise RuntimeError("boom")
            return [{"_key": "u1", "_collection": "users"}, {"_key": "c1", "_collection": "contacts"}]

        graph_provider.find_nodes_by_hard_key.side_effect = _side_effect

        result = await linker.link_batch("org-1", ["bad", "a@b.com"])

        assert result == 1


class TestLinkOrgUsers:
    @pytest.mark.asyncio
    async def test_empty_org_id_short_circuits(self, linker, graph_provider) -> None:
        result = await linker.link_org_users("")

        assert result == 0
        graph_provider.get_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_users_returns_zero(self, linker, graph_provider) -> None:
        graph_provider.get_users.return_value = []

        result = await linker.link_org_users("org-1")

        assert result == 0

    @pytest.mark.asyncio
    async def test_get_users_failure_returns_zero(self, linker, graph_provider) -> None:
        graph_provider.get_users.side_effect = RuntimeError("boom")

        result = await linker.link_org_users("org-1")

        assert result == 0

    @pytest.mark.asyncio
    async def test_dedupes_and_lowercases_emails_then_links(self, linker, graph_provider) -> None:
        graph_provider.get_users.return_value = [
            {"email": "A@B.com"},
            {"email": "a@b.com"},  # duplicate, case-insensitive
            {"email": None},  # skipped
            {"no_email": True},  # skipped
        ]
        graph_provider.find_nodes_by_hard_key.return_value = [
            {"_key": "u1", "_collection": "users"},
            {"_key": "c1", "_collection": "contacts"},
        ]

        result = await linker.link_org_users("org-1")

        assert result == 1
        graph_provider.find_nodes_by_hard_key.assert_awaited_once()
        assert graph_provider.find_nodes_by_hard_key.call_args[0][3] == "a@b.com"

    @pytest.mark.asyncio
    async def test_truncates_to_max_users(self, linker, graph_provider) -> None:
        graph_provider.get_users.return_value = [{"email": f"u{i}@x.com"} for i in range(10)]

        await linker.link_org_users("org-1", max_users=3)

        assert graph_provider.find_nodes_by_hard_key.await_count == 3
