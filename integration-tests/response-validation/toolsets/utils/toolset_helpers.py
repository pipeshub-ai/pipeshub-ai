"""HTTP and fixture helpers for toolsets response-validation integration tests."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import requests

from helper.pipeshub_client import PipeshubClient

logger = logging.getLogger("toolsets-integration-test")

__all__ = [
    "CREDENTIAL_FIELD_MAP",
    "FAKE_OBJECT_ID",
    "TARGET_AUTH_TYPES",
    "all_mock_credential_variants",
    "build_mock_auth_config",
    "create_oauth_test_instance",
    "create_test_instance",
    "delete",
    "delete_instance",
    "delete_no_auth",
    "delete_oauth_config",
    "delete_with_bad_token",
    "get",
    "get_first_registry_toolset_name",
    "get_no_auth",
    "get_with_bad_token",
    "make_mock_auth_body",
    "mock_auth_dict",
    "mock_value_for_field",
    "pick_oauth_toolset_for_testing",
    "pick_toolsets_for_testing",
    "post",
    "post_no_auth",
    "post_with_bad_token",
    "put",
    "put_no_auth",
    "put_with_bad_token",
]

FAKE_OBJECT_ID = "000000000000000000000000"  # valid 24-hex format but non-existent
_BAD_BEARER = "Bearer invalid.header.payload"

# Ordered list of non-OAuth auth types the credential tests want to exercise.
# pick_toolsets_for_testing tries to find one registry toolset per entry.
TARGET_AUTH_TYPES: list[str] = [
    "API_TOKEN",
    "BEARER_TOKEN",
    "BASIC_AUTH",
    "USERNAME_PASSWORD",
]

_NON_OAUTH_AUTH_TYPES = {
    "API_TOKEN",
    "BEARER_TOKEN",
    "USERNAME_PASSWORD",
    "BASIC_AUTH",
    "NONE",
}


def get(
    client: PipeshubClient,
    path: str,
    params: Optional[dict] = None,
) -> requests.Response:
    return requests.get(
        f"{client.base_url}{path}",
        headers=client._headers(),
        params=params,
        timeout=client.timeout_seconds,
    )


def get_first_registry_toolset_name(client: PipeshubClient) -> str:
    """Fetch registry and return the first toolset's name (type key)."""
    resp = get(client, "/api/v1/toolsets/registry")
    assert resp.status_code == 200
    toolsets = resp.json().get("toolsets", [])
    assert len(toolsets) > 0, "No toolsets in registry — cannot run schema tests"
    return toolsets[0]["name"]


