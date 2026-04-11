"""
Toolsets API – Response Validation Integration Tests
======================================================

Tests JSON-returning routes under /api/v1/toolsets against their YAML
response schemas.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response

Routes covered (read-only / safely reversible):
  GET  /api/v1/toolsets/registry                              — getRegistryToolsets
  GET  /api/v1/toolsets/registry/:toolsetType/schema          — getToolsetSchema
  GET  /api/v1/toolsets/my-toolsets                           — getMyToolsets
  GET  /api/v1/toolsets/instances                             — getToolsetInstances
  GET  /api/v1/toolsets/instances/:instanceId                 — getToolsetInstance
  GET  /api/v1/toolsets/instances/:instanceId/status          — getInstanceStatus
  POST + DELETE /api/v1/toolsets/instances                    — create then delete instance

Deprecated routes — not covered by these tests (prefer /instances/* and /my-toolsets):
  [DEPRECATED] POST   /api/v1/toolsets/
  [DEPRECATED] GET    /api/v1/toolsets/configured             — use GET /api/v1/toolsets/my-toolsets
  [DEPRECATED] GET    /api/v1/toolsets/:toolsetId/status
  [DEPRECATED] GET    /api/v1/toolsets/:toolsetId/config
  [DEPRECATED] POST   /api/v1/toolsets/:toolsetId/config
  [DEPRECATED] PUT    /api/v1/toolsets/:toolsetId/config
  [DEPRECATED] DELETE /api/v1/toolsets/:toolsetId/config
  [DEPRECATED] GET    /api/v1/toolsets/:toolsetId/oauth/authorize
  [DEPRECATED] POST   /api/v1/toolsets/:toolsetId/reauthenticate

Skipped (require external OAuth providers, agent keys, or specific instance state):
  GET    /oauth/callback                   — requires OAuth state from authorize flow
  POST   /instances/:id/authenticate       — requires valid credentials for instance
  PUT    /instances/:id/credentials        — requires existing credentials
  DELETE /instances/:id/credentials        — requires existing credentials
  POST   /instances/:id/reauthenticate     — requires OAuth-authenticated instance
  GET    /instances/:id/oauth/authorize    — requires OAuth-type instance
  GET    /oauth-configs/:type              — requires existing OAuth configs
  PUT    /oauth-configs/:type/:id          — requires existing OAuth config
  DELETE /oauth-configs/:type/:id          — requires existing OAuth config
  GET    /agents/:agentKey                 — requires valid agent key
  POST   /agents/:agentKey/instances/...   — requires valid agent + instance
  PUT    /agents/:agentKey/instances/...   — requires valid agent + instance
  DELETE /agents/:agentKey/instances/...   — requires valid agent + instance
  POST   /agents/:agentKey/.../reauthenticate — requires valid agent + instance
  GET    /agents/:agentKey/.../oauth/authorize — requires valid agent + OAuth instance

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

from __future__ import annotations

import logging
import random
import sys
import uuid
from pathlib import Path
from typing import Generator, Optional

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from response_validator import (  # noqa: E402
    assert_response_matches_schema,
    load_yaml_schemas,
)

logger = logging.getLogger("toolsets-integration-test")

# ------------------------------------------------------------------ #
# Load all toolsets response schemas
# ------------------------------------------------------------------ #
_SCHEMAS = load_yaml_schemas(
    "response-validation/schemas/toolsets/toolsets-response-schemas.yaml"
)

_SCHEMA_REGISTRY_LIST = _SCHEMAS["ToolsetListResponse"]
_SCHEMA_TOOLSET_SCHEMA = _SCHEMAS["GetToolsetSchemaResponse"]
_SCHEMA_MY_TOOLSETS = _SCHEMAS["GetMyToolsetsResponse"]
_SCHEMA_INSTANCES = _SCHEMAS["GetToolsetInstancesResponse"]
_SCHEMA_INSTANCE_DETAIL = _SCHEMAS["GetToolsetInstanceResponse"]
_SCHEMA_INSTANCE_STATUS = _SCHEMAS["GetInstanceStatusResponse"]
_SCHEMA_CREATE_INSTANCE = _SCHEMAS["CreateToolsetInstanceResponse"]
_SCHEMA_DELETE_INSTANCE = _SCHEMAS["DeleteToolsetInstanceResponse"]


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _get(
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


def _get_first_registry_toolset_name(client: PipeshubClient) -> str:
    """Fetch registry and return the first toolset's name (type key)."""
    resp = _get(client, "/api/v1/toolsets/registry")
    assert resp.status_code == 200
    toolsets = resp.json().get("toolsets", [])
    assert len(toolsets) > 0, "No toolsets in registry — cannot run schema tests"
    return toolsets[0]["name"]


