"""A scheduled task that asks for web search used to get an agent with no web
tools at all: `build_headless_context` had no `web_search_config` parameter, so
`PipesHubToolLoader._build_dynamic_tools` — which gates `web_search`/
`fetch_url` on exactly that value — always skipped them, and the run fell back
to whatever else the registry held.

These mirror the chat-path cases in
`tests/unit/agents/adapter/test_tool_loader_chat_mode_gates.py`, on the
headless constructor instead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from app.agents.agent_loop.tool_loader import _build_dynamic_tools
from app.services.tasks.runtime.headless_context import build_headless_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


def _context(**overrides) -> "AgentContext":
    defaults = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "u@example.com",
        "graph_provider": MagicMock(),
        "config_service": MagicMock(),
        "logger": MagicMock(),
    }
    defaults.update(overrides)
    return build_headless_context(**defaults)


class TestHeadlessWebSearchGate:
    def test_config_reaches_the_tool_state_the_loader_reads(self) -> None:
        context = _context(web_search_config={"provider": "tavily", "configuration": {}})
        assert context.tool_state["web_search_config"] == {"provider": "tavily", "configuration": {}}

    def test_no_config_builds_no_web_tools(self) -> None:
        assert _build_dynamic_tools(_context()) == []

    def test_config_builds_web_search_and_fetch_url(self) -> None:
        context = _context(web_search_config={"provider": "tavily", "configuration": {}})
        with (
            patch("app.utils.web_search_tool.create_web_search_tool", return_value=MagicMock()),
            patch("app.utils.fetch_url_tool.create_fetch_url_tool", return_value=MagicMock()),
            patch(
                "app.agents.agent_loop.tool_loader.split_original_tool_name",
                side_effect=[("web_search", "search"), ("fetch_url", "fetch")],
            ),
        ):
            tools = _build_dynamic_tools(context)

        assert len(tools) == 2
