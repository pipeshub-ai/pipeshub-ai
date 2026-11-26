"""The product's per-request tools, built the way a request builds them (``_build_dynamic_tools``), for tests that need
their real names, paths and tags: the web search and page fetch tools (FG2), and the Slack context tools (KN2)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.tools.base import Tool


def product_web_tools() -> dict[str, Tool]:
    """By model-facing name (``dynamic__web_search``, ``dynamic__fetch_url``)."""
    from app.agents.agent_loop.tool_loader import (
        _build_dynamic_tools,  # pyright: ignore[reportPrivateUsage]
    )

    state: dict[str, object] = {"web_search_config": {"provider": "duckduckgo", "configuration": {}}}
    tools = _build_dynamic_tools(request_context(state))  # type: ignore[arg-type]
    return {tool.name: tool for tool in tools}


def request_context(state: dict[str, object]) -> SimpleNamespace:
    # Main's web tools (#4300) are built for a caller and a research budget, read from the context.
    return SimpleNamespace(tool_state=state, org_id="org-1", user_id="user-1", conversation_id=None, deep_research=False)


def product_tools() -> dict[str, Tool]:
    """The web tools and the Slack context tools, by model-facing name (``slack__fetch_slack_thread``, …)."""
    from app.agents.agent_loop.tool_loader import (
        _build_dynamic_tools,  # pyright: ignore[reportPrivateUsage]
    )

    state: dict[str, object] = {
        "web_search_config": {"provider": "duckduckgo", "configuration": {}},
        "config_service": object(),
        "has_slack_connector": True,
        "has_slack_knowledge": True,
        "org_id": "org-1",
    }
    tools = _build_dynamic_tools(request_context(state))  # type: ignore[arg-type]
    return {tool.name: tool for tool in tools}


SLACK_CONTEXT_TOOLS = ("slack__fetch_slack_thread", "slack__fetch_slack_nearby_messages")

__all__ = ["SLACK_CONTEXT_TOOLS", "product_tools", "product_web_tools", "request_context"]