def _post(
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


def _delete(
    client: PipeshubClient,
    path: str,
) -> requests.Response:
    return requests.delete(
        f"{client.base_url}{path}",
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )


def _mock_value_for_field(field: dict) -> str:
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


def _build_mock_auth_config(auth_schemas: dict, auth_type: str) -> dict:
    """Build a mock authConfig dict from the schema fields for the given auth type."""
    schema = auth_schemas.get(auth_type, {})
    fields = schema.get("fields", [])
    config: dict = {}
    for field in fields:
        if field.get("required", True):
            config[field["name"]] = _mock_value_for_field(field)
    return config


# Auth types that don't require external OAuth redirect flows.
_NON_OAUTH_AUTH_TYPES = {"API_TOKEN", "BEARER_TOKEN", "USERNAME_PASSWORD",
                         "BASIC_AUTH", "NONE"}


def _pick_toolsets_for_testing(client: PipeshubClient) -> list[dict]:
    """Return up to 2 registry toolsets with a chosen auth type and mock config.

    Prefers non-OAuth auth types (API_TOKEN, BASIC_AUTH, etc.) since OAuth
    requires external redirect flows.  Falls back to NONE if nothing else
    is available.  Each entry contains:
        {"name": str, "authType": str, "authConfig": dict}
    """
    resp = _get(client, "/api/v1/toolsets/registry", params={"limit": 200})
    assert resp.status_code == 200

    candidates: list[dict] = []
    for t in resp.json().get("toolsets", []):
        schema_resp = _get(
            client, f"/api/v1/toolsets/registry/{t['name']}/schema"
        )
        if schema_resp.status_code != 200:
            continue

        toolset_info = schema_resp.json()["toolset"]
        supported = toolset_info.get("supportedAuthTypes", [])
        auth_schemas = (
            toolset_info.get("config", {}).get("auth", {}).get("schemas", {})
        )

        # Pick the first non-OAuth auth type; fall back to NONE
        chosen = None
        for at in supported:
            if at in _NON_OAUTH_AUTH_TYPES and at != "NONE":
                chosen = at
                break
        if chosen is None:
            continue

        mock_config = _build_mock_auth_config(auth_schemas, chosen)
        candidates.append({
            "name": t["name"],
            "authType": chosen,
            "authConfig": mock_config,
        })
        if len(candidates) >= 2:
            break

    return candidates


def _create_test_instance(
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

    resp = _post(client, "/api/v1/toolsets/instances", json=payload)
    assert resp.status_code in (200, 201), (
        f"Failed to create instance '{unique_name}': {resp.status_code}: {resp.text}"
    )
    return resp.json()["instance"]


def _delete_instance(client: PipeshubClient, instance_id: str) -> None:
    """Best-effort cleanup — ignore errors."""
    try:
        _delete(client, f"/api/v1/toolsets/instances/{instance_id}")
    except Exception:
        logger.error("Failed to delete instance %s", instance_id)


# ------------------------------------------------------------------ #
# Module-scoped fixture: create test toolset instances
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def toolset_test_instances(
    pipeshub_client: PipeshubClient,
) -> Generator[list[dict], None, None]:
    """Create 1-2 toolset instances with mock credentials for tests.

    Discovers toolsets from the registry, picks non-OAuth auth types,
    generates mock credentials based on the schema fields, creates
    instances, yields them for test use, then cleans up.
    """
    client = pipeshub_client
    candidates = _pick_toolsets_for_testing(client)
    if not candidates:
        pytest.skip("No suitable toolsets found in registry for testing")

    created: list[dict] = []
    try:
        for ts in candidates:
            instance = _create_test_instance(
                client, ts["name"], ts["authType"], ts["authConfig"]
            )
            created.append(instance)
            logger.info(
                "Created test instance %s (%s, auth=%s)",
                instance["_id"], ts["name"], ts["authType"],
            )
        yield created
    finally:
        for inst in created:
            logger.info("Cleaning up test instance %s", inst["_id"])
            _delete_instance(client, inst["_id"])


# ====================================================================
# GET /api/v1/toolsets/registry
# ====================================================================
@pytest.mark.integration
class TestGetRegistryToolsets:
    """GET /api/v1/toolsets/registry — list all available toolsets."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema_defaults(self) -> None:
        """Default params — response must match ToolsetListResponse schema."""
        resp = _get(self.client, "/api/v1/toolsets/registry")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REGISTRY_LIST)

    def test_response_schema_with_pagination(self) -> None:
        """Explicit page=1&limit=5 — schema must still hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"page": 1, "limit": 5},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_REGISTRY_LIST)
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 5

    def test_response_schema_with_large_page(self) -> None:
        """page=1&limit=200 — maximum allowed limit."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"page": 1, "limit": 200},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REGISTRY_LIST)

    def test_response_schema_with_search(self) -> None:
        """search=nonexistent — empty toolsets array, schema must hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"search": "zzz_nonexistent_toolset_xyz"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_REGISTRY_LIST)
        assert body["pagination"]["total"] == 0

    def test_response_schema_with_include_tools_true(self) -> None:
        """include_tools=true — tools arrays should be populated."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"include_tools": "true", "limit": 3},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REGISTRY_LIST)

    def test_response_schema_with_include_tools_false(self) -> None:
        """include_tools=false — tools arrays may be empty."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"include_tools": "false", "limit": 3},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REGISTRY_LIST)

    def test_response_schema_page_2(self) -> None:
        """page=2&limit=2 — second page, schema must hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"page": 2, "limit": 2},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REGISTRY_LIST)


# ====================================================================
# GET /api/v1/toolsets/registry/:toolsetType/schema
# ====================================================================
@pytest.mark.integration
class TestGetToolsetSchema:
    """GET /api/v1/toolsets/registry/:toolsetType/schema"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_schema_has_tools_and_config(self) -> None:
        """Schema response must match GetToolsetSchemaResponse schema and include config and tools."""
        registry_resp = _get(self.client, "/api/v1/toolsets/registry")
        assert registry_resp.status_code == 200
        toolsets = registry_resp.json().get("toolsets", [])
        assert len(toolsets) > 0, "No toolsets in registry — cannot run schema tests"
        toolset_type = random.choice(toolsets)["name"]
        resp = _get(
            self.client,
            f"/api/v1/toolsets/registry/{toolset_type}/schema",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_TOOLSET_SCHEMA)
        toolset = body["toolset"]
        assert "config" in toolset
        assert isinstance(toolset["tools"], list)


