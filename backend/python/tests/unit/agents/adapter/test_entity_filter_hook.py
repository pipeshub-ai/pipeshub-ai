"""Tests for the deterministic `entity_filter_resolution` PRE_TOOL_USE hook."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.entity_filter import entity_filter_resolution
from tests.unit.agents.adapter.support.hook_helpers import assert_allowed, run_pre_tool


def _make_context(**tool_state_overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    context.tool_state.update(tool_state_overrides)
    return context


@pytest.mark.asyncio
class TestEntityFilterResolution:
    async def test_ignores_unrelated_tools(self) -> None:
        evs = AsyncMock()
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        ctx = await run_pre_tool(
            middleware, tool_path="/tools/jira/create_issue", tool_input={"query": "legal"},
        )
        assert_allowed(ctx)
        evs.search_entities.assert_not_called()

    async def test_no_query_is_noop(self) -> None:
        evs = AsyncMock()
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            await run_pre_tool(middleware, tool_path="/tools/knowledgegraph/search", tool_input={})
        evs.search_entities.assert_not_called()

    async def test_missing_entity_vector_store_is_noop(self) -> None:
        context = _make_context(entity_vector_store=None)
        middleware = entity_filter_resolution(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "legal team docs"},
            )
        assert_allowed(ctx)
        assert context.tool_state.get("_kg_query_entity_filters", {}) == {}

    async def test_caches_filters_for_matching_query(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "legal team docs"},
            )
        assert_allowed(ctx)
        assert context.tool_state["_kg_query_entity_filters"]["legal team docs"] == {
            "departments": ["Legal"],
        }

    async def test_no_confident_matches_leaves_cache_empty(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.1},
        ]
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "random text"},
            )
        assert "random text" not in context.tool_state.get("_kg_query_entity_filters", {})

    async def test_fails_open_on_search_entities_exception(self) -> None:
        evs = AsyncMock()
        evs.search_entities.side_effect = RuntimeError("vector db down")
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "legal team docs"},
            )
        assert_allowed(ctx)

    async def test_calls_next_fn_even_on_match(self) -> None:
        evs = AsyncMock()
        evs.search_entities.return_value = [
            {"entityId": "d1", "entityType": "department", "name": "Legal", "score": 0.9},
        ]
        context = _make_context(entity_vector_store=evs)
        middleware = entity_filter_resolution(context)
        called = False

        async def _next() -> None:
            nonlocal called
            called = True

        from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

        ctx = ToolCallContext(tool_path="/tools/knowledgegraph/search", tool_input={"query": "legal"})
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            await middleware(ctx, _next)
        assert called is True
