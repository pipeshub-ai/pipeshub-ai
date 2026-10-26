"""Why an MCP server couldn't be used, as one code shared by the server listing, the agent
loop (what the model is told) and the UI (which state and action it shows)."""
from __future__ import annotations

import asyncio
from enum import Enum

import httpx
import httpx2

from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPInsufficientScopeError,
    MCPLaunchRefusedError,
    MCPUrlBlockedError,
    first_leaf,
    is_http_unauthorized,
)
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError


class MCPFailureReason(str, Enum):
    AUTH_EXPIRED = "auth_expired"
    """The sign-in expired and couldn't be refreshed: the user has to reconnect."""
    UNAUTHORIZED = "unauthorized"
    """The server answered 401 to the stored credentials: they have to be replaced."""
    NEEDS_PERMISSION = "needs_permission"
    """The OAuth sign-in lacks scopes the server asked for: signing in again asks for them."""
    BLOCKED = "blocked"
    """This deployment's URL guard or local-command policy refused it: an administrator's fix."""
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    """The connection failed or the server answered with an error."""
    ERROR = "error"


_POLICY_ERRORS = (MCPUrlBlockedError, MCPLaunchRefusedError)
_SIGN_IN_ERRORS = (MCPTokenRefreshError, MCPOAuthError)


def classify_mcp_failure(exc: BaseException) -> MCPFailureReason:
    # The SDK can wrap the real error in its task group's ExceptionGroup.
    leaf = first_leaf(exc)
    candidates = (exc, leaf)
    if any(isinstance(e, _SIGN_IN_ERRORS) for e in candidates):
        return MCPFailureReason.AUTH_EXPIRED
    if is_http_unauthorized(exc):
        return MCPFailureReason.UNAUTHORIZED
    if any(isinstance(e, MCPInsufficientScopeError) for e in candidates):
        return MCPFailureReason.NEEDS_PERMISSION
    if any(isinstance(e, _POLICY_ERRORS) for e in candidates):
        return MCPFailureReason.BLOCKED
    # Before the network check: TimeoutError is an OSError, httpx's timeouts are HTTPErrors.
    if isinstance(leaf, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException, httpx2.TimeoutException)):
        return MCPFailureReason.TIMEOUT
    if any(isinstance(e, (MCPConnectionError, httpx.HTTPError, httpx2.HTTPError, OSError)) for e in candidates):
        return MCPFailureReason.UNREACHABLE
    return MCPFailureReason.ERROR