# ====================================================================
# GET /api/v1/toolsets/my-toolsets
# ====================================================================
@pytest.mark.integration
class TestGetMyToolsets:
    """GET /api/v1/toolsets/my-toolsets — merged view of instances + auth status."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema_defaults(self) -> None:
        """Default params — response must match schema."""
        resp = _get(self.client, "/api/v1/toolsets/my-toolsets")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_response_schema_with_pagination(self) -> None:
        """page=1&limit=5 — schema must hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"page": 1, "limit": 5},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_MY_TOOLSETS)
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 5

    def test_response_schema_with_search(self) -> None:
        """search=nonexistent — empty results, schema must hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"search": "zzz_nonexistent_toolset_xyz"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_response_schema_include_registry_true(self) -> None:
        """includeRegistry=true — includes unconfigured registry entries."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"includeRegistry": "true"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_response_schema_include_registry_false(self) -> None:
        """includeRegistry=false — only configured instances."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"includeRegistry": "false"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_response_schema_auth_status_authenticated(self) -> None:
        """authStatus=authenticated — only authenticated toolsets."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"authStatus": "authenticated"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_response_schema_auth_status_not_authenticated(self) -> None:
        """authStatus=not-authenticated — only unauthenticated toolsets."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"authStatus": "not-authenticated"},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_filter_counts_present(self) -> None:
        """filterCounts must have all, authenticated, notAuthenticated."""
        resp = _get(self.client, "/api/v1/toolsets/my-toolsets")
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_MY_TOOLSETS)
        fc = body["filterCounts"]
        assert fc["all"] >= 0
        assert fc["authenticated"] >= 0
        assert fc["notAuthenticated"] >= 0
        assert fc["all"] == fc["authenticated"] + fc["notAuthenticated"]

    def test_response_schema_page_2(self) -> None:
        """page=2&limit=2 — second page, schema must hold."""
        resp = _get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"page": 2, "limit": 2},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_MY_TOOLSETS)

    def test_created_instances_appear(
        self, toolset_test_instances: list[dict]
    ) -> None:
        """Created test instances should appear in my-toolsets response."""
        resp = _get(self.client, "/api/v1/toolsets/my-toolsets")
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_MY_TOOLSETS)
        all_toolsets = body.get("toolsets", [])
        test_instance_ids = {inst["_id"] for inst in toolset_test_instances}
        found = [
            t for t in all_toolsets
            if t.get("_id") in test_instance_ids
            or t.get("instanceId") in test_instance_ids
        ]
        assert len(found) > 0, "Created test instances not found in my-toolsets"


# ====================================================================
# GET /api/v1/toolsets/instances
# ====================================================================
@pytest.mark.integration
class TestGetToolsetInstances:
    """GET /api/v1/toolsets/instances — list all instances for the org."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must match GetToolsetInstancesResponse schema."""
        resp = _get(self.client, "/api/v1/toolsets/instances")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_INSTANCES)


