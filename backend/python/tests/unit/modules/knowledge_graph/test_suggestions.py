"""Unit tests for ``MergeSuggestionStore`` (KG Clean Rebuild plan, Phase 7)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.knowledge_graph.contracts.resolution import LLMAdjudication
from app.modules.knowledge_graph.governance.suggestions import MergeSuggestionStore


def _adjudication(local_id: str = "loc-1", candidate: str = "cand-1") -> LLMAdjudication:
    return LLMAdjudication(
        local_id=local_id, candidate_node_id=candidate, decision="merge",
        confidence=0.8, reason="looks like the same person",
    )


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.batch_upsert_nodes = AsyncMock(return_value=True)
    provider.get_nodes_by_filters = AsyncMock(return_value=[])
    provider.get_document = AsyncMock(return_value=None)
    provider.update_node = AsyncMock(return_value=True)
    return provider


@pytest.fixture
def store(graph_provider) -> MergeSuggestionStore:
    return MergeSuggestionStore(graph_provider, MagicMock())


class TestRecord:
    @pytest.mark.asyncio
    async def test_empty_org_id_returns_none_without_writing(self, store, graph_provider) -> None:
        result = await store.record("", _adjudication())

        assert result is None
        graph_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_pending_suggestion_and_returns_deterministic_id(self, store, graph_provider) -> None:
        result_1 = await store.record("org-1", _adjudication(), entity_type="person", entity_name="Jane")
        result_2 = await store.record("org-1", _adjudication(), entity_type="person", entity_name="Jane")

        assert result_1 is not None
        assert result_1 == result_2, "same org/local_id/candidate must produce the same suggestion id"
        doc = graph_provider.batch_upsert_nodes.call_args_list[0][0][0][0]
        assert doc["status"] == "pending"
        assert doc["orgId"] == "org-1"
        assert doc["llmDecision"] == "merge"
        assert doc["entityName"] == "Jane"

    @pytest.mark.asyncio
    async def test_storage_failure_is_non_fatal(self, store, graph_provider) -> None:
        graph_provider.batch_upsert_nodes.side_effect = RuntimeError("db down")

        result = await store.record("org-1", _adjudication())

        assert result is None


class TestListSuggestions:
    @pytest.mark.asyncio
    async def test_empty_org_id_returns_empty(self, store, graph_provider) -> None:
        result = await store.list_suggestions("")

        assert result == []
        graph_provider.get_nodes_by_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_defaults_to_pending_filter(self, store, graph_provider) -> None:
        await store.list_suggestions("org-1")

        graph_provider.get_nodes_by_filters.assert_awaited_once_with(
            "kgMergeSuggestions", {"orgId": "org-1", "status": "pending"},
        )

    @pytest.mark.asyncio
    async def test_status_none_returns_all_statuses(self, store, graph_provider) -> None:
        await store.list_suggestions("org-1", status=None)

        graph_provider.get_nodes_by_filters.assert_awaited_once_with(
            "kgMergeSuggestions", {"orgId": "org-1"},
        )

    @pytest.mark.asyncio
    async def test_sorts_newest_first_and_respects_limit(self, store, graph_provider) -> None:
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "a", "createdAtTimestamp": 100},
            {"id": "b", "createdAtTimestamp": 300},
            {"id": "c", "createdAtTimestamp": 200},
        ])

        result = await store.list_suggestions("org-1", limit=2)

        assert [r["id"] for r in result] == ["b", "c"]

    @pytest.mark.asyncio
    async def test_normalizes_arango_key_to_id(self, store, graph_provider) -> None:
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[
            {"_key": "sugg_abc", "createdAtTimestamp": 100},
        ])

        result = await store.list_suggestions("org-1")

        assert result[0]["id"] == "sugg_abc"

    @pytest.mark.asyncio
    async def test_query_failure_returns_empty(self, store, graph_provider) -> None:
        graph_provider.get_nodes_by_filters.side_effect = RuntimeError("boom")

        result = await store.list_suggestions("org-1")

        assert result == []


class TestResolve:
    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, store, graph_provider) -> None:
        graph_provider.get_document.return_value = None

        result = await store.resolve("org-1", "sugg-1", "approved")

        assert result is None
        graph_provider.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_org_returns_none(self, store, graph_provider) -> None:
        graph_provider.get_document.return_value = {"id": "sugg-1", "orgId": "org-2"}

        result = await store.resolve("org-1", "sugg-1", "approved")

        assert result is None
        graph_provider.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_resolved_and_returns_original_doc(self, store, graph_provider) -> None:
        doc = {"id": "sugg-1", "orgId": "org-1", "localId": "loc-1", "candidateNodeId": "cand-1"}
        graph_provider.get_document.return_value = doc

        result = await store.resolve("org-1", "sugg-1", "rejected", resolved_by="admin-1")

        assert result == doc
        graph_provider.update_node.assert_awaited_once()
        _, kwargs = graph_provider.update_node.call_args
        assert kwargs["key"] == "sugg-1"
        assert kwargs["node_updates"]["status"] == "rejected"
        assert kwargs["node_updates"]["resolvedBy"] == "admin-1"
