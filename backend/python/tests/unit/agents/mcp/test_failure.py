"""One reason for every way an MCP server can fail (`app.agents.mcp.failure`)."""
from __future__ import annotations

import asyncio
from typing import Any

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
from tests.unit.agents.mcp.refresh_failures import (
    lock_contention_error,
    token_endpoint_error,
    token_request_error,
)


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


# A refresh the provider or another process stopped is not an expired sign-in: retrying can work.
_PROVIDER_DOWN = [
    pytest.param(503, {"error": "temporarily_unavailable"}, id="503"),
    pytest.param(502, {"error": "bad_gateway"}, id="502"),
    pytest.param(500, {"error": "server_error"}, id="500"),
    pytest.param(429, {"error": "slow_down"}, id="429"),
    # A permanent-sounding code from an overloaded endpoint isn't final either.
    pytest.param(503, {"error": "invalid_grant"}, id="503-invalid_grant"),
    pytest.param(429, {"error": "invalid_grant"}, id="429-invalid_grant"),
]


@pytest.mark.parametrize(("status", "body"), _PROVIDER_DOWN)
async def test_a_token_endpoint_outage_is_unreachable(status: int, body: dict[str, Any]) -> None:
    error = await token_endpoint_error(status, body)

    assert error.status == status
    assert classify_mcp_failure(error) is MCPFailureReason.UNREACHABLE
    assert classify_mcp_failure(ExceptionGroup("tg", [error])) is MCPFailureReason.UNREACHABLE


async def test_waiting_out_another_processs_refresh_is_unreachable() -> None:
    error = await lock_contention_error()

    assert classify_mcp_failure(error) is MCPFailureReason.UNREACHABLE
    assert classify_mcp_failure(ExceptionGroup("tg", [error])) is MCPFailureReason.UNREACHABLE


@pytest.mark.parametrize(("status", "body"), [
    pytest.param(400, {"error": "invalid_grant"}, id="400-invalid_grant"),
    pytest.param(401, {"error": "invalid_client"}, id="401-invalid_client"),
    pytest.param(200, {"error": "bad_refresh_token"}, id="200-github-style"),
    pytest.param(400, {"error": "invalid_request"}, id="400-invalid_request"),
    # Neither transient nor known to be permanent: still a sign-in problem.
    pytest.param(403, {"error": "access_denied"}, id="403"),
])
async def test_a_refused_refresh_still_asks_for_a_new_sign_in(status: int, body: dict[str, Any]) -> None:
    assert classify_mcp_failure(await token_endpoint_error(status, body)) is MCPFailureReason.AUTH_EXPIRED


@pytest.mark.parametrize("error", [
    MCPTokenRefreshError("No refresh token available at /services/mcp/credentials/i/u"),
    MCPTokenRefreshError("No OAuth client configuration found for /services/mcp/credentials/i/u"),
    MCPOAuthError("OAuth refresh response missing access_token"),
])
def test_a_refresh_failure_without_a_status_still_asks_for_a_new_sign_in(error: Exception) -> None:
    assert classify_mcp_failure(error) is MCPFailureReason.AUTH_EXPIRED


@pytest.mark.parametrize(("raised", "reason"), [
    (httpx.ConnectError("Name or service not known"), MCPFailureReason.UNREACHABLE),
    (httpx.ConnectError("Connection refused"), MCPFailureReason.UNREACHABLE),
    (httpx.ReadTimeout("slow"), MCPFailureReason.TIMEOUT),
    (httpx.ConnectTimeout("slow"), MCPFailureReason.TIMEOUT),
])
async def test_a_token_request_that_got_no_answer(raised: Exception, reason: MCPFailureReason) -> None:
    assert classify_mcp_failure(await token_request_error(raised)) is reason
