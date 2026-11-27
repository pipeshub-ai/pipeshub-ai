"""MCP Streamable HTTP transport of the run-scoped tool gateway (E2, ARCHITECTURE §4.4.2).

``ToolGatewayApp`` is one ASGI app the durable worker mounts. Each binding is mounted at ``/{key}/mcp`` with its
own MCP server over its own ``ToolEndpoint``, for as long as ``serving`` holds
it. Every request must carry the gateway token the egress proxy injects
(``Authorization: Bearer``): a missing, invalid or expired token, or one for
another binding, is refused with 401 and its reason code before MCP sees the
request, and logged once. A user's session token is never accepted: it is not
a gateway token (wrong issuer, audience and algorithm).
"""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError

from harness_core.gateway.errors import BindingMismatch, GatewayRefusal
from harness_core.gateway.ports import NotesConnects
from harness_core.gateway.result_cap import (
    bound_engine_result,
    endpoint_result_cap,
    wants_full_result,
)
from harness_core.logs import HarnessLog
from harness_core.telemetry.metrics import (
    NOOP_GATEWAY_METRICS,
    UNMATCHED,
    GatewayRequest,
    outcome_of,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping

    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.token import GatewayTokenVerifier
    from harness_core.gateway.types import (
        GatewayBinding,
        GatewayClaims,
        McpResult,
        McpTool,
    )
    from harness_core.telemetry.metrics import GatewayMetrics

    # ASGI, as the MCP SDK's session manager takes it.
    Scope = MutableMapping[str, Any]
    Message = MutableMapping[str, Any]
    Receive = Callable[[], Awaitable[Message]]
    Send = Callable[[Message], Awaitable[None]]

SERVER_NAME = "pipeshub-tools"
CLAIMS_KEY = "pipeshub.gateway.claims"
# The egress proxy's correlation id for one sandbox request (P5.4).
PROXY_REQUEST_HEADER = b"x-egress-request-id"
REFUSAL_HEADER = b"x-gateway-refusal"
# A listed tool's auth state (``needs_authentication``), for clients and ``ph`` (OWD-7 "mask, don't remove").
AUTH_META = "pipeshub/auth"
LOGGER = "app.harness.gateway"


def to_call_result(result: McpResult) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=result.text)],
        structured_content=result.structured(),
        is_error=result.is_error,
    )


def _listed(tool: McpTool) -> types.Tool:
    """A tool as ``tools/list`` shows it; a masked one carries its auth state in ``_meta`` (WS-09), the rest have none."""
    meta = {AUTH_META: tool.auth} if tool.auth is not None else None
    return types.Tool(name=tool.name, description=tool.description, input_schema=dict(tool.input_schema), meta=meta)


def _server(endpoint: ToolEndpoint) -> Server[Any]:
    def claims(ctx: Any) -> GatewayClaims:  # noqa: ANN401 - the SDK's request context
        scope = cast("dict[str, Any]", getattr(ctx.request, "scope", {}))
        return cast("GatewayClaims", scope[CLAIMS_KEY])

    async def list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:  # noqa: ANN401
        # The whole grant is one page and no cursor is ever issued, so any cursor is one this server never gave.
        if params is not None and params.cursor is not None:
            raise MCPError(types.INVALID_PARAMS, "invalid cursor: tools/list is never paginated by this server")
        tools = await endpoint.list_tools(claims(ctx))
        return types.ListToolsResult(tools=[_listed(t) for t in tools])

    # No input schema lookup: the endpoint validates arguments against the bound tool's own schema.
    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:  # noqa: ANN401
        session = ctx.session

        # The SDK sends it against the caller's progress token, and drops it when the caller asked for none.
        async def report(elapsed: float) -> None:
            await session.report_progress(elapsed, message="waiting")

        arguments = dict(params.arguments or {})
        result = await endpoint.call_tool(claims(ctx), params.name, arguments, progress=report)
        meta = cast("dict[str, object] | None", getattr(params, "meta", None))
        if not wants_full_result(meta if isinstance(meta, dict) else None):
            result = await bound_engine_result(
                endpoint, result, tool=params.name, arguments=arguments, max_chars=endpoint_result_cap(endpoint)
            )
        return to_call_result(result)

    server: Server[Any] = Server(SERVER_NAME, on_list_tools=list_tools, on_call_tool=call_tool)
    if isinstance(endpoint, NotesConnects):
        server.middleware.append(_ConnectNoted(endpoint))
    return server


# What a client opens an MCP session with: the 2025 handshake, or the 2026-07-28 probe that replaces it.
HANDSHAKES = frozenset({"initialize", "server/discover"})


class _ConnectNoted:
    """Tells the endpoint of each handshake it answered (``NotesConnects``); a refused one raised past it."""

    def __init__(self, endpoint: NotesConnects) -> None:
        self._endpoint = endpoint

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:  # noqa: ANN401 - the SDK's context
        result = await call_next(ctx)
        if ctx.method in HANDSHAKES and ctx.request_id is not None:
            self._endpoint.note_connect()
        return result


