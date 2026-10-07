"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/mcp-servers."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.second_user import SecondUser

MCP_SERVERS_BASE = "/api/v1/mcp-servers"
AGENTS_BASE = "/api/v1/agents"
PLATFORM_SETTINGS = "/api/v1/configurationManager/platform/settings"
MCP_FEATURE_FLAG = "ENABLE_MCP"

# Instance ids and agent keys are uuid4 strings; neither store validates the format,
# so an unknown one is a plain 404 from Python.
MISSING_INSTANCE_ID = "00000000-0000-4000-8000-000000000000"
MISSING_AGENT_KEY = "00000000-0000-4000-8000-000000000001"
MISSING_TYPE_ID = "spec-audit-no-such-type"
# A catalog STDIO server: allowed whatever MCP_ALLOW_CUSTOM_STDIO says, since it always runs
# the catalog's own command. Creating or authenticating it never starts the process.
CATALOG_STDIO_TYPE_ID = "exa"
CATALOG_STDIO_ENV = "EXA_API_KEY"
# Decodes to "bad%id", which guardPathParams refuses with a 400 before auth runs.
UNSAFE_PATH_ID = "bad%25id"

# Loopback and a closed port: nothing here is ever dialled by create/update, and
# OAuth discovery against it finds nothing.
UNREACHABLE_MCP_URL = "http://127.0.0.1:9/mcp"

# Tool the MCP fixture (integration-tests/mcp_fixture/server.py) exposes.
FIXTURE_TOOL = "lookup_order_status"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

# A token of the suite's own OAuth client that carries none of the mcp:* scopes.
NARROW_SCOPE = "org:read"

JsonObject = dict[str, Any]
SeedMcpInstance = Callable[..., JsonObject]
SeedAgent = Callable[..., str]


def instance_body(**overrides: Any) -> JsonObject:
    """A valid POST/PUT /instances body for a custom server with no auth; camelCase keys."""
    body: JsonObject = {
        "name": f"spec-audit-{uuid.uuid4().hex[:8]}",
        "transport": "streamable_http",
        "authMode": "none",
        "url": UNREACHABLE_MCP_URL,
        "description": "Seeded by the MCP servers spec audit",
    }
    body.update(overrides)
    return body


def oauth_instance_body(**overrides: Any) -> JsonObject:
    """A custom OAuth instance whose endpoints are stored, so authorize needs no discovery."""
    fields: JsonObject = {
        "authMode": "oauth",
        "authorizationUrl": "https://auth.invalid/authorize",
        "tokenUrl": "https://auth.invalid/token",
    }
    fields.update(overrides)
    return instance_body(**fields)


class McpServersClient(APIClient):
    """Client for /api/v1/mcp-servers, acting as the shared org admin.

    Routes without a method here are called with get/post/put/delete and a path
    relative to the router, e.g. ``client.post(f"/instances/{id}/authenticate", json=...)``.
    """

    BASE = MCP_SERVERS_BASE

    def list_instances(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/instances", auth=auth, **kwargs)

    def create_instance(self, body: JsonObject, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.post("/instances", auth=auth, json=body, **kwargs)

    def get_instance(self, instance_id: str, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get(f"/instances/{instance_id}", auth=auth, **kwargs)

    def update_instance(
        self, instance_id: str, body: JsonObject, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.put(f"/instances/{instance_id}", auth=auth, json=body, **kwargs)

    def delete_instance(self, instance_id: str, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.delete(f"/instances/{instance_id}", auth=auth, **kwargs)


def custom_stdio_allowed(client: McpServersClient) -> bool:
    """The deployment's MCP_ALLOW_CUSTOM_STDIO, as the catalog reports it."""
    resp = client.get("/catalog")
    assert resp.status_code == 200, resp.text[:300]
    return bool(resp.json()["customStdioAllowed"])


def agent_path(agent_key: str, instance_id: str | None = None, suffix: str = "") -> str:
    """Router-relative path of an agent-scoped route, e.g. ``agent_path(k, i, "/credentials")``."""
    if instance_id is None:
        return f"/agents/{agent_key}"
    return f"/agents/{agent_key}/instances/{instance_id}{suffix}"


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call an MCP servers route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{MCP_SERVERS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )


def mcp_fixture_connector_url() -> str:
    """Where the connectors service reaches the MCP fixture."""
    return os.getenv("MCP_FIXTURE_CONNECTOR_URL", "http://127.0.0.1:8097/mcp")


def mcp_fixture_healthy() -> bool:
    base = os.getenv("MCP_FIXTURE_TEST_URL", "http://127.0.0.1:8097").rstrip("/")
    try:
        return requests.get(f"{base}/__fixture__/health", timeout=10).ok
    except requests.RequestException:
        return False


def mint_narrow_scope_token(base_url: str, timeout: float = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


class FakeTokenEndpoint:
    """An OAuth token endpoint on a free local port that answers with ``reply``.

    ``reply`` is ``(status, json_body)``; every form the connectors service posts is
    kept in ``received``.
    """

    def __init__(self) -> None:
        self.reply: tuple[int, JsonObject] = (200, {"access_token": "spec-audit-access-token", "token_type": "Bearer"})
        self.received: list[str] = []
        self.url = ""


@contextmanager
def fake_token_endpoint() -> Iterator[FakeTokenEndpoint]:
    """The connectors service runs on this machine, so it reaches the server on 127.0.0.1."""
    endpoint = FakeTokenEndpoint()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
            length = int(self.headers.get("Content-Length") or 0)
            endpoint.received.append(self.rfile.read(length).decode())
            status, body = endpoint.reply
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint.url = f"http://127.0.0.1:{server.server_address[1]}/token"
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()


STATIC_OAUTH_CLIENT: JsonObject = {"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret"}


def connect_oauth_instance(
    client: McpServersClient, instance_id: str, token_endpoint: FakeTokenEndpoint
) -> None:
    """Run authorize and callback for the admin against ``token_endpoint``, storing its tokens.

    The instance must be an OAuth instance whose ``tokenUrl`` is ``token_endpoint.url``.
    """
    configured = client.put(f"/instances/{instance_id}/oauth-config", json=STATIC_OAUTH_CLIENT)
    assert configured.status_code == 200, f"storing the OAuth client: {configured.text[:500]}"
    authorize = client.get(f"/instances/{instance_id}/oauth/authorize")
    assert authorize.status_code == 200, f"starting the OAuth flow: {authorize.text[:500]}"
    state = parse_qs(urlparse(authorize.json()["authorizationUrl"]).query)["state"][0]
    callback = client.get("/oauth/callback", params={"code": "spec-audit-code", "state": state})
    assert callback.status_code == 200, callback.text[:500]
    assert callback.json().get("success") is True, f"completing the OAuth flow: {callback.text[:500]}"
