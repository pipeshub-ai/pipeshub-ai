"""One reason for every way an MCP server can fail (`app.agents.mcp.failure`)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPHttpStatusError,
    MCPInsufficientScopeError,
    MCPLaunchRefusedError,
    MCPUrlBlockedError,
)
from app.agents.mcp.failure import MCPFailureReason, classify_mcp_failure
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError


def _http_error(status: int) -> MCPConnectionError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    error = MCPConnectionError(f"HTTP {status}")
    error.__cause__ = httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))
    return error


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (MCPTokenRefreshError("no refresh token"), MCPFailureReason.AUTH_EXPIRED),
        (MCPOAuthError("invalid_grant"), MCPFailureReason.AUTH_EXPIRED),
        (_http_error(401), MCPFailureReason.UNAUTHORIZED),
        (MCPInsufficientScopeError("needs more", scopes=["admin"]), MCPFailureReason.NEEDS_PERMISSION),
        # A 403 that signing in again can't fix stays what it was.
        (MCPHttpStatusError(403, challenge={"error": "insufficient_scope", "scope": "admin"}), MCPFailureReason.UNREACHABLE),
        (MCPUrlBlockedError("private address"), MCPFailureReason.BLOCKED),
        (MCPLaunchRefusedError("Shells cannot be used"), MCPFailureReason.BLOCKED),
        (asyncio.TimeoutError(), MCPFailureReason.TIMEOUT),
        (httpx.ReadTimeout("slow"), MCPFailureReason.TIMEOUT),
        (_http_error(503), MCPFailureReason.UNREACHABLE),
        (MCPConnectionError("Connection refused"), MCPFailureReason.UNREACHABLE),
        (httpx.ConnectError("refused"), MCPFailureReason.UNREACHABLE),
        (ConnectionResetError(), MCPFailureReason.UNREACHABLE),
        (RuntimeError("bug"), MCPFailureReason.ERROR),
    ],
)
def test_each_failure_has_its_reason(exc: BaseException, reason: MCPFailureReason) -> None:
    assert classify_mcp_failure(exc) is reason


def test_the_sdk_task_group_is_looked_through() -> None:
    assert classify_mcp_failure(ExceptionGroup("tg", [MCPUrlBlockedError("x")])) is MCPFailureReason.BLOCKED
    assert classify_mcp_failure(ExceptionGroup("tg", [asyncio.TimeoutError()])) is MCPFailureReason.TIMEOUT


def test_a_refused_launch_is_still_a_connection_error() -> None:
    # Callers that catch MCPConnectionError keep working.
    assert issubclass(MCPLaunchRefusedError, MCPConnectionError)