def post(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    return requests.post(
        f"{client.base_url}{path}",
        headers=client._headers(),
        json=json,
        timeout=client.timeout_seconds,
    )


def put(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    return requests.put(
        f"{client.base_url}{path}",
        headers=client._headers(),
        json=json,
        timeout=client.timeout_seconds,
    )


def delete(
    client: PipeshubClient,
    path: str,
) -> requests.Response:
    return requests.delete(
        f"{client.base_url}{path}",
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )


def get_no_auth(
    client: PipeshubClient,
    path: str,
    params: Optional[dict] = None,
) -> requests.Response:
    """GET without an Authorization header."""
    return requests.get(
        f"{client.base_url}{path}",
        headers={"Content-Type": "application/json"},
        params=params,
        timeout=client.timeout_seconds,
    )


def post_no_auth(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    """POST without an Authorization header."""
    return requests.post(
        f"{client.base_url}{path}",
        headers={"Content-Type": "application/json"},
        json=json,
        timeout=client.timeout_seconds,
    )


def delete_no_auth(
    client: PipeshubClient,
    path: str,
) -> requests.Response:
    """DELETE without an Authorization header."""
    return requests.delete(
        f"{client.base_url}{path}",
        headers={"Content-Type": "application/json"},
        timeout=client.timeout_seconds,
    )


def delete_with_bad_token(
    client: PipeshubClient,
    path: str,
) -> requests.Response:
    """DELETE with a syntactically plausible but cryptographically invalid Bearer token."""
    return requests.delete(
        f"{client.base_url}{path}",
        headers={
            "Authorization": _BAD_BEARER,
            "Content-Type": "application/json",
        },
        timeout=client.timeout_seconds,
    )


def get_with_bad_token(
    client: PipeshubClient,
    path: str,
    params: Optional[dict] = None,
) -> requests.Response:
    """GET with a syntactically plausible but cryptographically invalid Bearer token."""
    return requests.get(
        f"{client.base_url}{path}",
        headers={
            "Authorization": _BAD_BEARER,
            "Content-Type": "application/json",
        },
        params=params,
        timeout=client.timeout_seconds,
    )


def post_with_bad_token(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    """POST with a syntactically plausible but cryptographically invalid Bearer token."""
    return requests.post(
        f"{client.base_url}{path}",
        headers={
            "Authorization": _BAD_BEARER,
            "Content-Type": "application/json",
        },
        json=json,
        timeout=client.timeout_seconds,
    )


def put_no_auth(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    """PUT without an Authorization header."""
    return requests.put(
        f"{client.base_url}{path}",
        headers={"Content-Type": "application/json"},
        json=json,
        timeout=client.timeout_seconds,
    )


def put_with_bad_token(
    client: PipeshubClient,
    path: str,
    json: Optional[dict] = None,
) -> requests.Response:
    """PUT with a syntactically plausible but cryptographically invalid Bearer token."""
    return requests.put(
        f"{client.base_url}{path}",
        headers={
            "Authorization": _BAD_BEARER,
            "Content-Type": "application/json",
        },
        json=json,
        timeout=client.timeout_seconds,
    )


def mock_value_for_field(field: dict) -> str:
    """Generate a plausible mock value based on the field's type and metadata."""
    field_type = field.get("fieldType", "TEXT").upper()
    name = field.get("name", "")
    placeholder = field.get("placeholder", "")

    if field_type == "URL":
        return placeholder or "https://mock-test.example.com"
    if field_type == "EMAIL":
        return placeholder or "mock-test@example.com"
    if field_type == "PASSWORD":
        return f"mock-secret-{uuid.uuid4().hex[:12]}"
    if field_type == "NUMBER":
        return placeholder or "0"
    if field_type == "CHECKBOX":
        return field.get("defaultValue", "false")

    # TEXT, SELECT, TEXTAREA — use placeholder if available, else derive from name
    if placeholder:
        return placeholder
    return f"mock-{name}-{uuid.uuid4().hex[:8]}"


def build_mock_auth_config(auth_schemas: dict, auth_type: str) -> dict:
    """Build a mock authConfig dict from the schema fields for the given auth type."""
    schema = auth_schemas.get(auth_type, {})
    fields = schema.get("fields", [])
    config: dict = {}
    for field in fields:
        if field.get("required", True):
            config[field["name"]] = mock_value_for_field(field)
    return config


# Maps each non-OAuth auth type to the exact field names its credential body must contain.
# Sending fields outside this set to the wrong auth type causes a backend 500
# (unguarded .strip() on None), so variants must be scoped to the instance's authType.
CREDENTIAL_FIELD_MAP: dict[str, list[str]] = {
    "API_TOKEN":         ["apiToken"],
    "BEARER_TOKEN":      ["bearerToken"],
    "BASIC_AUTH":        ["username", "password"],
    "USERNAME_PASSWORD": ["username", "password"],
}


def mock_auth_dict(auth_type: str) -> dict:
    """Return a fresh mock ``auth`` dict for *auth_type* (field names from CREDENTIAL_FIELD_MAP)."""
    fields = CREDENTIAL_FIELD_MAP.get(auth_type, ["apiToken"])
    auth: dict = {}
    for field in fields:
        if field == "apiToken":
            auth[field] = f"mock-token-{uuid.uuid4().hex[:16]}"
        elif field == "bearerToken":
            auth[field] = f"mock-bearer-{uuid.uuid4().hex[:24]}"
        elif field == "username":
            auth[field] = f"mock-user-{uuid.uuid4().hex[:8]}"
        elif field == "password":
            auth[field] = f"mock-pass-{uuid.uuid4().hex[:12]}"
    return auth


def all_mock_credential_variants(auth_type: str) -> list[tuple[str, dict]]:
    """Return three fresh ``(label, {"auth": {...}})`` bodies for *auth_type*.

    All three bodies contain exactly the fields required by *auth_type* so
    every iteration is accepted by the backend's field-presence check.
    Each call generates new random values so no two iterations share a string.
    """
    return [(f"{auth_type}_{i}", {"auth": mock_auth_dict(auth_type)}) for i in range(1, 4)]


def make_mock_auth_body(
    client: PipeshubClient,
    toolset_type: str,
    auth_type: str,
) -> dict:
    """Return ``{"auth": {...}}`` with plausible mock values for *auth_type*.

    Fetches the registry schema so field names match what the toolset
    expects, then falls back to type-specific defaults when the schema
    endpoint is unavailable or returns no fields for the given auth type.
    """
    try:
        schema_resp = get(client, f"/api/v1/toolsets/registry/{toolset_type}/schema")
        if schema_resp.status_code == 200:
            toolset_info = schema_resp.json().get("toolset", {})
            auth_schemas = (
                toolset_info.get("config", {}).get("auth", {}).get("schemas", {})
            )
            config = build_mock_auth_config(auth_schemas, auth_type)
            if config:
                return {"auth": config}
    except Exception:
        pass

    return {"auth": mock_auth_dict(auth_type)}


def pick_toolsets_for_testing(client: PipeshubClient) -> list[dict]:
    """Return one registry toolset candidate per entry in TARGET_AUTH_TYPES.

    Scans every toolset in the registry (up to limit=200).  For each toolset
    that supports one or more uncovered target auth types, records one candidate
    per newly-covered type.  Stops scanning as soon as all four types are filled.

    Types absent from the registry are silently skipped — call-sites log which
    types were actually covered.  Each entry contains:
        {"name": str, "authType": str, "authConfig": dict}
    """
    resp = get(client, "/api/v1/toolsets/registry", params={"limit": 200})
    assert resp.status_code == 200

    filled: dict[str, dict] = {}  # auth_type → candidate

    for t in resp.json().get("toolsets", []):
        if len(filled) == len(TARGET_AUTH_TYPES):
            break  # all target types covered — stop scanning

        schema_resp = get(
            client, f"/api/v1/toolsets/registry/{t['name']}/schema"
        )
        if schema_resp.status_code != 200:
            continue

        toolset_info = schema_resp.json()["toolset"]
        supported = toolset_info.get("supportedAuthTypes", [])
        auth_schemas = (
            toolset_info.get("config", {}).get("auth", {}).get("schemas", {})
        )

        for at in TARGET_AUTH_TYPES:
            if at in supported and at not in filled:
                mock_config = build_mock_auth_config(auth_schemas, at)
                filled[at] = {
                    "name": t["name"],
                    "authType": at,
                    "authConfig": mock_config,
                }

    return list(filled.values())


def create_test_instance(
    client: PipeshubClient,
    toolset_name: str,
    auth_type: str,
    auth_config: dict,
) -> dict:
    """Create a toolset instance with mock credentials. Returns the instance dict."""
    unique_name = f"rv-test-{toolset_name}-{uuid.uuid4().hex[:8]}"
    payload: dict = {
        "instanceName": unique_name,
        "toolsetType": toolset_name,
        "authType": auth_type,
    }
    if auth_config:
        payload["authConfig"] = auth_config

    resp = post(client, "/api/v1/toolsets/instances", json=payload)
    assert resp.status_code == 200, (
        f"Failed to create instance '{unique_name}': {resp.status_code}: {resp.text}"
    )
    return resp.json()["instance"]


def delete_instance(client: PipeshubClient, instance_id: str) -> None:
    """Best-effort cleanup — ignore errors."""
    try:
        delete(client, f"/api/v1/toolsets/instances/{instance_id}")
    except Exception:
        logger.error("Failed to delete instance %s", instance_id)


def pick_oauth_toolset_for_testing(client: PipeshubClient) -> dict | None:
    """Return the first registry toolset that supports OAUTH auth type.

    Scans every toolset in the registry (up to limit=200).  For each OAUTH-
    capable toolset it builds a mock authConfig populated with clientId and
    clientSecret so the caller can immediately use it to create an OAUTH
    instance without a live OAuth provider.

    Returns a dict with keys {name, authType, authConfig} or None when no
    OAUTH-supporting toolset exists in the registry.
    """
    resp = get(client, "/api/v1/toolsets/registry", params={"limit": 200})
    if resp.status_code != 200:
        return None

    for t in resp.json().get("toolsets", []):
        schema_resp = get(client, f"/api/v1/toolsets/registry/{t['name']}/schema")
        if schema_resp.status_code != 200:
            continue

        toolset_info = schema_resp.json().get("toolset", {})
        supported = toolset_info.get("supportedAuthTypes", [])

        if "OAUTH" not in supported:
            continue

        auth_schemas = toolset_info.get("config", {}).get("auth", {}).get("schemas", {})
        mock_config = build_mock_auth_config(auth_schemas, "OAUTH")

        # Ensure the minimum fields that _has_oauth_credentials checks for are present.
        if not mock_config.get("clientId"):
            mock_config["clientId"] = f"mock-client-id-{uuid.uuid4().hex[:16]}"
        if not mock_config.get("clientSecret"):
            mock_config["clientSecret"] = f"mock-secret-{uuid.uuid4().hex[:24]}"

        return {"name": t["name"], "authType": "OAUTH", "authConfig": mock_config}

    return None


def create_oauth_test_instance(
    client: PipeshubClient,
    toolset_name: str,
    auth_config: dict,
) -> dict:
    """Create an OAUTH-type toolset instance with mock credentials.

    Returns the full response dict (keys: status, instance, message).
    The ``instance`` value contains ``oauthConfigId`` when OAuth credentials
    are accepted by the backend.
    """
    unique_name = f"rv-oauth-{toolset_name}-{uuid.uuid4().hex[:8]}"
    payload: dict = {
        "instanceName": unique_name,
        "toolsetType": toolset_name,
        "authType": "OAUTH",
        "authConfig": auth_config,
        "baseUrl": "http://localhost:3001",
    }
    resp = post(client, "/api/v1/toolsets/instances", json=payload)
    assert resp.status_code == 200, (
        f"Failed to create OAUTH instance '{unique_name}': "
        f"{resp.status_code}: {resp.text}"
    )
    return resp.json()


def delete_oauth_config(
    client: PipeshubClient,
    toolset_type: str,
    oauth_config_id: str,
) -> None:
    """Best-effort deletion of an OAuth config — ignore errors."""
    try:
        delete(
            client,
            f"/api/v1/toolsets/oauth-configs/{toolset_type}/{oauth_config_id}",
        )
    except Exception:
        logger.error(
            "Failed to delete OAuth config %s for toolset %s",
            oauth_config_id,
            toolset_type,
        )
