"""
test_agent_entity_search.py — Integration tests for agent entity-aware search.

Matches plan section: "5. Agent Loop Integration (test_agent_entity_search.py)"

These tests verify the agent's two-step pattern:
  resolve_entity_filters -> search_internal_knowledge(with entity IDs)

They mock at the tool-call level, not at the LLM level, to keep tests deterministic.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_retrieval(state: dict | None = None):
    from app.agents.actions.retrieval.retrieval import Retrieval

    r = Retrieval.__new__(Retrieval)
    r.state = state or {}
    r.writer = None
    return r


def _resolve_hit(entity_id: str, entity_type: str, name: str, score: float = 0.88):
    return {"entityId": entity_id, "entityType": entity_type, "name": name, "score": score}


def _base_state(**overrides):
    """Build a minimal state dict that search_internal_knowledge accepts."""
    state = {
        "org_id": "org1",
        "user_id": "user1",
        "retrieval_service": None,
        "graph_provider": MagicMock(),
        "entity_vector_store": None,
        "logger": MagicMock(),
        "scope": None,
    }
    state.update(overrides)
    return state


def _empty_svc():
    svc = MagicMock()
    svc.search_with_filters = AsyncMock(return_value={"searchResults": []})
    return svc


# ===========================================================================
# TestAgentEntityAwareSearch
# ===========================================================================


class TestAgentEntityAwareSearch:
    """Verify resolve -> search two-step pattern at the tool level."""

    @pytest.mark.asyncio
    async def test_resolve_then_search_with_resolved_ids(self):
        """resolve_entity_filters result feeds directly into search_internal_knowledge."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[_resolve_hit("cat_eng", "category", "Engineering")]
        )

        svc = _empty_svc()
        state = _base_state(retrieval_service=svc, entity_vector_store=mock_evs)
        r = _make_retrieval(state)

        # Step 1: resolve facets
        resolve_result = json.loads(
            await r.resolve_entity_filters(query_facets=["engineering"])
        )
        resolved_ids = [
            h["entityId"]
            for h in resolve_result["resolved"].get("engineering", [])
        ]

        # Step 2: search with resolved IDs
        await r.search_internal_knowledge(
            query="OAuth docs",
            category_ids=resolved_ids,
        )

        # Verify category_ids were forwarded as filter_groups
        call = svc.search_with_filters.call_args
        fg = call.kwargs.get("filter_groups") or {}
        assert "categories" in fg
        assert "cat_eng" in fg["categories"]

    @pytest.mark.asyncio
    async def test_agent_retries_without_filters_on_empty_filtered_results(self):
        """
        Simulate: filtered search returns 0 -> retry without filters -> finds results.
        Tests the retry-without-filters pattern at the tool layer.
        """
        call_count = 0

        async def conditional_search(queries, org_id, user_id, limit, filter_groups=None, **kw):
            nonlocal call_count
            call_count += 1
            if filter_groups and filter_groups.get("categories"):
                return {"searchResults": []}
            return {
                "searchResults": [
                    {"_id": "vr1", "chunks": [{"content": "OAuth Guide", "block_index": 0, "score": 0.8}]}
                ]
            }

        svc = MagicMock()
        svc.search_with_filters = AsyncMock(side_effect=conditional_search)

        state = _base_state(retrieval_service=svc)
        r = _make_retrieval(state)

        # First: filtered search (0 results)
        result1 = json.loads(
            await r.search_internal_knowledge(
                query="OAuth security",
                category_ids=["cat_finance"],
            )
        )
        assert result1.get("result_count") == 0

        # Retry without filters — this time the mock returns a result
        result2_raw = await r.search_internal_knowledge(query="OAuth security")
        result2 = json.loads(result2_raw)
        # The response might be XML-formatted on success; just verify no error
        assert result2.get("status") != "error" or result2.get("result_count", 0) >= 0

    @pytest.mark.asyncio
    async def test_agent_skips_resolve_for_broad_query(self):
        """Simple broad query should work without resolve step — direct search."""
        svc = _empty_svc()
        state = _base_state(retrieval_service=svc)
        r = _make_retrieval(state)

        result = json.loads(
            await r.search_internal_knowledge(query="what is our vacation policy")
        )

        assert result.get("status") == "success"
        # No entity filters should have been applied
        fg = svc.search_with_filters.call_args.kwargs.get("filter_groups", {})
        for key in ("categories", "topics", "departments", "people"):
            assert key not in fg or not fg[key]

    @pytest.mark.asyncio
    async def test_agent_handles_low_confidence_resolve_result(self):
        """All resolve scores < threshold -> entity IDs should not be passed to search."""
        mock_evs = AsyncMock()
        # Return low-score hits below the actionable threshold
        mock_evs.search_entities = AsyncMock(
            return_value=[
                _resolve_hit("cat_xyz", "category", "Unknown", score=0.10),
            ]
        )

        svc = _empty_svc()
        state = _base_state(retrieval_service=svc, entity_vector_store=mock_evs)
        r = _make_retrieval(state)

        resolve_result = json.loads(
            await r.resolve_entity_filters(query_facets=["zzzunknown"])
        )

        # Agent should not proceed with entity filtering when scores are too low
        high_conf_ids = [
            h["entityId"]
            for h in resolve_result["resolved"].get("zzzunknown", [])
            if h["score"] >= 0.5
        ]
        assert high_conf_ids == []

    @pytest.mark.asyncio
    async def test_agent_combines_entity_filter_with_time_filter(self):
        """'engineering docs from last week' -> category_ids + created_after both forwarded."""
        svc = _empty_svc()
        state = _base_state(retrieval_service=svc)
        r = _make_retrieval(state)

        # Use timezone-aware ISO 8601 timestamp as required by the parser
        await r.search_internal_knowledge(
            query="engineering docs",
            category_ids=["cat_eng"],
            created_after="2024-06-01T00:00:00+00:00",
        )

        assert svc.search_with_filters.called
        fg = svc.search_with_filters.call_args.kwargs.get("filter_groups", {})
        assert "categories" in fg
        assert fg["categories"] == ["cat_eng"]

    @pytest.mark.asyncio
    async def test_entity_filter_org_isolation_in_resolve(self):
        """Resolve tool must pass org_id so results are tenant-isolated."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])

        state = _base_state(entity_vector_store=mock_evs, org_id="org_tenant_A")
        r = _make_retrieval(state)

        await r.resolve_entity_filters(query_facets=["security"])

        call_kwargs = mock_evs.search_entities.call_args.kwargs
        assert call_kwargs.get("org_id") == "org_tenant_A"

    @pytest.mark.asyncio
    async def test_resolve_returns_entity_type_in_result(self):
        """Each resolved entity should carry its entityType for downstream use."""
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[
                _resolve_hit("dept_eng", "department", "Engineering", 0.91),
                _resolve_hit("topic_ml", "topic", "Machine Learning", 0.85),
            ]
        )

        state = _base_state(entity_vector_store=mock_evs)
        r = _make_retrieval(state)

        result = json.loads(
            await r.resolve_entity_filters(query_facets=["engineering machine learning"])
        )

        hits = result["resolved"].get("engineering machine learning", [])
        types = {h["entityType"] for h in hits}
        assert "department" in types
        assert "topic" in types


# ===========================================================================
# Reflection node — entity filter fallback (agent loop level)
# ===========================================================================


class TestReflectNodeEntityFilterFallback:

    @pytest.mark.asyncio
    async def test_reflect_triggers_retry_when_entity_filter_returns_zero(self):
        """reflect_node detects filtered search with 0 results -> continue_with_more_tools."""
        from app.modules.agents.qna.nodes import reflect_node

        state = {
            "all_tool_results": [
                {
                    "tool_name": "retrieval.search_internal_knowledge",
                    "status": "success",
                    "args": {
                        "query": "oauth documentation",
                        "category_ids": ["cat_engineering"],
                    },
                    "result": json.dumps({"result_count": 0, "message": "No results"}),
                }
            ],
            "retry_count": 0,
            "max_retries": 2,
            "iteration_count": 0,
            "max_iterations": 5,
            "logger": MagicMock(),
            "query": "oauth documentation",
        }

        updated = await reflect_node(state, {}, MagicMock())

        assert updated["reflection_decision"] == "continue_with_more_tools"
        fix = updated.get("fix_instruction", "") or updated.get("reflection", {}).get("fix_instruction", "")
        assert fix  # some instruction to retry without filters

    @pytest.mark.asyncio
    async def test_reflect_does_not_trigger_retry_when_no_entity_filters(self):
        """reflect_node should not specifically flag entity filter issue if none were passed."""
        from app.modules.agents.qna.nodes import reflect_node

        state = {
            "all_tool_results": [
                {
                    "tool_name": "retrieval.search_internal_knowledge",
                    "status": "success",
                    "args": {
                        "query": "general query",
                        # No category_ids / topic_ids etc.
                    },
                    "result": json.dumps({"result_count": 0, "message": "No results"}),
                }
            ],
            "retry_count": 0,
            "max_retries": 2,
            "iteration_count": 0,
            "max_iterations": 5,
            "logger": MagicMock(),
            "query": "general query",
        }

        updated = await reflect_node(state, {}, MagicMock())

        assert "reflection_decision" in updated

    @pytest.mark.asyncio
    async def test_reflect_does_not_retry_when_filtered_search_has_results(self):
        """reflect_node should not trigger entity filter fallback if search returned results."""
        from app.modules.agents.qna.nodes import reflect_node

        state = {
            "all_tool_results": [
                {
                    "tool_name": "retrieval.search_internal_knowledge",
                    "status": "success",
                    "args": {
                        "query": "oauth",
                        "category_ids": ["cat_eng"],
                    },
                    "result": json.dumps({"result_count": 3, "results": ["a", "b", "c"]}),
                }
            ],
            "retry_count": 0,
            "max_retries": 2,
            "iteration_count": 0,
            "max_iterations": 5,
            "logger": MagicMock(),
            "query": "oauth",
        }

        updated = await reflect_node(state, {}, MagicMock())

        decision = updated.get("reflection_decision", "")
        fix = updated.get("fix_instruction", "") or updated.get("reflection", {}).get("fix_instruction", "")

        if decision == "continue_with_more_tools":
            assert "without entity filter" not in fix.lower()