@dataclass
class _Answer:
    """What one request was answered, as its metric reads it."""

    status: int = 500
    refusal: str | None = None
    route: str = UNMATCHED


@dataclass
class _Mount:
    binding: GatewayBinding
    manager: StreamableHTTPSessionManager


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            text = value.decode("latin-1").strip()
            scheme, _, token = text.partition(" ")
            return token.strip() if scheme.lower() == "bearer" and token.strip() else None
    return None


def _header(scope: Scope, wanted: bytes) -> str | None:
    for name, value in scope.get("headers", []):
        if name == wanted:
            return value.decode("latin-1")[:128]
    return None


async def _json(
    send: Send,
    status: int,
    body: dict[str, object],
    *,
    reason: str | None = None,
    extra: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store"), *extra]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="pipeshub-tool-gateway"'))
    if reason is not None:
        # Streaming MCP clients surface the status but not the body; the proxy logs headers too.
        headers.append((REFUSAL_HEADER, reason.encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class ToolGatewayApp:
    """The ASGI app; ``serving`` mounts one binding's endpoint for the life of the context."""

    def __init__(
        self,
        verifier: GatewayTokenVerifier,
        *,
        logs: HarnessLog | None = None,
        metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
    ) -> None:
        self._verifier = verifier
        self._mounts: dict[str, _Mount] = {}
        self._logs = logs or HarnessLog.named(LOGGER)
        self._metrics = metrics

    @contextlib.asynccontextmanager
    async def serving(self, endpoint: ToolEndpoint, binding: GatewayBinding) -> AsyncIterator[str]:
        """Yields the mount key: the binding's path is ``/{key}/mcp`` under this app."""
        key = secrets.token_urlsafe(16)
        manager = StreamableHTTPSessionManager(app=_server(endpoint), stateless=True)
        raised: Exception | None = None
        async with manager.run():
            self._mounts[key] = _Mount(binding, manager)
            try:
                yield key
            except Exception as exc:  # re-raised below, as itself
                raised = exc
            finally:
                del self._mounts[key]
        if raised is not None:
            # Outside the manager's task group, which would wrap it in an ExceptionGroup: the turn's caller tells a
            # lost lease, a refused save or an outage by its type (WP-82: run 6's kernel saw only ExceptionGroup).
            raise raised

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        started = time.monotonic()
        answered = _Answer()

        async def observed(message: Message) -> None:
            if message["type"] == "http.response.start":
                answered.status = message["status"]
                header = next((v.decode("latin-1") for n, v in message.get("headers", []) if n == REFUSAL_HEADER), None)
                answered.refusal = answered.refusal or header
            await send(message)

        try:
            await self._serve(scope, receive, observed, answered)
        finally:
            self._metrics.request(
                GatewayRequest(
                    gateway="tool",
                    route=answered.route,
                    outcome=outcome_of(answered.status, refused=answered.refusal is not None),
                    seconds=time.monotonic() - started,
                    reason=answered.refusal,
                )
            )

    async def _serve(self, scope: Scope, receive: Receive, send: Send, answered: _Answer) -> None:
        path = cast("str", scope.get("path", ""))
        root = cast("str", scope.get("root_path", ""))
        if root and path.startswith(root):
            path = path[len(root) :]
        key, _, rest = path.strip("/").partition("/")
        mount = self._mounts.get(key)
        if mount is None or rest.rstrip("/") != "mcp":
            await _json(send, 404, {"error": {"code": "GATEWAY_NOT_FOUND", "message": "no such binding"}})
            return
        answered.route = "mcp"
        proxy_request = _header(scope, PROXY_REQUEST_HEADER)
        try:
            claims = await self._verifier.verify(_bearer(scope))
            if claims.binding != mount.binding:
                raise BindingMismatch("the gateway token is for another binding")
        except GatewayRefusal as refusal:
            self._logs.warning(
                "gateway.refused",
                reason=refusal.reason_code,
                method=cast("str", scope.get("method", "")),
                proxyRequestId=proxy_request,
            )
            await _json(
                send,
                401,
                {"error": {"code": f"GATEWAY_{refusal.reason_code.upper()}", "reason": refusal.reason_code}},
                reason=refusal.reason_code,
            )
            return
        self._logs.debug(
            "gateway.request",
            method=cast("str", scope.get("method", "")),
            kid=claims.kid,
            proxyRequestId=proxy_request,
        )
        if scope.get("method") != "POST":
            # Stateless: no session to end (DELETE) and no server-initiated messages, so no GET stream; the SDK's
            # stateless GET would hold the connection open with nothing ever sent on it.
            answered.refusal = "method_not_allowed"
            await _json(
                send,
                405,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "only POST is served"}},
                extra=((b"allow", b"POST"),),
            )
            return
        await mount.manager.handle_request({**scope, CLAIMS_KEY: claims}, receive, send)


__all__ = ["AUTH_META", "CLAIMS_KEY", "PROXY_REQUEST_HEADER", "REFUSAL_HEADER", "ToolGatewayApp", "to_call_result"]
