"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/mcp-servers."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

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
# Decodes to "bad%id", which guardPathParams refuses with a 400 before auth runs.
UNSAFE_PATH_ID = "bad%25id"

# Loopback and a closed port: nothing here is ever dialled by create/update, and
# OAuth discovery against it finds nothing.
UNREACHABLE_MCP_URL = "http://127.0.0.1:9/mcp"

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
    return instance_body(
        authMode="oauth",
        authorizationUrl="https://auth.invalid/authorize",
        tokenUrl="https://auth.invalid/token",
        **overrides,
    )


class McpServersClient(APIClient):
    """Client for /api/v1/mcp-servers, acting as the shared org admin.

    Routes without a method here are called with get/post/put/delete and a path
    relative to the router, e.g. ``client.post(f"/instances/{id}/authenticate", json=...)``.
    """

    BASE = MCP_SERVERS_BASE

    def list_instances(self, *, auth: bool = True) -> requests.Response:
        return self.get("/instances", auth=auth)

    def create_instance(self, body: JsonObject, *, auth: bool = True) -> requests.Response:
        return self.post("/instances", auth=auth, json=body)

    def get_instance(self, instance_id: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/instances/{instance_id}", auth=auth)

    def update_instance(
        self, instance_id: str, body: JsonObject, *, auth: bool = True
    ) -> requests.Response:
        return self.put(f"/instances/{instance_id}", auth=auth, json=body)

    def delete_instance(self, instance_id: str, *, auth: bool = True) -> requests.Response:
        return self.delete(f"/instances/{instance_id}", auth=auth)


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
