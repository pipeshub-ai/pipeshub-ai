"""Why an MCP server couldn't be used, as one code shared by the server listing, the agent
loop (what the model is told) and the UI (which state and action it shows)."""
from __future__ import annotations

import asyncio
import logging
from enum import Enum

import httpx
import httpx2

from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPInsufficientScopeError,
    MCPLaunchRefusedError,
    MCPToolChangedError,
    MCPToolNotOfferedError,
    MCPUrlBlockedError,
    first_leaf,
    is_http_unauthorized,
)
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import (
    MCPRefreshLockTimeoutError,
    MCPTokenRefreshError,
)
from app.utils import error_class
from app.utils.credential_refresh_log import MISCONFIGURED_REPEAT_S, refresh_error_class
from app.utils.logging_fields import log_event, log_once
from app.utils.oauth_errors import is_transient_status
from mcp.shared.exceptions import MCPError


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
    """The connection failed or the server answered with an error, or the sign-in couldn't be renewed
    because its provider is down, rate-limiting or busy with another refresh."""
    ERROR = "error"


SIGN_IN_FAILURES = frozenset({
    MCPFailureReason.AUTH_EXPIRED, MCPFailureReason.UNAUTHORIZED, MCPFailureReason.NEEDS_PERMISSION,
})
"""The server wants a (new) sign-in: a person can fix these by signing in, unless the instance
has no authentication to sign in with."""

_POLICY_ERRORS = (MCPUrlBlockedError, MCPLaunchRefusedError)
_SIGN_IN_ERRORS = (MCPTokenRefreshError, MCPOAuthError)


def classify_mcp_failure(exc: BaseException) -> MCPFailureReason:
    # The SDK can wrap the real error in its task group's ExceptionGroup.
    leaf = first_leaf(exc)
    candidates = (exc, leaf)
    # A refresh the provider or another process's refresh stopped says nothing about the sign-in.
    if any(
        isinstance(e, MCPRefreshLockTimeoutError) or (isinstance(e, MCPOAuthError) and is_transient_status(e.status))
        for e in candidates
    ):
        return MCPFailureReason.UNREACHABLE
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


# For a failure `classify_error` can only call internal: what the MCP reason means.
_ERROR_CLASS_FALLBACK = {
    MCPFailureReason.AUTH_EXPIRED: "auth_expired",
    MCPFailureReason.UNAUTHORIZED: "auth_expired",
    MCPFailureReason.NEEDS_PERMISSION: "forbidden",
    MCPFailureReason.BLOCKED: "forbidden",
    MCPFailureReason.TIMEOUT: "timeout",
    MCPFailureReason.UNREACHABLE: "unavailable",
}
_JSONRPC_REQUEST_ERRORS = frozenset({-32600, -32601, -32602})  # invalid request, method not found, invalid params


def mcp_error_class(exc: BaseException, reason: MCPFailureReason | None = None) -> str:
    """The log `error_class` of an MCP failure (`app.utils.error_class.ERROR_CLASSES`).
    `reason` is `classify_mcp_failure(exc)` when the caller already has it."""
    leaf = first_leaf(exc)
    if isinstance(leaf, MCPToolChangedError):  # before its base class
        return "conflict"
    if isinstance(leaf, MCPToolNotOfferedError):  # the turn's cached tool list is stale
        return "not_found"
    if isinstance(leaf, MCPError):  # the server's own JSON-RPC error
        return "bad_request" if leaf.code in _JSONRPC_REQUEST_ERRORS else "upstream_5xx"
    if (tagged := refresh_error_class(leaf)) is not None:  # the class the refresh's own `end` line gave it
        return tagged
    found = error_class.classify_error(leaf)
    if found != "internal":
        return found
    return _ERROR_CLASS_FALLBACK.get(reason or classify_mcp_failure(exc), "internal")


def misconfigured_level(mcp_server_id: str, cls: str) -> int | None:
    """DEBUG for a per-turn `misconfigured` line already logged for this server within the hour (the first
    is ERROR); None otherwise. One gate per server and class, shared by `tool_call.end` and `mcp.discover.end`."""
    if cls == "misconfigured" and not log_once(f"mcp:{mcp_server_id}:{cls}", MISCONFIGURED_REPEAT_S):
        return logging.DEBUG
    return None


def log_discover_end(
    log: logging.Logger, mcp_server_id: str, failure: BaseException | None, *, source: str,
    reason: str = "no_attached_tools", level: int | None = None, **fields: object,
) -> None:
    """One `mcp.discover.end` for a server whose tools couldn't be listed (`source`: a chat `turn`, or
    the server `listing`): its failure at the failure's level unless `level` is given (with the stack
    only for our own bugs, and never the server's text), or a WARNING `skipped` with `reason`."""
    if failure is None:
        log_event(
            log, "mcp.discover.end", level=logging.WARNING, mcp_server_id=mcp_server_id, outcome="skipped",
            reason=reason, source=source, **fields,
        )
        return
    cls = mcp_error_class(failure)
    level = level if level is not None else misconfigured_level(mcp_server_id, cls)
    want = level if level is not None else error_class.level_for(cls)
    if error_class.already_logged(failure, want):
        # The refresh's `end` (or another unit) logged it: this server's line at its own level, no second stack.
        log_event(
            log, "mcp.discover.end", level=want, mcp_server_id=mcp_server_id, outcome="failure", error_class=cls,
            error_type=type(failure).__name__, source=source, **fields,
        )
        return
    error_class.log_failure(
        log, "mcp.discover.end", failure, level=level, error_class=cls,
        include_error_text=False, mcp_server_id=mcp_server_id, source=source, **fields,
    )
