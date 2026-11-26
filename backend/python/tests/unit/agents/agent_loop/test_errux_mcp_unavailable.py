"""EU-12: an MCP server that is unreachable or went away gives its tool reason `unavailable` (QA17-O4, DD-12)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED

from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.oauth_client import MCPOAuthError


def _adapter(exc: Exception) -> MCPToolAdapter:
    class Sessions:
        async def call(self, server: ResolvedMCPServer, tool_name: str, arguments: dict[str, Any], **_: Any) -> Any:  # noqa: ANN401
            raise exc

    server = ResolvedMCPServer(
        instance_id="inst-1", name="JiraMCP", display_name="Jira MCP",
        instance={"authMode": "none"}, auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(name="search", namespaced_name="mcp_jira_search", description="Search", input_schema={})
    return MCPToolAdapter(server, info, Sessions())  # type: ignore[arg-type]


CASES = [
    ("server refuses the connection", MCPConnectionError("Failed to connect to MCP server JiraMCP"), "unavailable"),
    ("the session's transport fails", httpx.ConnectError("All connection attempts failed"), "unavailable"),
    ("the server went away mid-session", MCPError(code=CONNECTION_CLOSED, message="Connection closed"),
     "unavailable"),
    ("the call times out", TimeoutError(), "timeout"),
    ("the token is refused", MCPOAuthError("refresh failed"), "auth"),
    ("the tool's own error", MCPError(code=-32602, message="bad params"), "failed"),
]


@pytest.mark.parametrize(("exc", "code"), [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES])
async def test_eu_12_an_mcp_failure_is_typed(exc: Exception, code: str) -> None:
    output = await _adapter(exc).execute(query="x")
    assert (output.success, output.error_code) == (False, code)
    assert output.error, "the model still reads the detail"
