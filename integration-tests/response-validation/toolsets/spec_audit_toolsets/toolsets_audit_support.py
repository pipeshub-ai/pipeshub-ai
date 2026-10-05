"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/toolsets."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import requests
from filelock import FileLock

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

TOOLSETS_BASE = "/api/v1/toolsets"
AGENTS_BASE = "/api/v1/agents"

# In the registry on every edition, with both an OAUTH and an API_TOKEN auth type.
TOOLSET_TYPE = "jira"
MISSING_TOOLSET_TYPE = "spec-audit-no-such-toolset"

# Instance ids, OAuth config ids and agent keys are uuid4 strings; no store validates
# the format, so an unknown one is a plain 404 from Python.
MISSING_INSTANCE_ID = "00000000-0000-4000-8000-000000000000"
MISSING_AGENT_KEY = "00000000-0000-4000-8000-000000000001"
MISSING_OAUTH_CONFIG_ID = "00000000-0000-4000-8000-000000000002"
# Python has no handler for the legacy /:toolsetId/* routes; any id reaches the same 404.
MISSING_TOOLSET_ID = "00000000-0000-4000-8000-000000000003"
# Decodes to "bad%id", which guardPathParams refuses with a 400 before auth runs.
UNSAFE_PATH_ID = "bad%25id"

# The credential fields Jira declares for API_TOKEN; any other key is refused with a 400.
API_TOKEN_AUTH: dict[str, str] = {
    "baseUrl": "https://spec-audit.invalid",
    "email": "spec-audit@example.com",
    "apiToken": "spec-audit-token",
}
# Never sent anywhere: the authorize URL is only built, and no test completes the flow.
OAUTH_CLIENT_AUTH: dict[str, str] = {
    "clientId": "spec-audit-client-id",
    "clientSecret": "spec-audit-client-secret",
}

JsonObject = dict[str, Any]
SeedToolsetInstance = Callable[..., JsonObject]
SeedAgent = Callable[..., str]


def toolset_store_lock() -> FileLock:
    """Serialises writes to the instance and OAuth-config lists across xdist workers.

    Python stores each as one list and rewrites it whole, so two concurrent
    create/update/delete calls lose one of the writes.
    """
    return FileLock(str(Path(tempfile.gettempdir()) / "spec_audit_toolsets_store.lock"))


def instance_body(**overrides: Any) -> JsonObject:
    """A valid POST /instances body for an API_TOKEN Jira instance; camelCase keys."""
    body: JsonObject = {
        "instanceName": f"spec-audit-{uuid.uuid4().hex[:8]}",
        "toolsetType": TOOLSET_TYPE,
        "authType": "API_TOKEN",
    }
    body.update(overrides)
    return body


def oauth_instance_body(**overrides: Any) -> JsonObject:
    """An OAUTH Jira instance; creating it also creates an OAuth config named after it."""
    return instance_body(authType="OAUTH", authConfig=dict(OAUTH_CLIENT_AUTH), **overrides)


class ToolsetsClient(APIClient):
    """Client for /api/v1/toolsets, acting as the shared org admin.

    Routes without a method here are called with get/post/put/delete and a path
    relative to the router, e.g. ``client.post(f"/instances/{id}/authenticate", json=...)``.
    """

    BASE = TOOLSETS_BASE

    def list_instances(self, *, auth: bool = True, **params: Any) -> requests.Response:
        return self.get("/instances", auth=auth, params=params)

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

    def list_oauth_configs(self, toolset_type: str, *, auth: bool = True) -> requests.Response:
        return self.get(f"/oauth-configs/{toolset_type}", auth=auth)

    def delete_oauth_config(
        self, toolset_type: str, oauth_config_id: str, *, auth: bool = True
    ) -> requests.Response:
        return self.delete(f"/oauth-configs/{toolset_type}/{oauth_config_id}", auth=auth)


def agent_path(agent_key: str, instance_id: str | None = None, suffix: str = "") -> str:
    """Router-relative path of an agent-scoped route, e.g. ``agent_path(k, i, "/credentials")``."""
    if instance_id is None:
        return f"/agents/{agent_key}"
    return f"/agents/{agent_key}/instances/{instance_id}{suffix}"


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a toolsets route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    return requests.request(
        method,
        f"{user.base_url}{TOOLSETS_BASE}{path}",
        headers=user.headers,
        **kwargs,
    )
