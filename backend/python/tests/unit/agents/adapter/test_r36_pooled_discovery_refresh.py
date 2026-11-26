"""R36-COVER: the MCP hub's pooled listing refreshes an OAuth server's token once on HTTP 401, as the session's own
listing and calls do.

Found by the journey runner's hub lane (``J-agent.quick-seeded-hub`` and the Slack, classic and scheduled ones):
after the provider ended the person's access token, a saved agent's run listed the server at assembly, was refused,
and dropped the server's tools; the call then failed. The default chat, which lists at first use through the
session, refreshed and went on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp.errors import MCPHttpStatusError

LISTED: tuple[Any, Any, Any] = (["tool"], None, None)


def _server(auth_mode: str = "oauth") -> SimpleNamespace:
    return SimpleNamespace(instance_id="inst-1", name="oa", owner_id="u-1", instance={"authMode": auth_mode},
                           auth={"oauthTokens": {"access_token": "old"}})


def _manager() -> MCPSessionManager:
    context = SimpleNamespace(logger=None, tool_state={}, config_service=object(), org_id="org-1")
    return MCPSessionManager(context)  # type: ignore[arg-type]


async def test_a_rejected_pooled_listing_is_listed_again_after_one_refresh() -> None:
    manager = _manager()
    listing = AsyncMock(side_effect=[MCPHttpStatusError(401), LISTED])
    tokens = SimpleNamespace(model_dump=lambda **_: {"access_token": "new"})
    refresh = AsyncMock(return_value=tokens)
    server = _server()
    with patch.object(MCPToolProvider, "_discover_pooled", listing), \
         patch("app.agents.agent_loop.mcp_session.refresh_credential_record", refresh):
        found = await MCPToolProvider()._discover_pooled_refreshing(server, object(), manager, "ns", 5.0)  # type: ignore[arg-type]
    assert found == LISTED
    assert listing.await_count == 2
    refresh.assert_awaited_once()
    assert server.auth["oauthTokens"] == {"access_token": "new"}


@pytest.mark.parametrize(("auth_mode", "failure"), [("oauth", MCPHttpStatusError(503)),
                                                    ("api_token", MCPHttpStatusError(401))])
async def test_anything_else_fails_as_it_did_with_no_refresh(auth_mode: str, failure: Exception) -> None:
    listing = AsyncMock(side_effect=[failure])
    refresh = AsyncMock()
    with patch.object(MCPToolProvider, "_discover_pooled", listing), \
         patch("app.agents.agent_loop.mcp_session.refresh_credential_record", refresh), \
         pytest.raises(MCPHttpStatusError):
        await MCPToolProvider()._discover_pooled_refreshing(_server(auth_mode), object(), _manager(), "ns", 5.0)  # type: ignore[arg-type]
    refresh.assert_not_awaited()
