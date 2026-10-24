"""Exceptions shared by the MCP client and the URL guard it depends on."""
import httpx
import httpx2

_HTTP_UNAUTHORIZED = 401


class MCPConnectionError(Exception):
    """Raised when an MCP server cannot be reached or its transport config is invalid.

    `str()` is safe to show any caller. A STDIO server's captured stderr is kept apart in
    `stderr_tail` — it can echo environment values or paths — and only `detail` (for logs
    and administrators) includes it.
    """

    def __init__(self, message: str = "", *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail

    @property
    def detail(self) -> str:
        return f"{self} | subprocess stderr: {self.stderr_tail}" if self.stderr_tail else str(self)


class MCPHttpStatusError(MCPConnectionError):
    """The MCP server answered with an HTTP error status. The mcp SDK reports these without the
    status; the client restores it from the operation's wire record (`app.agents.mcp.wire`)."""

    def __init__(self, status_code: int, message: str = "", *, challenge: dict[str, str] | None = None) -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        # The response's Bearer challenge (`WWW-Authenticate`), parsed; {} without one.
        self.challenge = challenge or {}


class MCPInsufficientScopeError(MCPHttpStatusError):
    """HTTP 403 `insufficient_scope` from an OAuth server: the sign-in lacks `scopes`, which its
    next authorization asks for (`app.agents.mcp.step_up`). The message says what to do."""

    def __init__(self, message: str, *, scopes: list[str]) -> None:
        super().__init__(403, message)
        self.scopes = scopes


class MCPRequestNotSentError(MCPConnectionError):
    """A request never reached the MCP server: no connection could be made, or the session had
    already closed. Nothing ran, so it can be sent again."""


class MCPRequestLostError(MCPConnectionError):
    """The connection failed after a request went out, so the server may have acted on it. Only
    that request failed; the session is still usable."""


class MCPConnectionLostError(MCPConnectionError):
    """The session's connection closed (a local server exited, the transport failed). A request
    that was waiting for its answer may already have run."""


class MCPSessionExpiredError(MCPConnectionLostError):
    """The server no longer knows the session (HTTP 404 on its session id). It refused the request
    without running it."""


class MCPCallInterruptedError(MCPConnectionError):
    """A tool call failed after it was sent and the server may already have run it, so it is never
    retried. The message says so."""


class MCPToolNotOfferedError(Exception):
    """The server no longer lists the tool the model called: the turn's tool list came from the
    cache, and the server's own list has changed since. The call isn't sent."""


class MCPToolChangedError(MCPToolNotOfferedError):
    """The server still lists the tool, but its arguments or hints changed since the turn's
    cached list: the call was planned, and approved or not, on the old ones. Not sent."""


class MCPUrlBlockedError(MCPConnectionError):
    """A URL (or redirect hop) points somewhere MCP traffic may not go. The message is
    safe to show; the resolved address that triggered it is only logged."""


class MCPLaunchRefusedError(MCPConnectionError):
    """This deployment's local-command policy won't start the server. The message says why."""


def is_http_unauthorized(exc: BaseException) -> bool:
    """Whether `exc`, its `__cause__` chain or any exception in a group is an HTTP 401
    response. The client restores the status of a rejected request as `MCPHttpStatusError`;
    the HTTP libraries' own status errors come from our OAuth requests and the SSE connect.

    `__context__` is not followed: it is whatever was being handled when `exc` was raised,
    so a retry that fails after a 401 would read as another 401."""
    for current in _cause_chain(exc):
        if isinstance(current, (httpx.HTTPStatusError, httpx2.HTTPStatusError)) and current.response.status_code == _HTTP_UNAUTHORIZED:
            return True
        if isinstance(current, MCPHttpStatusError) and current.status_code == _HTTP_UNAUTHORIZED:
            return True
    return False


def insufficient_scope(exc: BaseException) -> "MCPHttpStatusError | None":
    """The HTTP 403 in `exc` or its `__cause__` chain whose challenge says `insufficient_scope`."""
    for current in _cause_chain(exc):
        if (
            isinstance(current, MCPHttpStatusError) and current.status_code == 403
            and current.challenge.get("error", "").lower() == "insufficient_scope"
        ):
            return current
    return None


def _cause_chain(exc: BaseException) -> "list[BaseException]":
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    found: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return found


def _first_of(exc: BaseException, kinds: "type[BaseException] | tuple[type[BaseException], ...]") -> "BaseException | None":
    return next((e for e in _cause_chain(exc) if isinstance(e, kinds)), None)


def request_never_reached_server(exc: BaseException) -> bool:
    """The request can't have run: it was never sent, or the server refused its session."""
    return _first_of(exc, (MCPRequestNotSentError, MCPSessionExpiredError)) is not None


def request_may_have_run(exc: BaseException) -> bool:
    """The connection failed after the request went out, so the server may have run it."""
    found = _first_of(exc, (MCPRequestLostError, MCPConnectionLostError))
    return found is not None and not isinstance(found, MCPSessionExpiredError) and not request_never_reached_server(exc)


def first_leaf(exc: BaseException) -> BaseException:
    """The first exception in `exc` that isn't a group: what a task group's failure was."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc
