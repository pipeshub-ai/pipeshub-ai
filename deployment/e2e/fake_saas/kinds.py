"""MCP servers of every kind the product connects to, for the e2e lanes (MCP-E2E), and an OAuth provider for them.

A test makes each server through the control routes (``PUT /__control/servers/{id}``): its transport (streamable
HTTP or SSE, the MCP SDK's own server transports), what a request must carry (nothing, a bearer token, a named
header, an access token of this process's OAuth provider) and its tools, which a test can change while the stack runs
(a tool added, removed, or described differently: manifest drift). Every tool answers with its server, the server's
nonce and the digest of the credential the request carried, and each call is recorded the same way, so a test reads
"the product sent this credential" at the server without the value ever being stored in a record or printed.

    /s/{id}/mcp                       streamable HTTP
    /s/{id}/sse, /s/{id}/messages/    SSE
    /oauth/{id}/authorize|token|register, /.well-known/...   authorization code + PKCE, refresh, RFC 7591

The OAuth provider is a fake: it approves every authorization request of a registered client at once (no login
page) and its tokens are random strings. Control routes need ``X-E2E-Control``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode

import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

CONTROL_TOKEN = os.environ.get("FAKE_SAAS_CONTROL_TOKEN", "")
TRANSPORTS = ("streamable_http", "sse")
AUTH_KINDS = ("none", "bearer", "header", "oauth")
ACCESS_TOKEN_TTL_S = 3600
Json = dict[str, Any]


def digest(value: str | None) -> str:
    return "none" if value is None else hashlib.sha256(value.encode()).hexdigest()[:16]


@dataclass
class OAuthRealm:
    """One server's authorization server: its clients, the codes it handed out and the tokens that are live."""

    discoverable: bool = False
    clients: dict[str, str] = field(default_factory=dict[str, str])
    codes: dict[str, Json] = field(default_factory=dict[str, Json])
    access: set[str] = field(default_factory=set[str])
    refresh: dict[str, str] = field(default_factory=dict[str, str])
    issued: list[Json] = field(default_factory=list[Json])

    def issue(self, client_id: str, grant: str) -> Json:
        access = f"oat-{secrets.token_urlsafe(24)}"
        refresh = f"ort-{secrets.token_urlsafe(24)}"
        self.access.add(access)
        self.refresh[refresh] = client_id
        self.issued.append({"grant": grant, "accessDigest": digest(access), "refreshDigest": digest(refresh),
                            "clientDigest": digest(client_id), "at": time.time()})
        return {"access_token": access, "token_type": "Bearer", "refresh_token": refresh,
                "expires_in": ACCESS_TOKEN_TTL_S}


@dataclass
class FakeServer:
    id: str
    transport: str
    auth: str
    credential: str | None
    header: str
    tools: dict[str, Json]
    nonce: str = field(default_factory=lambda: secrets.token_hex(6))
    calls: list[Json] = field(default_factory=list[Json])
    refused: list[Json] = field(default_factory=list[Json])
    # The `tools/list` and `initialize` requests clients sent (R26-LAZY: an unused server gets none), counted as they
    # arrive: the SDK's stateless server also runs its list handler to check each `tools/call`, which no client asked.
    lists: int = 0
    initializes: int = 0
    realm: OAuthRealm = field(default_factory=OAuthRealm)
    mcp: Server[Any, Any] = field(init=False)
    http: StreamableHTTPSessionManager = field(init=False)
    sse: SseServerTransport = field(init=False)

    def __post_init__(self) -> None:
        async def _list(_ctx: Any, _params: Any) -> types.ListToolsResult:  # noqa: ANN401 - the SDK's handler shape
            return types.ListToolsResult(tools=[
                types.Tool(
                    name=name, description=str(spec.get("description") or name),
                    input_schema=dict(spec.get("inputSchema") or {"type": "object", "properties": {}}),
                    annotations=types.ToolAnnotations(
                        read_only_hint=bool(spec.get("readOnly", True)),
                        # SEC-B-02: a tool is an outside destination unless it says it stays closed-world.
                        open_world_hint=bool(spec.get("openWorld", False)),
                    ),
                )
                for name, spec in self.tools.items()
            ])

        async def _call(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:  # noqa: ANN401
            name, arguments = params.name, params.arguments or {}
            if name not in self.tools:
                return types.CallToolResult(content=[types.TextContent(type="text", text=f"unknown tool {name}")],
                                            is_error=True)
            request = ctx.request
            presented = self.presented(request.headers) if request is not None else None
            self.calls.append({"tool": name, "credentialDigest": digest(presented), "at": time.time(),
                               "argsDigest": digest(json.dumps(arguments, sort_keys=True))})
            text = (f"server={self.id} tool={name} nonce={self.nonce} credential={digest(presented)} "
                    f"n={len(self.calls)}")
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

        self.mcp = Server(f"e2e-{self.id}", on_list_tools=_list, on_call_tool=_call)
        self.http = StreamableHTTPSessionManager(app=self.mcp, stateless=True)
        self.sse = SseServerTransport(f"/s/{self.id}/messages/")

    def presented(self, headers: Any) -> str | None:  # noqa: ANN401 - a Starlette header map
        if self.auth == "none":
            return None
        if self.auth == "header":
            return headers.get(self.header)
        value = headers.get("authorization") or ""
        return value.removeprefix("Bearer ") if value.startswith("Bearer ") else None

    def admits(self, presented: str | None) -> bool:
        if self.auth == "none":
            return True
        if self.auth == "oauth":
            return presented is not None and presented in self.realm.access
        return presented is not None and secrets.compare_digest(presented, self.credential or "")

    def view(self) -> Json:
        return {
            "id": self.id, "transport": self.transport, "auth": self.auth, "nonce": self.nonce,
            "credentialDigest": digest(self.credential), "tools": sorted(self.tools), "lists": self.lists,
            "initializes": self.initializes,
            "calls": self.calls, "refused": self.refused,
            "oauth": {"clients": [digest(c) for c in self.realm.clients], "issued": self.realm.issued,
                      "live": sorted(digest(t) for t in self.realm.access)},
        }


class Kinds:
    """The process: its servers, each one's transport kept running by one supervisor task."""

    def __init__(self) -> None:
        self.servers: dict[str, FakeServer] = {}
        self._new: asyncio.Queue[tuple[FakeServer, asyncio.Event]] = asyncio.Queue()

    def start(self) -> asyncio.Task[None]:
        """The supervisor task of a process starting now: empty, on the running event loop (a test may start the
        application more than once in one process)."""
        self.servers = {}
        self._new = asyncio.Queue()
        return asyncio.ensure_future(self.supervise())

    async def supervise(self) -> None:
        """A session manager must be entered and left by one task; requests come and go, so this task owns them."""
        async with contextlib.AsyncExitStack() as stack:
            while True:
                server, ready = await self._new.get()
                await stack.enter_async_context(server.http.run())
                ready.set()

    async def put(self, server_id: str, body: Json) -> FakeServer:
        transport, auth = str(body.get("transport", "streamable_http")), str(body.get("auth", "none"))
        if transport not in TRANSPORTS or auth not in AUTH_KINDS:
            raise ValueError(f"transport is one of {TRANSPORTS}, auth one of {AUTH_KINDS}")
        tools = body.get("tools")
        if not isinstance(tools, dict):
            raise ValueError("tools is an object of name -> {description, inputSchema, readOnly}")
        existing = self.servers.get(server_id)
        if existing is not None:
            existing.tools = dict(tools)
            existing.credential = body.get("credential", existing.credential)
            return existing
        server = FakeServer(server_id, transport, auth, body.get("credential"), str(body.get("header", "x-api-key")),
                            dict(tools))
        server.realm.discoverable = bool(body.get("discoverable"))
        ready = asyncio.Event()
        await self._new.put((server, ready))
        await ready.wait()
        self.servers[server_id] = server
        return server


KINDS = Kinds()


async def _send(scope: Scope, receive: Receive, send: Send, response: Response) -> None:
    await response(scope, receive, send)


def _controlled(request: Request) -> bool:
    return bool(CONTROL_TOKEN) and request.headers.get("X-E2E-Control") == CONTROL_TOKEN


async def _control(request: Request, parts: list[str]) -> Response:
    if not _controlled(request):
        return JSONResponse({"error": "control token required"}, status_code=401)
    if parts == ["servers"] and request.method == "GET":
        return JSONResponse({"servers": [s.view() for s in KINDS.servers.values()]})
    if len(parts) >= 2 and parts[0] == "servers":
        server_id = parts[1]
        if request.method == "PUT" and len(parts) == 2:
            try:
                return JSONResponse((await KINDS.put(server_id, await request.json())).view())
            except ValueError as error:
                return JSONResponse({"error": str(error)}, status_code=400)
        server = KINDS.servers.get(server_id)
        if server is None:
            return JSONResponse({"error": f"no server {server_id}"}, status_code=404)
        if request.method == "GET" and len(parts) == 2:
            return JSONResponse(server.view())
        if request.method == "POST" and parts[2:] == ["oauth", "clients"]:
            body = await request.json()
            server.realm.clients[str(body["clientId"])] = str(body["clientSecret"])
            return JSONResponse({"ok": True})
        if request.method == "POST" and parts[2:] == ["oauth", "expire"]:
            # Every access token stops working; refresh tokens stay good (an expired token, K-8).
            expired = len(server.realm.access)
            server.realm.access.clear()
            return JSONResponse({"expired": expired})
        if request.method == "POST" and parts[2:] == ["oauth", "revoke"]:
            # The sign-in is gone, refresh tokens too: only a new sign-in works again (MCPNOTE-1).
            revoked = len(server.realm.access) + len(server.realm.refresh)
            server.realm.access.clear()
            server.realm.refresh.clear()
            return JSONResponse({"revoked": revoked})
    return JSONResponse({"error": "no such control route"}, status_code=404)


def _base(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def _metadata(request: Request, server: FakeServer) -> Json:
    issuer = f"{_base(request)}/oauth/{server.id}"
    return {"issuer": issuer, "authorization_endpoint": f"{issuer}/authorize", "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"]}


def _well_known(request: Request, path: str) -> Response:
    """RFC 9728 and RFC 8414 documents, for the servers made ``discoverable`` only."""
    kind, _, rest = path.removeprefix("/.well-known/").partition("/")
    segments = [p for p in rest.split("/") if p]
    server = KINDS.servers.get(segments[1]) if len(segments) >= 2 and segments[0] in ("s", "oauth") else None
    if server is None or not server.realm.discoverable:
        return JSONResponse({"error": "not found"}, status_code=404)
    if kind == "oauth-protected-resource":
        return JSONResponse({"resource": f"{_base(request)}/s/{server.id}/mcp",
                             "authorization_servers": [f"{_base(request)}/oauth/{server.id}"]})
    if kind == "oauth-authorization-server":
        return JSONResponse(_metadata(request, server))
    return JSONResponse({"error": "not found"}, status_code=404)


def _oauth_error(code: str, status: int = 400) -> Response:
    return JSONResponse({"error": code}, status_code=status)


def _pkce_holds(verifier: str, challenge: str) -> bool:
    made = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return secrets.compare_digest(made, challenge)


async def _oauth(request: Request, server: FakeServer, action: str) -> Response:
    realm = server.realm
    if action == "register" and request.method == "POST":
        if not realm.discoverable:
            return _oauth_error("registration_not_supported", 404)
        body = await request.json()
        client_id, client_secret = f"dcr-{secrets.token_hex(8)}", f"dcs-{secrets.token_urlsafe(24)}"
        realm.clients[client_id] = client_secret
        return JSONResponse({"client_id": client_id, "client_secret": client_secret,
                             "redirect_uris": body.get("redirect_uris") or [],
                             "token_endpoint_auth_method": "client_secret_post"}, status_code=201)
    if action == "authorize" and request.method == "GET":
        query = request.query_params
        if query.get("client_id") not in realm.clients or query.get("response_type") != "code":
            return _oauth_error("unauthorized_client")
        if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
            return _oauth_error("invalid_request")
        code = f"code-{secrets.token_urlsafe(16)}"
        realm.codes[code] = {"client": query["client_id"], "redirect": query.get("redirect_uri"),
                             "challenge": query["code_challenge"]}
        return RedirectResponse(f"{query.get('redirect_uri')}?{urlencode({'code': code, 'state': query.get('state', '')})}",
                                status_code=302)
    if action == "token" and request.method == "POST":
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        client_id = form.get("client_id", "")
        if realm.clients.get(client_id) is None or realm.clients[client_id] != form.get("client_secret"):
            return _oauth_error("invalid_client", 401)
        if form.get("grant_type") == "authorization_code":
            pending = realm.codes.pop(form.get("code", ""), None)
            if (pending is None or pending["client"] != client_id or pending["redirect"] != form.get("redirect_uri")
                    or not _pkce_holds(form.get("code_verifier", ""), pending["challenge"])):
                return _oauth_error("invalid_grant")
            return JSONResponse(realm.issue(client_id, "authorization_code"))
        if form.get("grant_type") == "refresh_token":
            if realm.refresh.pop(form.get("refresh_token", ""), None) != client_id:
                return _oauth_error("invalid_grant")
            return JSONResponse(realm.issue(client_id, "refresh_token"))
        return _oauth_error("unsupported_grant_type")
    return _oauth_error("not_found", 404)


def _counting_requests(server: FakeServer, receive: Receive) -> Receive:
    """``receive`` that counts each JSON-RPC ``initialize`` and ``tools/list`` in a request's body as it is read."""
    body = bytearray()

    async def counted() -> Message:
        message = await receive()
        if message["type"] == "http.request":
            body.extend(message.get("body", b""))
            if not message.get("more_body"):
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    parsed = json.loads(bytes(body))
                    batch = parsed if isinstance(parsed, list) else [parsed]
                    methods = [m.get("method") for m in batch if isinstance(m, dict)]
                    server.initializes += methods.count("initialize")
                    server.lists += methods.count("tools/list")
        return message

    return counted


async def _serve_mcp(scope: Scope, receive: Receive, send: Send, server: FakeServer, tail: str) -> None:
    request = Request(scope, receive)
    presented = server.presented(request.headers)
    if not server.admits(presented):
        server.refused.append({"credentialDigest": digest(presented), "path": tail, "at": time.time()})
        challenge = 'Bearer error="invalid_token"' if server.auth in ("bearer", "oauth") else "ApiKey"
        await _send(scope, receive, send, JSONResponse({"error": "invalid_token", "error_description": "token expired"},
                                                       status_code=401, headers={"WWW-Authenticate": challenge}))
        return
    if server.transport == "streamable_http" and tail == "mcp":
        await server.http.handle_request(scope, _counting_requests(server, receive), send)
    elif server.transport == "sse" and tail == "sse" and scope["method"] == "GET":
        async with server.sse.connect_sse(scope, receive, send) as (read, write):
            await server.mcp.run(read, write, server.mcp.create_initialization_options())
    elif server.transport == "sse" and tail.rstrip("/") == "messages" and scope["method"] == "POST":
        await server.sse.handle_post_message(scope, _counting_requests(server, receive), send)
    else:
        await _send(scope, receive, send, JSONResponse({"error": "not found"}, status_code=404))


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "lifespan":
        supervisor: asyncio.Task[None] | None = None
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                supervisor = KINDS.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if supervisor is not None:
                    _ = supervisor.cancel()
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    path: str = scope["path"]
    request = Request(scope, receive)
    parts = [p for p in path.split("/") if p]
    if path == "/healthz":
        response: Response = JSONResponse({"ok": True})
    elif parts[:1] == ["__control"]:
        response = await _control(request, parts[1:])
    elif path.startswith("/.well-known/"):
        response = _well_known(request, path)
    elif len(parts) == 3 and parts[0] == "oauth" and parts[1] in KINDS.servers:
        response = await _oauth(request, KINDS.servers[parts[1]], parts[2])
    elif len(parts) >= 3 and parts[0] == "s" and parts[1] in KINDS.servers:
        await _serve_mcp(scope, receive, send, KINDS.servers[parts[1]], "/".join(parts[2:]))
        return
    else:
        response = JSONResponse({"error": "not found"}, status_code=404)
    await _send(scope, receive, send, response)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
