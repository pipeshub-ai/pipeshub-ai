"""Who gets to see a STDIO server's stderr in `app.api.routes.mcp_servers` responses."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.errors import MCPConnectionError, MCPUrlBlockedError
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import get_instance_tools, get_my_mcp_servers
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

_STDERR = "server: EXA_API_KEY=sk-live-123 rejected"


def _store() -> FakeConfigService:
    return FakeConfigService({
        "/services/mcp/instances/inst-1": {
            "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Exa", "typeId": None,
            "transport": "stdio", "command": "npx", "authMode": "none", "isCustom": True,
            "createdAt": 1, "updatedAt": 1,
        },
    })


def _discovery_fails() -> Any:  # noqa: ANN401
    return patch(
        "app.agents.mcp.discovery.discover_tool_listing",
        new=AsyncMock(side_effect=MCPConnectionError("Connection closed", stderr_tail=_STDERR)),
    )


@pytest.mark.parametrize(("is_admin", "shows_stderr"), [(False, False), (True, True)])
async def test_tools_error_shows_stderr_to_admins_only(is_admin: bool, shows_stderr: bool) -> None:
    with _discovery_fails(), patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=is_admin)):
        result = await get_my_mcp_servers(route_request(_store()), include_tools=True)

    tools_error = result["instances"][0]["toolsError"]
    assert tools_error.startswith("Connection closed")
    assert (_STDERR in tools_error) is shows_stderr


async def test_instance_tools_502_shows_nobody_the_stderr() -> None:
    """The gateway answers every 5xx with its own sentence anyway; admins read the stderr in
    the listing above."""
    with _discovery_fails():
        with pytest.raises(HTTPException) as exc:
            await get_instance_tools(route_request(_store()), "inst-1")

    assert exc.value.status_code == 502
    assert _STDERR not in exc.value.detail


async def test_listing_without_tools_checks_the_admin_once() -> None:
    # Whether connection details are listed depends on it, with or without tools.
    admin_check = AsyncMock(return_value=True)
    with patch.object(mcp_servers, "_check_user_is_admin", new=admin_check):
        result = await get_my_mcp_servers(route_request(_store()), include_tools=False)

    admin_check.assert_awaited_once()
    assert result["instances"][0]["command"] == "npx"


def _unauthorized() -> MCPConnectionError:
    import httpx

    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    try:
        raise httpx.HTTPStatusError("no", request=request, response=httpx.Response(401, request=request))
    except httpx.HTTPStatusError as e:
        error = MCPConnectionError("HTTP 401")
        error.__cause__ = e
        return error


@pytest.mark.parametrize(("failure", "code"), [
    (MCPConnectionError("Connection refused"), "unreachable"),
    (_unauthorized(), "unauthorized"),
    (MCPUrlBlockedError("That address is on a private network."), "blocked"),
    (RuntimeError("bug"), "error"),
])
async def test_tools_error_carries_a_code_the_ui_can_act_on(failure: Exception, code: str) -> None:
    with patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=failure)), \
         patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
        result = await get_my_mcp_servers(route_request(_store()), include_tools=True)

    assert result["instances"][0]["toolsErrorCode"] == code


async def test_an_expired_sign_in_is_coded_for_reconnect() -> None:
    from app.agents.mcp.token_refresh import MCPTokenRefreshError

    with patch.object(mcp_servers, "discover_tools_for_owner", new=AsyncMock(side_effect=MCPTokenRefreshError("gone"))), \
         patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
        result = await get_my_mcp_servers(route_request(_store()), include_tools=True)

    assert result["instances"][0]["toolsErrorCode"] == "auth_expired"


async def test_a_healthy_listing_has_no_code() -> None:
    with patch.object(mcp_servers, "discover_tools_for_owner", new=AsyncMock(return_value=([], {}))), \
         patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=False)):
        result = await get_my_mcp_servers(route_request(_store()), include_tools=True)

    assert result["instances"][0]["toolsErrorCode"] is None
