"""H2 SN-10: the Slack nearby-messages tool is built with the request's graph, org, user, record-id shortener and a
service-account agent's caller, so its anchor is checked as ``fetch_slack_thread``'s is (H1 FR-10)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from app.agents.agent_loop.tool_loader import (
    _build_dynamic_tools,  # pyright: ignore[reportPrivateUsage]
)


def test_sn10_the_nearby_tool_gets_the_request_s_principals() -> None:
    graph = MagicMock()
    state: dict[str, Any] = {
        "config_service": MagicMock(), "has_slack_connector": True, "has_slack_knowledge": True,
        "graph_provider": graph, "org_id": "org-1", "user_id": "creator-1",
        "user_info": {"callerUserId": "caller-1"},
    }
    built: dict[str, Any] = {}

    def factory(**kwargs: Any) -> MagicMock:  # noqa: ANN401
        built.update(kwargs)
        tool = MagicMock()
        tool.name = "fetch_slack_nearby_messages"
        return tool

    with patch("app.utils.fetch_slack_nearby_messages.create_fetch_slack_nearby_messages_tool", factory), patch(
        "app.agents.agent_loop.tool_loader.PipesHubStructuredToolAdapter", MagicMock()
    ):
        _build_dynamic_tools(SimpleNamespace(tool_state=state))  # type: ignore[arg-type]
    assert built["graph_provider"] is graph
    assert (built["org_id"], built["user_id"], built["caller_user_id"]) == ("org-1", "creator-1", "caller-1")
    assert built["tool_state"] is state
