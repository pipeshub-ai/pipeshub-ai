"""Tests for `mention_binding` (PRE_TOOL_USE pronoun resolution) and
`remember_entity_mentions` (mention-map population)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.mention_binding import (
    mention_binding,
    remember_entity_mentions,
)
from tests.unit.agents.adapter.support.hook_helpers import assert_allowed, run_pre_tool


def _make_context(**tool_state_overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    context.tool_state.update(tool_state_overrides)
    return context


class TestRememberEntityMentions:
    def test_empty_entities_is_noop(self) -> None:
        state: dict = {}
        remember_entity_mentions(state, [])
        assert state.get("_kg_mentions") is None

    def test_appends_new_mention(self) -> None:
        state: dict = {}
        remember_entity_mentions(state, [{"entityId": "d1", "entityType": "department", "name": "Legal"}])
        assert state["_kg_mentions"] == [{"id": "d1", "name": "Legal", "type": "department"}]

    def test_skips_entities_missing_id_or_name(self) -> None:
        state: dict = {}
        remember_entity_mentions(state, [{"entityId": None, "name": "Legal"}, {"entityId": "d1", "name": None}])
        assert state.get("_kg_mentions", []) == []

    def test_remention_moves_to_most_recent(self) -> None:
        state: dict = {}
        remember_entity_mentions(state, [{"entityId": "d1", "entityType": "department", "name": "Legal"}])
        remember_entity_mentions(state, [{"entityId": "t1", "entityType": "topic", "name": "Roadmap"}])
        remember_entity_mentions(state, [{"entityId": "d1", "entityType": "department", "name": "Legal"}])
        ids = [m["id"] for m in state["_kg_mentions"]]
        assert ids == ["t1", "d1"]

    def test_caps_at_max_mentions(self) -> None:
        state: dict = {}
        for i in range(25):
            remember_entity_mentions(state, [{"entityId": f"e{i}", "entityType": "topic", "name": f"Topic {i}"}])
        assert len(state["_kg_mentions"]) == 20
        assert state["_kg_mentions"][-1]["id"] == "e24"


@pytest.mark.asyncio
class TestMentionBindingMiddleware:
    async def test_ignores_unrelated_tools(self) -> None:
        context = _make_context(_kg_mentions=[{"id": "d1", "name": "Legal", "type": "department"}])
        middleware = mention_binding(context)
        ctx = await run_pre_tool(
            middleware, tool_path="/tools/jira/create_issue", tool_input={"query": "what is it about"},
        )
        assert_allowed(ctx)
        assert ctx.tool_input["query"] == "what is it about"

    async def test_no_pronoun_leaves_query_unchanged(self) -> None:
        context = _make_context(_kg_mentions=[{"id": "d1", "name": "Legal", "type": "department"}])
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "legal department policies"},
            )
        assert ctx.tool_input["query"] == "legal department policies"

    async def test_empty_mention_map_leaves_query_unchanged(self) -> None:
        context = _make_context()
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "what did they say about it"},
            )
        assert ctx.tool_input["query"] == "what did they say about it"

    async def test_single_mention_inlined_unambiguously(self) -> None:
        context = _make_context(_kg_mentions=[{"id": "d1", "name": "Legal", "type": "department"}])
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "what policies does it have"},
            )
        assert "Legal" in ctx.tool_input["query"]

    async def test_two_distinct_recent_mentions_are_ambiguous(self) -> None:
        context = _make_context(_kg_mentions=[
            {"id": "d1", "name": "Legal", "type": "department"},
            {"id": "t1", "name": "Roadmap", "type": "topic"},
        ])
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "tell me more about that"},
            )
        query = ctx.tool_input["query"]
        assert "Legal" in query and "Roadmap" in query

    async def test_two_mentions_of_same_entity_are_unambiguous(self) -> None:
        context = _make_context(_kg_mentions=[
            {"id": "d1", "name": "Legal", "type": "department"},
            {"id": "d1", "name": "Legal", "type": "department"},
        ])
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            return_value="knowledgegraph__search",
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "tell me more about it"},
            )
        assert "Legal" in ctx.tool_input["query"]
        assert " or " not in ctx.tool_input["query"]

    async def test_fails_open_on_exception(self) -> None:
        context = _make_context(_kg_mentions=[{"id": "d1", "name": "Legal", "type": "department"}])
        middleware = mention_binding(context)
        with patch(
            "app.agents.agent_loop.hooks._tool_naming.resolve_tool_name",
            side_effect=RuntimeError("boom"),
        ):
            ctx = await run_pre_tool(
                middleware, tool_path="/tools/knowledgegraph/search",
                tool_input={"query": "what about it"},
            )
        assert_allowed(ctx)
