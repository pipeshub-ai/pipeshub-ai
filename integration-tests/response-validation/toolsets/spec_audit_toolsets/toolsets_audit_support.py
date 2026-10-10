"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/toolsets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

import redis
import requests
from filelock import FileLock
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.oauth_token_helper import _decrypt, _derive_key, _encrypt
from helper.second_user import SecondUser

TOOLSETS_BASE = "/api/v1/toolsets"
AGENTS_BASE = "/api/v1/agents"

# In the registry on every edition, with both an OAUTH and an API_TOKEN auth type.
TOOLSET_TYPE = "jira"
MISSING_TOOLSET_TYPE = "spec-audit-no-such-toolset"
# Supports OAUTH and runs no setup check against the provider once a flow completes.
OAUTH_ONLY_LOCAL_TOOLSET_TYPE = "github"

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

# The legacy /:toolsetId/* routes paste the id into a Python URL. These three ids turn
# that URL into one Python does serve (or serves for another method only).
INSTANCES_SEGMENT = "instances"
OAUTH_CONFIGS_SEGMENT = "oauth-configs"
AGENTS_SEGMENT = "agents"
RESERVED_SEGMENTS = (INSTANCES_SEGMENT, OAUTH_CONFIGS_SEGMENT, AGENTS_SEGMENT)

# What FastAPI answers for a URL no Python route matches, relayed by Node.
NO_BACKEND_ROUTE = "Not Found"
INSTANCE_NOT_FOUND = "This toolset was removed, or you no longer have access. Refresh the page and try again."
CREDENTIALS_REQUIRED = "Credentials are required."
NO_SAVED_CREDENTIALS = "No existing credentials found for this instance. Please authenticate first."
OAUTH_INSTANCE_CREDENTIALS_REFUSAL = (
    "OAuth toolsets sign in through the OAuth flow. Use reauthenticate to start a new one."
)
AGENT_EDIT_REFUSAL = "You do not have permission to manage toolsets for this agent."
REGULAR_AGENT_REFUSAL = (
    "Per-agent toolsets credentials only apply to service account agents. "
    "For regular agents, configure credentials in Settings \u2192 Toolsets."
)
# What Python's OAuth-config list answers for a toolset type that has none.
NO_OAUTH_CONFIGS: dict[str, Any] = {"status": "success", "oauthConfigs": [], "total": 0}

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
# The global sanitizer refuses any query or body string that holds markup.
HTML_VALUE = "<b>spec-audit</b>"
HTML_REFUSAL = "HTML tags, scripts, and XSS content are not allowed"
# A scope the suite's OAuth client holds and no toolsets route accepts.
NARROW_SCOPE = "org:read"

JsonObject = dict[str, Any]
SeedToolsetInstance = Callable[..., JsonObject]
SeedAgent = Callable[..., str]
DeleteInstanceLater = Callable[[JsonObject], None]


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


def mint_narrow_scope_token(base_url: str, timeout: int = 60) -> str:
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
    """Remove the stored row of an access token this suite minted (there is no delete API).

    Node keeps one row per issued token, keyed by the token's SHA-256; without the row
    the token is refused as revoked.
    """
    client: MongoClient[JsonObject] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()


def oauth_state(instance_id: str, user_id: str, *, state: str = "spec-audit-state", is_agent: bool = False) -> str:
    """An OAuth callback ``state`` in the encoding Python's authorize routes produce.

    For an agent flow ``user_id`` carries the agent key.
    """
    data: JsonObject = {"state": state, "instance_id": instance_id, "user_id": user_id}
    if is_agent:
        data["is_agent"] = True
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def mark_code_as_exchanged(instance_id: str, user_id: str, code: str) -> None:
    """Rewrite the user's stored OAuth record so that ``code`` reads as already exchanged.

    A real exchange needs the provider's token endpoint, which the registry fixes and no
    request can redirect. What the exchange leaves behind is a token and the code in
    ``used_codes``; with both in place the callback takes its "duplicate callback" branch
    and completes without calling the provider. The record must exist already (the
    authorize route creates it) and is deleted with the instance.
    """
    secret = os.environ.get("SECRET_KEY")
    assert secret, "SECRET_KEY (the backend's) is needed to rewrite a stored OAuth record"
    key_bytes = _derive_key(secret)
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD") or None,
        db=int(os.environ.get("REDIS_DB", "0")),
    )
    try:
        # Matched by suffix: the namespace and key prefix are the backend's own settings.
        keys = list(client.scan_iter(match=f"*/services/toolsets/{instance_id}/{user_id}", count=1000))
        assert len(keys) == 1, f"expected one stored OAuth record for the instance and user, found {len(keys)}"
        stored = client.get(keys[0])
        assert isinstance(stored, bytes)
        record = json.loads(_decrypt(key_bytes, json.loads(stored.decode())))
        record["credentials"] = {"access_token": "spec-audit-access-token", "token_type": "Bearer"}
        record.setdefault("oauth", {})["used_codes"] = [code]
        client.set(keys[0], json.dumps(_encrypt(key_bytes, json.dumps(record))).encode())
    finally:
        client.close()


def agent_not_found(agent_key: str) -> str:
    return f"Agent '{agent_key}' not found."


def decode_state(state: str) -> JsonObject:
    """The JSON object inside an OAuth ``state`` produced by an authorize route."""
    decoded: JsonObject = json.loads(base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)))
    return decoded


def assert_bad_request(resp: requests.Response, message: str) -> None:
    """A 400 the backend wrote by hand (not the Node validator's VALIDATION_ERROR)."""
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message), resp.text[:500]


def assert_forbidden(resp: requests.Response, message: str) -> None:
    assert resp.status_code == 403, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_FORBIDDEN", message), resp.text[:500]