# ====================================================================
# GET /api/v1/toolsets/instances/:instanceId
# ====================================================================
@pytest.mark.integration
class TestGetToolsetInstance:
    """GET /api/v1/toolsets/instances/:instanceId — get a specific instance."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance_id = toolset_test_instances[0]["_id"]

    def test_response_schema(self) -> None:
        """Fetch test instance by ID — response must match schema."""
        resp = _get(
            self.client,
            f"/api/v1/toolsets/instances/{self.instance_id}",
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_INSTANCE_DETAIL)


# ====================================================================
# GET /api/v1/toolsets/instances/:instanceId/status
# ====================================================================
@pytest.mark.integration
class TestGetInstanceStatus:
    """GET /api/v1/toolsets/instances/:instanceId/status"""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance_id = toolset_test_instances[0]["_id"]

    def test_response_schema(self) -> None:
        """Fetch instance status — response must match schema."""
        resp = _get(
            self.client,
            f"/api/v1/toolsets/instances/{self.instance_id}/status",
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_INSTANCE_STATUS)


# ====================================================================
# POST + DELETE /api/v1/toolsets/instances (create then cleanup)
# ====================================================================
@pytest.mark.integration
class TestCreateAndDeleteToolsetInstance:
    """POST /api/v1/toolsets/instances + DELETE — create then delete."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_create_and_delete_response_schemas(self) -> None:
        """Create an instance, validate schema, then delete and validate."""
        # Get a valid toolset type from the registry
        toolset_type = _get_first_registry_toolset_name(self.client)

        # Get the supported auth types for this toolset
        schema_resp = _get(
            self.client,
            f"/api/v1/toolsets/registry/{toolset_type}/schema",
        )
        assert schema_resp.status_code == 200
        toolset_info = schema_resp.json()["toolset"]
        auth_types = toolset_info.get("supportedAuthTypes", [])
        assert len(auth_types) > 0, (
            f"Toolset {toolset_type} has no supported auth types"
        )
        auth_type = auth_types[0]

        # Create instance
        create_resp = _post(
            self.client,
            "/api/v1/toolsets/instances",
            json={
                "instanceName": f"integration-test-instance-{uuid.uuid4().hex[:8]}",
                "toolsetType": toolset_type,
                "authType": auth_type,
            }
        )
        assert create_resp.status_code in (200, 201), (
            f"Expected 200/201, got {create_resp.status_code}: {create_resp.text}"
        )
        create_body = create_resp.json()

        assert_response_matches_schema(create_body, _SCHEMA_CREATE_INSTANCE)

        instance_id = create_body["instance"]["_id"]

        # Delete instance (cleanup)
        delete_resp = _delete(
            self.client,
            f"/api/v1/toolsets/instances/{instance_id}",
        )
        assert delete_resp.status_code == 200, (
            f"Cleanup failed: {delete_resp.status_code}: {delete_resp.text}"
        )
        assert_response_matches_schema(delete_resp.json(), _SCHEMA_DELETE_INSTANCE)
