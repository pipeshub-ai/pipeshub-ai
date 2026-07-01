"""
test_retrieval_with_filters.py — Unit tests for search_internal_knowledge with entity filters.

Matches plan section: "4. Retrieval Tool with Entity Filters (test_retrieval_with_filters.py)"
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_retrieval_tool(state: dict):
    """Build a Retrieval instance with a mocked retrieval_service."""
    from app.agents.actions.retrieval.retrieval import Retrieval

    r = Retrieval.__new__(Retrieval)
    r.state = state
    r.writer = None
    return r


def _state(
    retrieval_service=None,
    org_id: str = "org1",
    user_id: str = "user1",
) -> dict:
    if retrieval_service is None:
        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(
            return_value={"searchResults": []}
        )
    return {
        "org_id": org_id,
        "user_id": user_id,
        "retrieval_service": retrieval_service,
        "graph_provider": MagicMock(),
        "entity_vector_store": None,
        "logger": MagicMock(),
        "scope": None,
    }


def _mock_svc(results=None):
    """Return a mock service that yields searchResults (the actual key used in the code)."""
    svc = MagicMock()
    svc.search_with_filters = AsyncMock(
        return_value={"searchResults": results or []}
    )
    return svc


def _capture_filter_groups(svc):
    """Return the filter_groups kwarg passed to search_with_filters."""
    call = svc.search_with_filters.call_args
    if call is None:
        return {}
    return call.kwargs.get("filter_groups", {})


# ===========================================================================
# TestSearchInternalKnowledgeWithEntityFilters
# ===========================================================================


class TestSearchInternalKnowledgeWithEntityFilters:

    @pytest.mark.asyncio
    async def test_category_ids_passed_to_retrieval_service(self):
        """Pass category_ids -> forwarded to retrieval service filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="OAuth security",
            category_ids=["cat_security"],
        )

        fg = _capture_filter_groups(svc)
        assert "categories" in fg
        assert "cat_security" in fg["categories"]

    @pytest.mark.asyncio
    async def test_topic_ids_passed_to_retrieval_service(self):
        """Pass topic_ids -> forwarded as 'topics' in filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(query="auth design", topic_ids=["topic_oauth"])

        fg = _capture_filter_groups(svc)
        assert "topics" in fg
        assert "topic_oauth" in fg["topics"]

    @pytest.mark.asyncio
    async def test_department_ids_passed_to_retrieval_service(self):
        """Pass department_ids -> forwarded as 'departments' in filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(query="budgets", department_ids=["dept_finance"])

        fg = _capture_filter_groups(svc)
        assert "departments" in fg
        assert "dept_finance" in fg["departments"]

    @pytest.mark.asyncio
    async def test_people_ids_passed_to_retrieval_service(self):
        """Pass people_ids -> forwarded as 'people' in filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(query="assigned tickets", people_ids=["user_alice"])

        fg = _capture_filter_groups(svc)
        assert "people" in fg
        assert "user_alice" in fg["people"]

    @pytest.mark.asyncio
    async def test_combined_category_and_topic_filters(self):
        """Both category_ids and topic_ids present -> both forwarded."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="machine learning pipelines",
            category_ids=["cat_datascience"],
            topic_ids=["topic_ml"],
        )

        fg = _capture_filter_groups(svc)
        assert fg.get("categories") == ["cat_datascience"]
        assert fg.get("topics") == ["topic_ml"]

    @pytest.mark.asyncio
    async def test_combined_filters_with_time_range(self):
        """Entity filters and time range should co-exist in filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        # Use timezone-aware ISO 8601 timestamp as required by the parser
        await r.search_internal_knowledge(
            query="recent announcements",
            category_ids=["cat_hr"],
            created_after="2024-01-01T00:00:00+00:00",
        )

        fg = _capture_filter_groups(svc)
        assert "categories" in fg
        assert "cat_hr" in fg["categories"]
        call = svc.search_with_filters.call_args
        assert call is not None

    @pytest.mark.asyncio
    async def test_filter_with_no_entity_ids_does_not_add_filter_groups(self):
        """When no entity IDs are provided, filter_groups should not include empty lists."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(query="general knowledge base query")

        fg = _capture_filter_groups(svc)
        # None of the entity filter keys should appear with empty values
        for key in ("categories", "topics", "departments", "people"):
            assert key not in fg or bool(fg[key])

    @pytest.mark.asyncio
    async def test_filter_produces_zero_results_returns_empty_not_error(self):
        """Overly restrictive filter -> empty results, no exception."""
        svc = _mock_svc(results=[])
        r = _make_retrieval_tool(_state(svc))

        result = json.loads(
            await r.search_internal_knowledge(
                query="quantum physics",
                category_ids=["cat_legal"],  # unlikely to have quantum physics
            )
        )

        # No exception; zero results path returns result_count: 0
        assert result.get("status") == "success"
        assert result.get("result_count") == 0

    @pytest.mark.asyncio
    async def test_filters_combined_with_connector_ids(self):
        """topic_ids + connector_ids should both reach the retrieval service."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="design docs",
            topic_ids=["topic_design"],
            connector_ids=["conn_drive"],
        )

        assert svc.search_with_filters.called
        fg = _capture_filter_groups(svc)
        assert "topics" in fg
        assert fg["topics"] == ["topic_design"]

    @pytest.mark.asyncio
    async def test_none_entity_ids_are_not_forwarded(self):
        """Passing None for entity ID fields should not include those in filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="security",
            category_ids=None,
            topic_ids=None,
        )

        fg = _capture_filter_groups(svc)
        assert "categories" not in fg
        assert "topics" not in fg

    @pytest.mark.asyncio
    async def test_multiple_category_ids_all_forwarded(self):
        """Multiple category IDs should all appear in the filter."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="documents",
            category_ids=["cat_legal", "cat_finance", "cat_hr"],
        )

        fg = _capture_filter_groups(svc)
        assert set(fg["categories"]) == {"cat_legal", "cat_finance", "cat_hr"}

    @pytest.mark.asyncio
    async def test_service_is_called_with_query_and_entity_filters(self):
        """search_with_filters must be invoked with both query and entity filter_groups."""
        svc = _mock_svc()
        r = _make_retrieval_tool(_state(svc))

        await r.search_internal_knowledge(
            query="compliance documents",
            category_ids=["cat_legal"],
            topic_ids=["topic_gdpr"],
        )

        assert svc.search_with_filters.called
        call = svc.search_with_filters.call_args
        queries = call.kwargs.get("queries") or call.args[0]
        assert queries == ["compliance documents"]
        fg = call.kwargs.get("filter_groups", {})
        assert fg.get("categories") == ["cat_legal"]
        assert fg.get("topics") == ["topic_gdpr"]