def error_of(resp: requests.Response) -> JsonObject:
    """The ``error`` object of an ErrorResponse body."""
    body = resp.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), resp.text[:500]
    return body["error"]


def assert_backend_failure(resp: requests.Response) -> None:
    """A 500 Node made out of a backend answer it does not relay (a 405, or a crash)."""
    assert resp.status_code == 500, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_INTERNAL_SERVER_ERROR", resp.text[:500]
    assert error["message"].startswith("Something went wrong while PipesHub tried to "), resp.text[:500]


def assert_not_found(resp: requests.Response, message: str) -> None:
    assert resp.status_code == 404, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", message), resp.text[:500]


def rejected_fields(resp: requests.Response) -> set[str]:
    """Field paths the Node request validator named in a 400 VALIDATION_ERROR."""
    error = error_of(resp)
    assert error["code"] == "VALIDATION_ERROR", resp.text[:500]
    return {entry["field"] for entry in error["metadata"]["errors"]}


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


# One reachable URL per operation, for the behaviours every operation shares:
# (id, method, path relative to the router, route template relative to the router).
SHARED_BEHAVIOUR_OPERATIONS: list[tuple[str, str, str, str]] = [
    ("registry", "GET", "/registry", "/registry"),
    ("registry_schema", "GET", f"/registry/{TOOLSET_TYPE}/schema", "/registry/:toolsetType/schema"),
    ("legacy_create", "POST", "", ""),
    ("configured", "GET", "/configured", "/configured"),
    ("legacy_status", "GET", f"/{MISSING_TOOLSET_ID}/status", "/:toolsetId/status"),
    ("legacy_config_get", "GET", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config"),
    ("legacy_config_save", "POST", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config"),
    ("legacy_config_update", "PUT", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config"),
    ("legacy_config_delete", "DELETE", f"/{MISSING_TOOLSET_ID}/config", "/:toolsetId/config"),
    ("legacy_reauthenticate", "POST", f"/{MISSING_TOOLSET_ID}/reauthenticate", "/:toolsetId/reauthenticate"),
    ("legacy_authorize", "GET", f"/{MISSING_TOOLSET_ID}/oauth/authorize", "/:toolsetId/oauth/authorize"),
    ("oauth_callback", "GET", "/oauth/callback", "/oauth/callback"),
    ("my_toolsets", "GET", "/my-toolsets", "/my-toolsets"),
    ("instances_list", "GET", "/instances", "/instances"),
    ("instances_create", "POST", "/instances", "/instances"),
    ("instance_get", "GET", f"/instances/{MISSING_INSTANCE_ID}", "/instances/:instanceId"),
    ("instance_update", "PUT", f"/instances/{MISSING_INSTANCE_ID}", "/instances/:instanceId"),
    ("instance_delete", "DELETE", f"/instances/{MISSING_INSTANCE_ID}", "/instances/:instanceId"),
    (
        "instance_authenticate",
        "POST",
        f"/instances/{MISSING_INSTANCE_ID}/authenticate",
        "/instances/:instanceId/authenticate",
    ),
    (
        "instance_credentials_update",
        "PUT",
        f"/instances/{MISSING_INSTANCE_ID}/credentials",
        "/instances/:instanceId/credentials",
    ),
    (
        "instance_credentials_delete",
        "DELETE",
        f"/instances/{MISSING_INSTANCE_ID}/credentials",
        "/instances/:instanceId/credentials",
    ),
    (
        "instance_reauthenticate",
        "POST",
        f"/instances/{MISSING_INSTANCE_ID}/reauthenticate",
        "/instances/:instanceId/reauthenticate",
    ),
    (
        "instance_authorize",
        "GET",
        f"/instances/{MISSING_INSTANCE_ID}/oauth/authorize",
        "/instances/:instanceId/oauth/authorize",
    ),
    ("instance_status", "GET", f"/instances/{MISSING_INSTANCE_ID}/status", "/instances/:instanceId/status"),
    ("oauth_configs_list", "GET", f"/oauth-configs/{TOOLSET_TYPE}", "/oauth-configs/:toolsetType"),
    (
        "oauth_config_update",
        "PUT",
        f"/oauth-configs/{TOOLSET_TYPE}/{MISSING_OAUTH_CONFIG_ID}",
        "/oauth-configs/:toolsetType/:oauthConfigId",
    ),
    (
        "oauth_config_delete",
        "DELETE",
        f"/oauth-configs/{TOOLSET_TYPE}/{MISSING_OAUTH_CONFIG_ID}",
        "/oauth-configs/:toolsetType/:oauthConfigId",
    ),
    ("agent_toolsets", "GET", f"/agents/{MISSING_AGENT_KEY}", "/agents/:agentKey"),
    (
        "agent_authenticate",
        "POST",
        f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}/authenticate",
        "/agents/:agentKey/instances/:instanceId/authenticate",
    ),
    (
        "agent_credentials_update",
        "PUT",
        f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}/credentials",
        "/agents/:agentKey/instances/:instanceId/credentials",
    ),
    (
        "agent_credentials_delete",
        "DELETE",
        f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}/credentials",
        "/agents/:agentKey/instances/:instanceId/credentials",
    ),
    (
        "agent_reauthenticate",
        "POST",
        f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}/reauthenticate",
        "/agents/:agentKey/instances/:instanceId/reauthenticate",
    ),
    (
        "agent_authorize",
        "GET",
        f"/agents/{MISSING_AGENT_KEY}/instances/{MISSING_INSTANCE_ID}/oauth/authorize",
        "/agents/:agentKey/instances/:instanceId/oauth/authorize",
    ),
]
