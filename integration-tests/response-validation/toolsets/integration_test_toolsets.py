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
  GET    /api/v1/toolsets/registry                                  — getRegistryToolsets
  GET    /api/v1/toolsets/registry/:toolsetType/schema              — getToolsetSchema
  GET    /api/v1/toolsets/my-toolsets                               — getMyToolsets
  GET    /api/v1/toolsets/instances                                  — getToolsetInstances
  GET    /api/v1/toolsets/instances/:instanceId                      — getToolsetInstance
  GET    /api/v1/toolsets/instances/:instanceId/status               — getInstanceStatus
  POST   /api/v1/toolsets/instances                                  — createToolsetInstance
  DELETE /api/v1/toolsets/instances/:instanceId                      — deleteToolsetInstance
  PUT    /api/v1/toolsets/instances/:instanceId                      — updateToolsetInstance
  POST   /api/v1/toolsets/instances/:instanceId/authenticate         — authenticateToolsetInstance
  PUT    /api/v1/toolsets/instances/:instanceId/credentials          — updateToolsetCredentials
  DELETE /api/v1/toolsets/instances/:instanceId/credentials          — removeToolsetCredentials
  GET    /api/v1/toolsets/oauth-configs/:toolsetType                 — listToolsetOAuthConfigs
  PUT    /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId  — updateToolsetOAuthConfig
  DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId  — deleteToolsetOAuthConfig

Deprecated routes — not covered by these tests:
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
  POST   /instances/:id/reauthenticate     — requires OAuth-authenticated instance
  GET    /instances/:id/oauth/authorize    — requires OAuth-type instance
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
import sys
import uuid
from itertools import product
from pathlib import Path
from typing import Generator

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RV_ROOT = Path(__file__).resolve().parents[1]
_RV_HELPER = _RV_ROOT / "helper"
for _p in (_RV_ROOT, _ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from toolsets.utils.toolset_helpers import (  # noqa: E402
    FAKE_OBJECT_ID,
    TARGET_AUTH_TYPES,
    all_mock_credential_variants,
    create_oauth_test_instance,
    create_test_instance,
    delete,
    delete_instance,
    delete_no_auth,
    delete_oauth_config,
    delete_with_bad_token,
    get,
    get_first_registry_toolset_name,
    get_no_auth,
    get_with_bad_token,
    make_mock_auth_body,
    pick_oauth_toolset_for_testing,
    pick_toolsets_for_testing,
    post,
    post_no_auth,
    post_with_bad_token,
    put,
    put_no_auth,
    put_with_bad_token,
)

logger = logging.getLogger("toolsets-integration-test")


# ------------------------------------------------------------------ #
# Module-scoped fixture: create test toolset instances
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def toolset_test_instances(
    pipeshub_client: PipeshubClient,
) -> Generator[list[dict], None, None]:
    """Create one toolset instance per entry in TARGET_AUTH_TYPES.

    Scans the registry for a toolset supporting each of API_TOKEN, BEARER_TOKEN,
    BASIC_AUTH, and USERNAME_PASSWORD.  Auth types not found in the registry are
    silently skipped and logged as warnings so the operator can see gaps.
    """
    client = pipeshub_client
    candidates = pick_toolsets_for_testing(client)
    if not candidates:
        pytest.skip("No suitable toolsets found in registry for testing")

    covered = {c["authType"] for c in candidates}
    missing = [at for at in TARGET_AUTH_TYPES if at not in covered]
    if missing:
        logger.warning(
            "Auth types not found in registry — those variants will be skipped: %s",
            missing,
        )
    logger.info("Auth types covered by test instances: %s", sorted(covered))

    created: list[dict] = []
    try:
        for ts in candidates:
            instance = create_test_instance(
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
            delete_instance(client, inst["_id"])


# ====================================================================
# GET /api/v1/toolsets/registry
# ====================================================================
@pytest.mark.integration
class TestGetRegistryToolsets:
    """GET /api/v1/toolsets/registry — list all available toolsets."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_toolset_registry_response_schema(self) -> None:
        """All valid query-parameter combinations must return 200 and match schema."""
        pages = [None, 1, 2]
        limits = [None, 5, 200]
        searches = [None, "zzz_nonexistent_toolset_xyz"]
        include_tools_options = [None, True, False]

        for page, limit, search, include_tools in product(
            pages, limits, searches, include_tools_options
        ):
            params: dict = {}
            if page is not None:
                params["page"] = page
            if limit is not None:
                params["limit"] = limit
            if search is not None:
                params["search"] = search
            if include_tools is not None:
                params["include_tools"] = str(include_tools).lower()

            label = (
                f"page={page if page is not None else 'default'}, "
                f"limit={limit if limit is not None else 'default'}, "
                f"search={search if search is not None else 'none'}, "
                f"include_tools="
                f"{include_tools if include_tools is not None else 'default'}"
            )

            # Call GET /registry with this query-parameter combination.
            resp = get(
                self.client,
                "/api/v1/toolsets/registry",
                params=params or None,
            )

            # Valid combinations must succeed with HTTP 200.
            assert resp.status_code == 200, (
                f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
            )

            body = resp.json()

            # Response JSON must conform to the listToolsetRegistry OpenAPI schema.
            assert_response_matches_openapi_operation(body, "listToolsetRegistry")

            # Explicit page must be reflected in pagination metadata.
            if page is not None:
                assert body["pagination"]["page"] == page, (
                    f"[{label}] pagination.page mismatch"
                )

            # Explicit limit must be reflected in pagination metadata.
            if limit is not None:
                assert body["pagination"]["limit"] == limit, (
                    f"[{label}] pagination.limit mismatch"
                )

            # A search term that matches no toolsets must return zero total.
            if search == "zzz_nonexistent_toolset_xyz":
                assert body["pagination"]["total"] == 0, (
                    f"[{label}] Expected total=0 for non-matching search"
                )

    def test_registry_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, and bad params."""
        op_id = "listToolsetRegistry"

        # --- Auth failures: authMiddleware.authenticate → 401 + ErrorResponse ---
        no_auth = get_no_auth(self.client, "/api/v1/toolsets/registry")
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        no_auth_body = no_auth.json()
        assert_response_matches_openapi_operation(
            no_auth_body, op_id, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, "/api/v1/toolsets/registry")
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        bad_token_body = bad_token.json()
        assert_response_matches_openapi_operation(
            bad_token_body, op_id, status_code="401"
        )

        # --- Bad query params: ValidationMiddleware + toolsetListSchema → 400 ---
        bad_params = get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"page": -1, "limit": 0},
        )
        assert bad_params.status_code == 400, (
            f"Expected 400 for invalid pagination params, got {bad_params.status_code}"
        )
        bad_params_body = bad_params.json()
        assert_response_matches_openapi_operation(
            bad_params_body, op_id, status_code="400"
        )


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
        """Every registry toolset schema must match GetToolsetSchemaResponse and include config and tools."""
        registry_resp = get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"limit": 50},
        )
        assert registry_resp.status_code == 200
        toolsets = registry_resp.json().get("toolsets", [])
        assert len(toolsets) > 0, "No toolsets in registry — cannot run schema tests"

        for entry in toolsets:
            toolset_type = entry["name"]
            resp = get(
                self.client,
                f"/api/v1/toolsets/registry/{toolset_type}/schema",
            )
            assert resp.status_code == 200, (
                f"[{toolset_type}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getToolsetSchema")
            toolset = body["toolset"]
            assert "config" in toolset, f"[{toolset_type}] missing config"
            assert isinstance(toolset["tools"], list), (
                f"[{toolset_type}] tools must be a list"
            )

    def test_toolset_schema_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, and non-existent toolset type."""
        op_id = "getToolsetSchema"

        # --- Auth failures: authMiddleware.authenticate → 401 + ErrorResponse ---
        no_auth = get_no_auth(
            self.client, "/api/v1/toolsets/registry/some_toolset/schema"
        )
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        no_auth_body = no_auth.json()
        assert_response_matches_openapi_operation(
            no_auth_body, op_id, status_code="401"
        )

        bad_token = get_with_bad_token(
            self.client, "/api/v1/toolsets/registry/some_toolset/schema"
        )
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        bad_token_body = bad_token.json()
        assert_response_matches_openapi_operation(
            bad_token_body, op_id, status_code="401"
        )

        # --- Non-existent toolset type: connector → 404 + ErrorResponse ---
        nonexistent = get(
            self.client,
            "/api/v1/toolsets/registry/zzz_nonexistent_toolset_xyz/schema",
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent toolset type, got {nonexistent.status_code}"
        )
        nonexistent_body = nonexistent.json()
        assert_response_matches_openapi_operation(
            nonexistent_body, op_id, status_code="404"
        )


# ====================================================================
# GET /api/v1/toolsets/my-toolsets
# ====================================================================
@pytest.mark.integration
class TestGetMyToolsets:
    """GET /api/v1/toolsets/my-toolsets — merged view of instances + auth status."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_my_toolsets_response_schema(self) -> None:
        """Valid query-parameter cases must return 200 and match getMyToolsets schema."""
        registry_resp = get(
            self.client,
            "/api/v1/toolsets/registry",
            params={"limit": 1},
        )
        assert registry_resp.status_code == 200, registry_resp.text
        registry_toolsets = registry_resp.json().get("toolsets", [])
        assert registry_toolsets, (
            "No toolsets in registry — cannot run my-toolsets param tests"
        )
        sample_toolset_type = registry_toolsets[0]["name"]
        search_prefix = sample_toolset_type[: min(4, len(sample_toolset_type))]

        def assert_my_toolsets_case(label: str, params: dict) -> None:
            resp = get(
                self.client,
                "/api/v1/toolsets/my-toolsets",
                params=params or None,
            )
            assert resp.status_code == 200, (
                f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getMyToolsets")

            fc = body["filterCounts"]
            assert fc["all"] >= 0, f"[{label}] filterCounts.all invalid"
            assert fc["authenticated"] >= 0, (
                f"[{label}] filterCounts.authenticated invalid"
            )
            assert fc["notAuthenticated"] >= 0, (
                f"[{label}] filterCounts.notAuthenticated invalid"
            )
            assert fc["all"] == fc["authenticated"] + fc["notAuthenticated"], (
                f"[{label}] filterCounts must sum to all"
            )

            if "page" in params:
                assert body["pagination"]["page"] == params["page"], (
                    f"[{label}] pagination.page mismatch"
                )
            if "limit" in params:
                assert body["pagination"]["limit"] == params["limit"], (
                    f"[{label}] pagination.limit mismatch"
                )

        # --- Phase 1: each query param in isolation (others omitted → server defaults) ---
        single_param_cases: list[tuple[str, dict]] = [
            ("defaults", {}),
            ("page=1", {"page": 1}),
            ("page=2", {"page": 2}),
            ("limit=5", {"limit": 5}),
            ("limit=200", {"limit": 200}),
            ("search=nonexistent", {"search": "zzz_nonexistent_toolset_xyz"}),
            ("includeRegistry=true", {"includeRegistry": "true"}),
            ("includeRegistry=false", {"includeRegistry": "false"}),
            ("authStatus=authenticated", {"authStatus": "authenticated"}),
            ("authStatus=not-authenticated", {"authStatus": "not-authenticated"}),
            ("toolsetType", {"toolsetType": sample_toolset_type}),
        ]
        for label, params in single_param_cases:
            assert_my_toolsets_case(f"single:{label}", params)

        # --- Phase 2: multi-param combinations (product grid; skip single-param rows) ---
        pages = [None, 1, 2]
        limits = [None, 2, 5]
        searches = [None, "zzz_nonexistent_toolset_xyz"]
        include_registry_options = [None, True, False]
        auth_status_options = [None, "authenticated", "not-authenticated"]

        for page, limit, search, include_registry, auth_status in product(
            pages,
            limits,
            searches,
            include_registry_options,
            auth_status_options,
        ):
            if sum(
                1
                for v in (page, limit, search, include_registry, auth_status)
                if v is not None
            ) < 2:
                continue

            params: dict = {}
            if page is not None:
                params["page"] = page
            if limit is not None:
                params["limit"] = limit
            if search is not None:
                params["search"] = search
            if include_registry is not None:
                params["includeRegistry"] = str(include_registry).lower()
            if auth_status is not None:
                params["authStatus"] = auth_status

            label = (
                f"page={page if page is not None else 'default'}, "
                f"limit={limit if limit is not None else 'default'}, "
                f"search={search if search is not None else 'none'}, "
                f"includeRegistry="
                f"{include_registry if include_registry is not None else 'default'}, "
                f"authStatus={auth_status if auth_status is not None else 'all'}"
            )
            assert_my_toolsets_case(f"combo:{label}", params)

        # --- Phase 3: toolsetType combined with pagination, registry, auth, and search ---
        toolset_type_combo_cases: list[tuple[str, dict]] = [
            (
                "toolsetType+includeRegistry=true",
                {"toolsetType": sample_toolset_type, "includeRegistry": "true"},
            ),
            (
                "toolsetType+page=1,limit=5",
                {"toolsetType": sample_toolset_type, "page": 1, "limit": 5},
            ),
            (
                "toolsetType+page=2,limit=2",
                {"toolsetType": sample_toolset_type, "page": 2, "limit": 2},
            ),
            (
                "toolsetType+authStatus=authenticated",
                {"toolsetType": sample_toolset_type, "authStatus": "authenticated"},
            ),
            (
                "toolsetType+authStatus=not-authenticated",
                {
                    "toolsetType": sample_toolset_type,
                    "authStatus": "not-authenticated",
                },
            ),
            (
                "toolsetType+search",
                {"toolsetType": sample_toolset_type, "search": search_prefix},
            ),
            (
                "toolsetType+search+includeRegistry=false",
                {
                    "toolsetType": sample_toolset_type,
                    "search": search_prefix,
                    "includeRegistry": "false",
                },
            ),
            (
                "full:toolsetType+page+limit+includeRegistry+authStatus+search",
                {
                    "toolsetType": sample_toolset_type,
                    "page": 1,
                    "limit": 5,
                    "includeRegistry": "true",
                    "authStatus": "authenticated",
                    "search": search_prefix,
                },
            ),
        ]
        for label, params in toolset_type_combo_cases:
            assert_my_toolsets_case(f"toolsetType-combo:{label}", params)

    def test_created_instances_appear(
        self, toolset_test_instances: list[dict]
    ) -> None:
        """Created test instances should appear in my-toolsets response."""
        resp = get(self.client, "/api/v1/toolsets/my-toolsets")
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getMyToolsets")
        all_toolsets = body.get("toolsets", [])
        test_instance_ids = {inst["_id"] for inst in toolset_test_instances}
        found = [
            t for t in all_toolsets
            if t.get("_id") in test_instance_ids
            or t.get("instanceId") in test_instance_ids
        ]
        assert len(found) > 0, "Created test instances not found in my-toolsets"

    def test_my_toolsets_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, and invalid filter value."""
        op_id = "getMyToolsets"

        # --- Auth failures: authMiddleware.authenticate → 401 + ErrorResponse ---
        no_auth = get_no_auth(self.client, "/api/v1/toolsets/my-toolsets")
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), op_id, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, "/api/v1/toolsets/my-toolsets")
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), op_id, status_code="401"
        )

        # --- Bad query params: ValidationMiddleware + getMyToolsetsSchema → 400 ---
        bad_filter = get(
            self.client,
            "/api/v1/toolsets/my-toolsets",
            params={"authStatus": "completely_invalid_status"},
        )
        assert bad_filter.status_code == 400, (
            f"Expected 400 for invalid authStatus enum, got {bad_filter.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_filter.json(), op_id, status_code="400"
        )


# ====================================================================
# GET /api/v1/toolsets/instances
# ====================================================================
@pytest.mark.integration
class TestGetToolsetInstances:
    """GET /api/v1/toolsets/instances — list all instances for the org."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_instances_response_schema(self) -> None:
        """Valid query-parameter combinations must return 200 and match schema."""
        op_id = "getToolsetInstances"

        def assert_instances_case(label: str, params: dict) -> None:
            resp = get(
                self.client,
                "/api/v1/toolsets/instances",
                params=params or None,
            )
            assert resp.status_code == 200, (
                f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, op_id)

            if "page" in params:
                assert body["pagination"]["page"] == params["page"], (
                    f"[{label}] pagination.page mismatch"
                )
            if "limit" in params:
                assert body["pagination"]["limit"] == params["limit"], (
                    f"[{label}] pagination.limit mismatch"
                )
            if params.get("search") == "zzz_nonexistent_toolset_xyz":
                assert body["pagination"]["total"] == 0, (
                    f"[{label}] Expected total=0 for non-matching search"
                )

        # --- Phase 1: each query param in isolation (getToolsetInstancesSchema) ---
        single_param_cases: list[tuple[str, dict]] = [
            ("defaults", {}),
            ("page=1", {"page": 1}),
            ("page=2", {"page": 2}),
            ("limit=5", {"limit": 5}),
            ("limit=200", {"limit": 200}),
            ("search=nonexistent", {"search": "zzz_nonexistent_toolset_xyz"}),
            ("search=rv-test-prefix", {"search": "rv-test"}),
        ]
        for label, params in single_param_cases:
            assert_instances_case(f"single:{label}", params)

        # --- Phase 2: multi-param combinations (page × limit × search grid) ---
        pages = [None, 1, 2]
        limits = [None, 2, 200]
        searches = [None, "zzz_nonexistent_toolset_xyz", "rv-test"]

        for page, limit, search in product(pages, limits, searches):
            if sum(1 for v in (page, limit, search) if v is not None) < 2:
                continue

            params: dict = {}
            if page is not None:
                params["page"] = page
            if limit is not None:
                params["limit"] = limit
            if search is not None:
                params["search"] = search

            label = (
                f"page={page if page is not None else 'default'}, "
                f"limit={limit if limit is not None else 'default'}, "
                f"search={search if search is not None else 'none'}"
            )
            assert_instances_case(f"combo:{label}", params)

    def test_created_instances_appear(
        self, toolset_test_instances: list[dict]
    ) -> None:
        """Module-scoped test instances should appear in the org instances list."""
        resp = get(self.client, "/api/v1/toolsets/instances")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getToolsetInstances")
        listed_ids = {inst["_id"] for inst in body.get("instances", [])}
        test_ids = {inst["_id"] for inst in toolset_test_instances}
        assert test_ids.issubset(listed_ids), (
            "Created test instances not found in instances list"
        )

    def test_get_instances_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, and bad params."""
        op_id = "getToolsetInstances"
        path = "/api/v1/toolsets/instances"

        # --- Auth failures: authMiddleware.authenticate → 401 + ErrorResponse ---
        no_auth = get_no_auth(self.client, path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), op_id, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, path)
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), op_id, status_code="401"
        )

        # --- Bad query params: ValidationMiddleware + getToolsetInstancesSchema → 400 ---
        bad_page = get(self.client, path, params={"page": 0})
        assert bad_page.status_code == 400, (
            f"Expected 400 for page=0, got {bad_page.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_page.json(), op_id, status_code="400"
        )

        bad_limit = get(self.client, path, params={"limit": 201})
        assert bad_limit.status_code == 400, (
            f"Expected 400 for limit=201, got {bad_limit.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_limit.json(), op_id, status_code="400"
        )

        bad_combo = get(self.client, path, params={"page": -1, "limit": 0})
        assert bad_combo.status_code == 400, (
            f"Expected 400 for invalid pagination, got {bad_combo.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_combo.json(), op_id, status_code="400"
        )

# ====================================================================
# GET /api/v1/toolsets/instances/:instanceId
# ====================================================================
@pytest.mark.integration
class TestGetToolsetInstance:
    """GET /api/v1/toolsets/instances/:instanceId — get a specific instance."""

    OP_ID = "getToolsetInstance"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance_id = toolset_test_instances[0]["_id"]

    def test_toolset_instance_response_schema(self) -> None:
        """Fetch test instance by ID — response must match schema."""
        resp = get(
            self.client,
            f"/api/v1/toolsets/instances/{self.instance_id}",
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), self.OP_ID)

    def test_get_instance_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, non-existent ID, and malformed ID."""
        path = f"/api/v1/toolsets/instances/{self.instance_id}"
        instances_path = "/api/v1/toolsets/instances"

        # --- Auth failures: authMiddleware.authenticate → 401 + ErrorResponse ---
        no_auth = get_no_auth(self.client, path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, path)
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        # --- Not found: valid ObjectId format but no such instance → 404 ---
        nonexistent = get(
            self.client,
            f"{instances_path}/{FAKE_OBJECT_ID}",
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent instance, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )

        # --- Invalid path param: malformed instanceId → 404 (connectors lookup) ---
        malformed = get(
            self.client,
            f"{instances_path}/not-a-valid-id!!",
        )
        assert malformed.status_code == 404, (
            f"Expected 404 for malformed instance ID, got {malformed.status_code}"
        )
        assert_response_matches_openapi_operation(
            malformed.json(), self.OP_ID, status_code="404"
        )

# ====================================================================
# POST /api/v1/toolsets/instances
# ====================================================================
@pytest.mark.integration
class TestCreateToolsetInstance:
    """POST /api/v1/toolsets/instances — createToolsetInstance."""

    CREATE_PATH = "/api/v1/toolsets/instances"
    OP_ID = "createToolsetInstance"

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_create_instance_response_schema(self) -> None:
        """Valid request-body combinations must return 200 and match OpenAPI schema."""
        candidates = pick_toolsets_for_testing(self.client)
        if not candidates:
            pytest.skip(
                "No suitable toolsets found in registry for create-instance tests"
            )

        created_ids: list[str] = []

        def build_payload(
            ts: dict,
            *,
            include_auth_config: bool,
            include_base_url: bool,
            include_oauth_instance_name: bool,
        ) -> dict:
            payload: dict = {
                "instanceName": f"rv-create-{ts['name']}-{uuid.uuid4().hex[:8]}",
                "toolsetType": ts["name"],
                "authType": ts["authType"],
            }
            if include_auth_config:
                payload["authConfig"] = ts.get("authConfig") or {}
            if include_base_url:
                payload["baseUrl"] = "https://mock-test.example.com"
            if include_oauth_instance_name:
                payload["oauthInstanceName"] = (
                    f"rv-oauth-app-{uuid.uuid4().hex[:8]}"
                )
            return payload

        def assert_create_case(label: str, body: dict) -> None:
            resp = post(self.client, self.CREATE_PATH, json=body)
            assert resp.status_code == 200, (
                f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            resp_body = resp.json()
            assert_response_matches_openapi_operation(
                resp_body,
                self.OP_ID,
                status_code="200",
            )
            instance = resp_body["instance"]
            assert instance["instanceName"] == body["instanceName"], (
                f"[{label}] instanceName mismatch"
            )
            assert instance["toolsetType"] == body["toolsetType"].lower(), (
                f"[{label}] toolsetType mismatch (stored lowercased)"
            )
            assert instance["authType"] == body["authType"].upper(), (
                f"[{label}] authType mismatch (stored uppercased)"
            )
            created_ids.append(instance["_id"])

        try:
            for ts in candidates:
                toolset_label = ts["name"]

                # --- Phase 1: optional body fields in isolation ---
                single_cases: list[tuple[str, bool, bool, bool]] = [
                    ("required-only", False, False, False),
                    ("authConfig", True, False, False),
                    ("baseUrl", False, True, False),
                    ("oauthInstanceName", False, False, True),
                ]
                for case_label, inc_auth, inc_url, inc_oauth_name in single_cases:
                    body = build_payload(
                        ts,
                        include_auth_config=inc_auth,
                        include_base_url=inc_url,
                        include_oauth_instance_name=inc_oauth_name,
                    )
                    assert_create_case(
                        f"{toolset_label}:single:{case_label}",
                        body,
                    )

                # --- Phase 2: multi-field combinations (2+ optional fields) ---
                for inc_auth, inc_url, inc_oauth_name in product(
                    [False, True],
                    [False, True],
                    [False, True],
                ):
                    if sum(
                        1
                        for v in (inc_auth, inc_url, inc_oauth_name)
                        if v
                    ) < 2:
                        continue

                    body = build_payload(
                        ts,
                        include_auth_config=inc_auth,
                        include_base_url=inc_url,
                        include_oauth_instance_name=inc_oauth_name,
                    )
                    combo_label = (
                        f"authConfig={'yes' if inc_auth else 'no'}, "
                        f"baseUrl={'yes' if inc_url else 'no'}, "
                        f"oauthInstanceName={'yes' if inc_oauth_name else 'no'}"
                    )
                    assert_create_case(
                        f"{toolset_label}:combo:{combo_label}",
                        body,
                    )

                # --- Phase 3: full optional set (all supported non-OAuth fields) ---
                full_body = build_payload(
                    ts,
                    include_auth_config=True,
                    include_base_url=True,
                    include_oauth_instance_name=False,
                )
                assert_create_case(f"{toolset_label}:explicit:full-non-oauth", full_body)

        finally:
            for instance_id in created_ids:
                delete_instance(self.client, instance_id)

    def test_create_instance_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, and invalid POST body."""
        valid_body = {
            "instanceName": "x",
            "toolsetType": "y",
            "authType": "API_TOKEN",
        }

        no_auth_post = post_no_auth(self.client, self.CREATE_PATH, json=valid_body)
        assert no_auth_post.status_code == 401, (
            f"Expected 401 for POST with missing auth, got {no_auth_post.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth_post.json(), self.OP_ID, status_code="401"
        )

        bad_token_post = post_with_bad_token(
            self.client, self.CREATE_PATH, json=valid_body
        )
        assert bad_token_post.status_code == 401, (
            f"Expected 401 for POST with bad token, got {bad_token_post.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token_post.json(), self.OP_ID, status_code="401"
        )

        missing_type = post(
            self.client,
            self.CREATE_PATH,
            json={"instanceName": "test-instance", "authType": "API_TOKEN"},
        )
        assert missing_type.status_code == 400, (
            f"Expected 400 for missing 'toolsetType', got {missing_type.status_code}"
        )
        assert_response_matches_openapi_operation(
            missing_type.json(), self.OP_ID, status_code="400"
        )

        missing_name = post(
            self.client,
            self.CREATE_PATH,
            json={"toolsetType": "github", "authType": "API_TOKEN"},
        )
        assert missing_name.status_code == 400, (
            f"Expected 400 for missing 'instanceName', got {missing_name.status_code}"
        )
        assert_response_matches_openapi_operation(
            missing_name.json(), self.OP_ID, status_code="400"
        )

        unknown_type = post(
            self.client,
            self.CREATE_PATH,
            json={
                "instanceName": f"neg-test-{uuid.uuid4().hex[:8]}",
                "toolsetType": "zzz_nonexistent_toolset_xyz",
                "authType": "API_TOKEN",
            },
        )
        assert unknown_type.status_code == 404, (
            f"Expected 404 for unknown toolset type, got {unknown_type.status_code}"
        )
        assert_response_matches_openapi_operation(
            unknown_type.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# DELETE /api/v1/toolsets/instances/:instanceId
# ====================================================================
@pytest.mark.integration
class TestDeleteToolsetInstance:
    """DELETE /api/v1/toolsets/instances/:instanceId — deleteToolsetInstance."""

    OP_ID = "deleteToolsetInstance"

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_delete_instance_response_schema(self) -> None:
        """Create a disposable instance, delete it, and validate the response schema."""
        candidates = pick_toolsets_for_testing(self.client)
        if not candidates:
            pytest.skip(
                "No suitable toolsets found in registry for delete-instance tests"
            )

        ts = candidates[0]
        instance = create_test_instance(
            self.client,
            ts["name"],
            ts["authType"],
            ts["authConfig"],
        )
        instance_id = instance["_id"]

        delete_resp = delete(
            self.client,
            f"/api/v1/toolsets/instances/{instance_id}",
        )
        assert delete_resp.status_code == 200, (
            f"Expected 200, got {delete_resp.status_code}: {delete_resp.text}"
        )
        body = delete_resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["instanceId"] == instance_id
        assert body["status"] == "success"
        assert isinstance(body["message"], str) and body["message"]
        assert body["deletedCredentialsCount"] >= 0

        # Instance should no longer be retrievable
        get_resp = get(
            self.client,
            f"/api/v1/toolsets/instances/{instance_id}",
        )
        assert get_resp.status_code == 404, (
            f"Expected 404 after delete, got {get_resp.status_code}"
        )

    def test_delete_instance_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth and non-existent instance."""
        no_auth_delete = delete_no_auth(
            self.client,
            f"/api/v1/toolsets/instances/{FAKE_OBJECT_ID}",
        )
        assert no_auth_delete.status_code == 401, (
            f"Expected 401 for DELETE with missing auth, got {no_auth_delete.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth_delete.json(), self.OP_ID, status_code="401"
        )

        delete_nonexistent = delete(
            self.client,
            f"/api/v1/toolsets/instances/{FAKE_OBJECT_ID}",
        )
        assert delete_nonexistent.status_code == 404, (
            f"Expected 404 for deleting non-existent instance, got {delete_nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            delete_nonexistent.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# PUT /api/v1/toolsets/instances/:instanceId (update / rename)
# ====================================================================
@pytest.mark.integration
class TestUpdateToolsetInstance:
    """PUT /api/v1/toolsets/instances/:instanceId — updateToolsetInstance."""

    OP_ID = "updateToolsetInstance"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance = toolset_test_instances[0]
        self.instance_id = self.instance["_id"]
        self._mock_auth_config = self._resolve_mock_auth_config()

    def _instance_path(self) -> str:
        return f"{self.INSTANCES_PATH}/{self.instance_id}"

    def _resolve_mock_auth_config(self) -> dict:
        """Mock authConfig for this instance's toolset type (from registry helpers)."""
        toolset_type = self.instance["toolsetType"]
        for ts in pick_toolsets_for_testing(self.client):
            if ts["name"].lower() == toolset_type.lower():
                return ts.get("authConfig") or {}
        return {}

    def test_update_instance_response_schema(self) -> None:
        """Valid request-body combinations must return 200 and match OpenAPI schema."""
        path = self._instance_path()
        last_instance_name = self.instance["instanceName"]

        def build_payload(
            *,
            include_instance_name: bool,
            include_base_url: bool,
            include_auth_config: bool,
            include_oauth_config_id: bool,
        ) -> dict:
            payload: dict = {}
            if include_instance_name:
                payload["instanceName"] = (
                    f"rv-update-{uuid.uuid4().hex[:8]}"
                )
            if include_base_url:
                payload["baseUrl"] = "https://mock-test.example.com"
            if include_auth_config:
                payload["authConfig"] = dict(self._mock_auth_config)
            if include_oauth_config_id:
                payload["oauthConfigId"] = FAKE_OBJECT_ID
            return payload

        def assert_update_case(label: str, body: dict) -> str:
            nonlocal last_instance_name
            resp = put(self.client, path, json=body)
            assert resp.status_code == 200, (
                f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
            )
            resp_body = resp.json()
            assert_response_matches_openapi_operation(resp_body, self.OP_ID)

            instance = resp_body["instance"]
            assert instance["_id"] == self.instance_id, (
                f"[{label}] PUT must not change the instance _id"
            )
            assert instance["toolsetType"] == self.instance["toolsetType"], (
                f"[{label}] PUT must not change the toolsetType"
            )
            if "instanceName" in body:
                assert instance["instanceName"] == body["instanceName"], (
                    f"[{label}] instanceName not updated in response"
                )
                last_instance_name = body["instanceName"]
                assert resp_body["deauthenticatedUserCount"] == 0, (
                    f"[{label}] Renaming must not deauthenticate users"
                )
            else:
                assert resp_body["deauthenticatedUserCount"] >= 0, (
                    f"[{label}] deauthenticatedUserCount must be non-negative"
                )
            return last_instance_name

        # --- Phase 0: empty body (all fields optional in Zod / OpenAPI) ---
        assert_update_case("single:empty-body", {})

        # --- Phase 1: optional body fields in isolation ---
        single_cases: list[tuple[str, bool, bool, bool, bool]] = [
            ("instanceName", True, False, False, False),
            ("baseUrl", False, True, False, False),
            ("authConfig", False, False, True, False),
            ("oauthConfigId", False, False, False, True),
        ]
        for case_label, inc_name, inc_url, inc_auth, inc_oauth_id in single_cases:
            body = build_payload(
                include_instance_name=inc_name,
                include_base_url=inc_url,
                include_auth_config=inc_auth,
                include_oauth_config_id=inc_oauth_id,
            )
            assert_update_case(f"single:{case_label}", body)

        # --- Phase 2: multi-field combinations (2+ optional fields) ---
        for inc_name, inc_url, inc_auth, inc_oauth_id in product(
            [False, True],
            [False, True],
            [False, True],
            [False, True],
        ):
            if sum(1 for v in (inc_name, inc_url, inc_auth, inc_oauth_id) if v) < 2:
                continue
            body = build_payload(
                include_instance_name=inc_name,
                include_base_url=inc_url,
                include_auth_config=inc_auth,
                include_oauth_config_id=inc_oauth_id,
            )
            combo_label = (
                f"instanceName={'yes' if inc_name else 'no'}, "
                f"baseUrl={'yes' if inc_url else 'no'}, "
                f"authConfig={'yes' if inc_auth else 'no'}, "
                f"oauthConfigId={'yes' if inc_oauth_id else 'no'}"
            )
            assert_update_case(f"combo:{combo_label}", body)

        # --- Phase 3: typical non-OAuth update (rename + credentials + baseUrl) ---
        full_body = build_payload(
            include_instance_name=True,
            include_base_url=True,
            include_auth_config=True,
            include_oauth_config_id=False,
        )
        last_instance_name = assert_update_case(
            "explicit:full-non-oauth", full_body
        )

        # --- Persistence: GET must reflect the last rename ---
        get_resp = get(self.client, path)
        assert get_resp.status_code == 200, (
            f"GET after updates failed: {get_resp.status_code}: {get_resp.text}"
        )
        assert get_resp.json()["instance"]["instanceName"] == last_instance_name, (
            "GET after PUT must return the updated instanceName"
        )

    def test_update_instance_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, non-existent instance, and malformed ID."""
        path = self._instance_path()
        instances_path = self.INSTANCES_PATH
        valid_body = {"instanceName": f"rv-neg-{uuid.uuid4().hex[:8]}"}

        no_auth = put_no_auth(self.client, path, json=valid_body)
        assert no_auth.status_code == 401, (
            f"Expected 401 for PUT with missing auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = put_with_bad_token(self.client, path, json=valid_body)
        assert bad_token.status_code == 401, (
            f"Expected 401 for PUT with invalid token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        nonexistent = put(
            self.client,
            f"{instances_path}/{FAKE_OBJECT_ID}",
            json=valid_body,
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent instance, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )

        malformed_id = put(
            self.client,
            f"{instances_path}/not-a-valid-id!!",
            json=valid_body,
        )
        assert malformed_id.status_code == 404, (
            f"Expected 404 for malformed instance ID, got {malformed_id.status_code}"
        )
        assert_response_matches_openapi_operation(
            malformed_id.json(), self.OP_ID, status_code="404"
        )

# ====================================================================
# GET /api/v1/toolsets/instances/:instanceId/status
# ====================================================================
@pytest.mark.integration
class TestGetInstanceStatus:
    """GET /api/v1/toolsets/instances/:instanceId/status — getToolsetInstanceStatus."""

    OP_ID = "getToolsetInstanceStatus"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance = toolset_test_instances[0]
        self.instance_id = self.instance["_id"]

    def test_toolset_status_response_schema(self) -> None:
        """Fetch instance status — response must match schema."""
        # Use live instance metadata: earlier tests (e.g. TestUpdateToolsetInstance)
        # may have renamed the shared fixture instance via PUT.
        get_inst = get(
            self.client,
            f"{self.INSTANCES_PATH}/{self.instance_id}",
        )
        assert get_inst.status_code == 200, (
            f"Expected 200 loading instance, got {get_inst.status_code}: {get_inst.text}"
        )
        live = get_inst.json()["instance"]

        resp = get(self.client, self._status_path())
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["instanceId"] == self.instance_id
        assert body["instanceName"] == live["instanceName"]
        assert body["toolsetType"] == live["toolsetType"]
        assert body["authType"] == live["authType"]
        assert body["isConfigured"] is True
        assert isinstance(body["isAuthenticated"], bool)

    def _status_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/status"

    def test_instance_status_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, non-existent instance, and malformed ID."""
        path = self._status_path()

        no_auth = get_no_auth(self.client, path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for missing auth token, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, path)
        assert bad_token.status_code == 401, (
            f"Expected 401 for invalid token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        nonexistent = get(self.client, self._status_path(FAKE_OBJECT_ID))
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent instance, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )

        malformed = get(
            self.client,
            f"{self.INSTANCES_PATH}/not-a-valid-id!!/status",
        )
        assert malformed.status_code == 404, (
            f"Expected 404 for malformed instance ID, got {malformed.status_code}"
        )
        assert_response_matches_openapi_operation(
            malformed.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# POST /api/v1/toolsets/instances/:instanceId/authenticate
# ====================================================================
@pytest.mark.integration
class TestAuthenticateToolsetInstance:
    """POST /api/v1/toolsets/instances/:instanceId/authenticate — authenticateToolsetInstance.

    Validates that non-OAuth credentials can be stored against a toolset
    instance and that the response matches the OpenAPI schema.  Uses mock
    credentials — the backend writes them to etcd without contacting the
    external service, so no real credentials are required.
    """

    OP_ID = "authenticateToolsetInstance"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instances = toolset_test_instances
        # Keep a single fallback instance for negative-case tests
        self.instance = toolset_test_instances[0]
        self.instance_id = self.instance["_id"]
        self.auth_type = self.instance["authType"]
        self.mock_auth_body = make_mock_auth_body(
            self.client, self.instance["toolsetType"], self.auth_type
        )

    def _auth_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/authenticate"

    def _credentials_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/credentials"

    def test_authenticate_response_schema(self) -> None:
        """POST credentials for every covered auth type must return 200 and match schema.

        Iterates over all instances created by the fixture (one per covered auth
        type).  For each instance, sends three freshly-generated credential bodies
        shaped for that instance's authType.  Each body is cleaned up before the
        next one runs so every variant starts from a clean state.
        """
        covered = [inst["authType"] for inst in self.instances]
        missing = [at for at in TARGET_AUTH_TYPES if at not in covered]
        logger.info(
            "[test_authenticate_response_schema] Auth types covered: %s",
            covered,
        )
        if missing:
            logger.warning(
                "[test_authenticate_response_schema] Auth types missing: %s",
                missing,
            )

        for inst in self.instances:
            auth_type = inst["authType"]
            instance_id = inst["_id"]
            logger.info(
                "Testing authType=%s instance=%s toolset=%s",
                auth_type, instance_id, inst["toolsetType"],
            )
            for label, body in all_mock_credential_variants(auth_type):
                logger.info("variant=%s", label)
                try:
                    resp = post(self.client, self._auth_path(instance_id), json=body)
                    assert resp.status_code == 200, (
                        f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
                    )
                    resp_body = resp.json()
                    assert_response_matches_openapi_operation(resp_body, self.OP_ID)
                    assert resp_body["status"] == "success", (
                        f"[{label}] status must be 'success'"
                    )
                    assert resp_body["isAuthenticated"] is True, (
                        f"[{label}] isAuthenticated must be True"
                    )
                    assert isinstance(resp_body["message"], str) and resp_body["message"], (
                        f"[{label}] message must be a non-empty string"
                    )
                finally:
                    delete(self.client, self._credentials_path(instance_id))

    def test_authenticate_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, empty body, non-existent instance."""
        path = self._auth_path()

        # --- Auth failures: authMiddleware.authenticate → 401 ---
        no_auth = post_no_auth(self.client, path, json=self.mock_auth_body)
        assert no_auth.status_code == 401, (
            f"Expected 401 for POST without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = post_with_bad_token(self.client, path, json=self.mock_auth_body)
        assert bad_token.status_code == 401, (
            f"Expected 401 for POST with bad token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        # --- Missing / empty auth body: Python handler raises 400 ---
        missing_auth = post(self.client, path, json={})
        assert missing_auth.status_code == 400, (
            f"Expected 400 for missing auth body, got {missing_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            missing_auth.json(), self.OP_ID, status_code="400"
        )

        # --- Non-existent instance → 404 ---
        nonexistent = post(
            self.client,
            f"{self.INSTANCES_PATH}/{FAKE_OBJECT_ID}/authenticate",
            json=self.mock_auth_body,
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent instance, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# PUT /api/v1/toolsets/instances/:instanceId/credentials
# ====================================================================
@pytest.mark.integration
class TestUpdateToolsetCredentials:
    """PUT /api/v1/toolsets/instances/:instanceId/credentials — updateToolsetCredentials.

    Requires an existing auth record (written by POST .../authenticate).
    Each happy-path test creates that record inline and tears it down in
    a finally block so the shared fixture instance is left clean.
    """

    OP_ID = "updateToolsetCredentials"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instances = toolset_test_instances
        # Keep a single fallback instance for negative-case tests
        self.instance = toolset_test_instances[0]
        self.instance_id = self.instance["_id"]
        self.auth_type = self.instance["authType"]
        self.mock_auth_body = make_mock_auth_body(
            self.client, self.instance["toolsetType"], self.auth_type
        )

    def _credentials_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/credentials"

    def _authenticate_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/authenticate"

    def test_update_credentials_response_schema(self) -> None:
        """Authenticate first, then PUT each credential variant — response must match schema.

        Iterates over all instances created by the fixture (one per covered auth
        type).  For each instance, sends three freshly-generated credential bodies
        shaped for that instance's authType.  Each iteration:
          1. POSTs a matching body to create the required auth record.
          2. PUTs an updated body of the same shape and asserts the response contract.
          3. Cleans up so the next variant starts from a clean state.
        """
        covered = [inst["authType"] for inst in self.instances]
        missing = [at for at in TARGET_AUTH_TYPES if at not in covered]
        logger.info(
            "[test_update_credentials_response_schema] Auth types covered: %s",
            covered,
        )
        if missing:
            logger.warning(
                "[test_update_credentials_response_schema] Auth types missing: %s",
                missing,
            )

        for inst in self.instances:
            auth_type = inst["authType"]
            instance_id = inst["_id"]
            logger.info(
                "Testing authType=%s instance=%s toolset=%s",
                auth_type, instance_id, inst["toolsetType"],
            )
            for label, updated_body in all_mock_credential_variants(auth_type):
                logger.info("variant=%s", label)
                # Pre-condition: ensure an auth record exists before each PUT.
                auth_resp = post(
                    self.client, self._authenticate_path(instance_id), json=updated_body
                )
                assert auth_resp.status_code == 200, (
                    f"[{label}] Pre-auth failed: {auth_resp.status_code}: {auth_resp.text}"
                )
                try:
                    resp = put(self.client, self._credentials_path(instance_id), json=updated_body)
                    assert resp.status_code == 200, (
                        f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
                    )
                    resp_body = resp.json()
                    assert_response_matches_openapi_operation(resp_body, self.OP_ID)
                    assert resp_body["status"] == "success", (
                        f"[{label}] status must be 'success'"
                    )
                    assert isinstance(resp_body["message"], str) and resp_body["message"], (
                        f"[{label}] message must be a non-empty string"
                    )
                finally:
                    delete(self.client, self._credentials_path(instance_id))

    def test_update_credentials_negative_cases(self) -> None:
        """Consolidates negative scenarios: missing auth, bad token, empty body, no prior record."""
        path = self._credentials_path()

        # --- Auth failures: authMiddleware.authenticate → 401 ---
        no_auth = put_no_auth(self.client, path, json=self.mock_auth_body)
        assert no_auth.status_code == 401, (
            f"Expected 401 for PUT without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = put_with_bad_token(self.client, path, json=self.mock_auth_body)
        assert bad_token.status_code == 401, (
            f"Expected 401 for PUT with bad token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        # --- Missing auth body: Python handler raises 400 ---
        missing_auth = put(self.client, path, json={})
        assert missing_auth.status_code == 400, (
            f"Expected 400 for missing auth body, got {missing_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            missing_auth.json(), self.OP_ID, status_code="400"
        )

        # --- No prior auth record: FAKE_OBJECT_ID has no etcd entry → 404 ---
        no_prior_record = put(
            self.client,
            f"{self.INSTANCES_PATH}/{FAKE_OBJECT_ID}/credentials",
            json=self.mock_auth_body,
        )
        assert no_prior_record.status_code == 404, (
            f"Expected 404 when no prior auth record exists, got {no_prior_record.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_prior_record.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# DELETE /api/v1/toolsets/instances/:instanceId/credentials
# ====================================================================
@pytest.mark.integration
class TestRemoveToolsetCredentials:
    """DELETE /api/v1/toolsets/instances/:instanceId/credentials — removeToolsetCredentials.

    The backend deletes the etcd key and always returns 200, making this
    operation idempotent.  The happy-path test authenticates first so there
    is actually something to delete, then verifies a second DELETE also
    returns 200 (idempotency guarantee).
    """

    OP_ID = "removeToolsetCredentials"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        toolset_test_instances: list[dict],
    ) -> None:
        self.client = pipeshub_client
        self.instance = toolset_test_instances[0]
        self.instance_id = self.instance["_id"]
        self.auth_type = self.instance["authType"]
        self.mock_auth_body = make_mock_auth_body(
            self.client, self.instance["toolsetType"], self.auth_type
        )

    def _credentials_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/credentials"

    def _authenticate_path(self, instance_id: str | None = None) -> str:
        iid = instance_id if instance_id is not None else self.instance_id
        return f"{self.INSTANCES_PATH}/{iid}/authenticate"

    def test_remove_credentials_response_schema(self) -> None:
        """Authenticate, DELETE credentials, verify 200 + schema, then confirm idempotency."""
        # Pre-condition: ensure an auth record exists so DELETE has something to remove.
        auth_resp = post(
            self.client, self._authenticate_path(), json=self.mock_auth_body
        )
        assert auth_resp.status_code == 200, (
            f"Pre-auth failed: {auth_resp.status_code}: {auth_resp.text}"
        )

        path = self._credentials_path()

        # --- Primary DELETE ---
        resp = delete(self.client, path)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["status"] == "success", "status must be 'success'"
        assert isinstance(body["message"], str) and body["message"], (
            "message must be a non-empty string"
        )

        # --- Idempotency: second DELETE on the now-absent record → still 200 ---
        resp2 = delete(self.client, path)
        assert resp2.status_code == 200, (
            f"Expected 200 for idempotent DELETE, got {resp2.status_code}: {resp2.text}"
        )
        assert_response_matches_openapi_operation(resp2.json(), self.OP_ID)

    def test_remove_credentials_negative_cases(self) -> None:
        """Missing auth header must return 401."""
        path = self._credentials_path()

        no_auth = delete_no_auth(self.client, path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for DELETE without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )


# ------------------------------------------------------------------ #
# Module-scoped fixtures for OAuth config tests
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def oauth_config_test_instance(
    pipeshub_client: PipeshubClient,
) -> Generator[dict, None, None]:
    """Create one OAUTH-type toolset instance for the GET and PUT oauth-config tests.

    Scans the registry for any toolset supporting OAUTH, creates an instance
    with mock clientId/clientSecret (no live OAuth provider needed — the
    backend stores the config in etcd without contacting the provider), then
    yields a context dict::

        {
            "instance_id":    str,
            "oauth_config_id": str,
            "toolset_type":   str,
        }

    Teardown deletes the instance then the OAuth config (in that order,
    because the safe-delete guard rejects a config deletion while instances
    still reference it).
    """
    client = pipeshub_client
    toolset = pick_oauth_toolset_for_testing(client)
    if not toolset:
        pytest.skip("No OAUTH-supporting toolset found in registry — skipping OAuth config tests")

    resp_data = create_oauth_test_instance(client, toolset["name"], toolset["authConfig"])
    instance = resp_data["instance"]
    instance_id = instance["_id"]
    oauth_config_id = instance.get("oauthConfigId")

    if not oauth_config_id:
        delete_instance(client, instance_id)
        pytest.skip(
            "Created OAUTH instance has no oauthConfigId "
            "(backend may require real credentials) — skipping OAuth config tests"
        )

    logger.info(
        "oauth_config_test_instance: created instance %s, oauthConfigId=%s, toolset=%s",
        instance_id, oauth_config_id, toolset["name"],
    )

    context = {
        "instance_id": instance_id,
        "oauth_config_id": oauth_config_id,
        "toolset_type": toolset["name"],
    }
    try:
        yield context
    finally:
        logger.info("Cleaning up OAuth config test instance %s", instance_id)
        delete_instance(client, instance_id)
        delete_oauth_config(client, toolset["name"], oauth_config_id)


@pytest.fixture()
def _fresh_oauth_instance(
    pipeshub_client: PipeshubClient,
) -> Generator[dict, None, None]:
    """Create a fresh OAUTH instance per test — used by DELETE test methods.

    Each test gets its own instance so happy-path and conflict tests can
    manage the instance lifecycle independently.  Teardown is best-effort:
    the test itself may have already deleted the instance or config.
    """
    client = pipeshub_client
    toolset = pick_oauth_toolset_for_testing(client)
    if not toolset:
        pytest.skip("No OAUTH-supporting toolset found in registry")

    resp_data = create_oauth_test_instance(client, toolset["name"], toolset["authConfig"])
    instance = resp_data["instance"]
    instance_id = instance["_id"]
    oauth_config_id = instance.get("oauthConfigId")

    if not oauth_config_id:
        delete_instance(client, instance_id)
        pytest.skip("OAUTH instance has no oauthConfigId")

    yield {
        "instance_id": instance_id,
        "oauth_config_id": oauth_config_id,
        "toolset_type": toolset["name"],
    }

    # the test may have already removed these
    delete_instance(client, instance_id)
    delete_oauth_config(client, toolset["name"], oauth_config_id)


# ====================================================================
# GET /api/v1/toolsets/oauth-configs/:toolsetType
# ====================================================================
@pytest.mark.integration
class TestListToolsetOAuthConfigs:
    """GET /api/v1/toolsets/oauth-configs/:toolsetType — listToolsetOAuthConfigs.

    Creates one OAUTH-type instance via the module-scoped fixture so there
    is always at least one config to list.  No live OAuth provider is needed
    — the backend stores mock clientId/clientSecret in etcd.
    """

    OP_ID = "listToolsetOAuthConfigs"
    OAUTH_CONFIGS_PATH = "/api/v1/toolsets/oauth-configs"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_config_test_instance: dict,
    ) -> None:
        self.client = pipeshub_client
        self.ctx = oauth_config_test_instance
        self.toolset_type = self.ctx["toolset_type"]
        self.oauth_config_id = self.ctx["oauth_config_id"]

    def _list_path(self, toolset_type: str | None = None) -> str:
        tt = toolset_type if toolset_type is not None else self.toolset_type
        return f"{self.OAUTH_CONFIGS_PATH}/{tt}"

    def test_list_oauth_configs_response_schema(self) -> None:
        """GET must return 200, match schema, and contain the config created by the fixture."""
        resp = get(self.client, self._list_path())
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["status"] == "success", "status must be 'success'"
        assert isinstance(body["oauthConfigs"], list), "oauthConfigs must be a list"
        assert isinstance(body["total"], int), "total must be an integer"
        assert body["total"] == len(body["oauthConfigs"]), (
            "total must equal len(oauthConfigs)"
        )

        config_ids = [cfg.get("_id") for cfg in body["oauthConfigs"]]
        assert self.oauth_config_id in config_ids, (
            f"Expected oauthConfigId {self.oauth_config_id!r} in list, got: {config_ids}"
        )

    def test_list_oauth_configs_unknown_type_returns_empty(self) -> None:
        """GET for a toolset type with no configs must return 200 and an empty list — never 404."""
        resp = get(self.client, self._list_path("nonexistent_toolset_zzz"))
        assert resp.status_code == 200, (
            f"Expected 200 for unknown toolset type, got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["oauthConfigs"] == [], "oauthConfigs must be empty for unknown type"
        assert body["total"] == 0

    def test_list_oauth_configs_negative_cases(self) -> None:
        """Missing auth → 401; bad token → 401."""
        path = self._list_path()

        no_auth = get_no_auth(self.client, path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for GET without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = get_with_bad_token(self.client, path)
        assert bad_token.status_code == 401, (
            f"Expected 401 for GET with bad token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )


# ====================================================================
# PUT /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId
# ====================================================================
@pytest.mark.integration
class TestUpdateToolsetOAuthConfig:
    """PUT /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId — updateToolsetOAuthConfig.

    Updates the config created by the module-scoped fixture with fresh mock
    credentials.  Because no real users are authenticated against the mock
    instance, deauthenticatedUserCount is always 0 — the test verifies the
    field is present and is an integer rather than asserting its value.
    """

    OP_ID = "updateToolsetOAuthConfig"
    OAUTH_CONFIGS_PATH = "/api/v1/toolsets/oauth-configs"

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_config_test_instance: dict,
    ) -> None:
        self.client = pipeshub_client
        self.ctx = oauth_config_test_instance
        self.toolset_type = self.ctx["toolset_type"]
        self.oauth_config_id = self.ctx["oauth_config_id"]

    def _update_path(
        self,
        toolset_type: str | None = None,
        oauth_config_id: str | None = None,
    ) -> str:
        tt = toolset_type if toolset_type is not None else self.toolset_type
        oid = oauth_config_id if oauth_config_id is not None else self.oauth_config_id
        return f"{self.OAUTH_CONFIGS_PATH}/{tt}/{oid}"

    # Optional authConfig fields from updateToolsetOAuthConfigSchema (toolsets_routes.ts).
    _OPTIONAL_AUTH_CONFIG_FIELDS: dict = {
        "tenantId": "mock-tenant-id",
        "authorizeUrl": "https://mock.example.com/oauth/authorize",
        "tokenUrl": "https://mock.example.com/oauth/token",
        "scopes": ["read", "write"],
        "redirectUri": "http://localhost:3001/callback",
        "additionalParams": {"prompt": "consent"},
        "tokenAccessType": "offline",
        "scopeParameterName": "scope",
        "tokenResponsePath": "access_token",
    }

    @classmethod
    def _mock_auth_config(cls, *, include_all_optionals: bool = False) -> dict:
        auth_config = {
            "clientId": f"mock-client-id-{uuid.uuid4().hex[:16]}",
            "clientSecret": f"mock-secret-{uuid.uuid4().hex[:24]}",
        }
        if include_all_optionals:
            auth_config.update(cls._OPTIONAL_AUTH_CONFIG_FIELDS)
        return auth_config

    @classmethod
    def _mock_update_body(cls, *, include_all_optionals: bool = False) -> dict:
        return {
            "authConfig": cls._mock_auth_config(
                include_all_optionals=include_all_optionals
            ),
            "baseUrl": "http://localhost:3001",
        }

    def _assert_update_success(self, label: str, body: dict) -> None:
        resp = put(self.client, self._update_path(), json=body)
        assert resp.status_code == 200, (
            f"[{label}] Expected 200, got {resp.status_code}: {resp.text}"
        )

        resp_body = resp.json()
        assert_response_matches_openapi_operation(resp_body, self.OP_ID)
        assert resp_body["status"] == "success", f"[{label}] status must be 'success'"
        assert isinstance(resp_body["oauthConfigId"], str) and resp_body["oauthConfigId"], (
            f"[{label}] oauthConfigId must be a non-empty string"
        )
        assert isinstance(resp_body["message"], str) and resp_body["message"], (
            f"[{label}] message must be a non-empty string"
        )
        assert isinstance(resp_body["deauthenticatedUserCount"], int), (
            f"[{label}] deauthenticatedUserCount must be an integer"
        )

    def test_update_oauth_config_response_schema(self) -> None:
        """Minimal and full optional authConfig bodies must return 200 and match schema."""
        cases: list[tuple[str, bool]] = [
            ("minimal", False),
            ("all-optionals", True),
        ]
        for label, include_all_optionals in cases:
            self._assert_update_success(
                label,
                self._mock_update_body(include_all_optionals=include_all_optionals),
            )

    def test_update_oauth_config_negative_cases(self) -> None:
        """Auth failures, missing required body fields, and non-existent config."""
        body = self._mock_update_body()
        path = self._update_path()

        no_auth = put_no_auth(self.client, path, json=body)
        assert no_auth.status_code == 401, (
            f"Expected 401 for PUT without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = put_with_bad_token(self.client, path, json=body)
        assert bad_token.status_code == 401, (
            f"Expected 401 for PUT with bad token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        # --- Missing required body fields: ValidationMiddleware + Zod → 400 ---
        invalid_body_cases: list[tuple[str, dict]] = [
            ("empty-body", {}),
            ("missing-authConfig", {"baseUrl": "http://localhost:3001"}),
            (
                "missing-baseUrl",
                {
                    "authConfig": {
                        "clientId": "mock-client-id",
                        "clientSecret": "mock-secret",
                    },
                },
            ),
            (
                "missing-clientId",
                {
                    "authConfig": {"clientSecret": "mock-secret"},
                    "baseUrl": "http://localhost:3001",
                },
            ),
            (
                "missing-clientSecret",
                {
                    "authConfig": {"clientId": "mock-client-id"},
                    "baseUrl": "http://localhost:3001",
                },
            ),
        ]
        for label, invalid_body in invalid_body_cases:
            resp = put(self.client, path, json=invalid_body)
            assert resp.status_code == 400, (
                f"[{label}] Expected 400 for invalid body, got {resp.status_code}: "
                f"{resp.text}"
            )
            assert_response_matches_openapi_operation(
                resp.json(), self.OP_ID, status_code="400"
            )

        nonexistent = put(
            self.client,
            self._update_path(oauth_config_id=FAKE_OBJECT_ID),
            json=body,
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent config, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )


# ====================================================================
# DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId
# ====================================================================
@pytest.mark.integration
class TestDeleteToolsetOAuthConfig:
    """DELETE /api/v1/toolsets/oauth-configs/:toolsetType/:oauthConfigId — deleteToolsetOAuthConfig.

    Each test method receives its own fresh OAUTH instance via the function-
    scoped ``_fresh_oauth_instance`` fixture so lifecycle steps can be
    managed independently:

    * Happy path: delete the referencing instance first, then confirm the
      config DELETE returns 200.
    * Conflict (409): attempt DELETE while the instance still references the
      config — backend must reject with 409.
    * Negative auth cases: tested with FAKE_OBJECT_ID to avoid touching the
      shared fixture state.
    """

    OP_ID = "deleteToolsetOAuthConfig"
    OAUTH_CONFIGS_PATH = "/api/v1/toolsets/oauth-configs"
    INSTANCES_PATH = "/api/v1/toolsets/instances"

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_delete_oauth_config_response_schema(
        self, _fresh_oauth_instance: dict
    ) -> None:
        """Delete the referencing instance first, then DELETE the config → 200 + schema."""
        ctx = _fresh_oauth_instance
        instance_id = ctx["instance_id"]
        oauth_config_id = ctx["oauth_config_id"]
        toolset_type = ctx["toolset_type"]

        # Remove the instance so the safe-delete guard is satisfied
        del_inst = delete(self.client, f"{self.INSTANCES_PATH}/{instance_id}")
        assert del_inst.status_code == 200, (
            f"Instance deletion failed: {del_inst.status_code}: {del_inst.text}"
        )

        path = f"{self.OAUTH_CONFIGS_PATH}/{toolset_type}/{oauth_config_id}"
        resp = delete(self.client, path)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        body = resp.json()
        assert_response_matches_openapi_operation(body, self.OP_ID)
        assert body["status"] == "success", "status must be 'success'"
        assert isinstance(body["message"], str) and body["message"], (
            "message must be a non-empty string"
        )

    def test_delete_oauth_config_blocked_while_instance_references_it(
        self, _fresh_oauth_instance: dict
    ) -> None:
        """DELETE must return 409 when an instance still references the config."""
        ctx = _fresh_oauth_instance
        oauth_config_id = ctx["oauth_config_id"]
        toolset_type = ctx["toolset_type"]

        path = f"{self.OAUTH_CONFIGS_PATH}/{toolset_type}/{oauth_config_id}"

        # Instance is still alive — safe-delete guard must block the deletion
        resp = delete(self.client, path)
        assert resp.status_code == 409, (
            f"Expected 409 while instance references the config, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), self.OP_ID, status_code="409"
        )

    def test_delete_oauth_config_negative_cases(self) -> None:
        """Missing auth → 401; bad token → 401; non-existent config → 404."""
        fake_path = f"{self.OAUTH_CONFIGS_PATH}/nonexistent_toolset_zzz/{FAKE_OBJECT_ID}"

        no_auth = delete_no_auth(self.client, fake_path)
        assert no_auth.status_code == 401, (
            f"Expected 401 for DELETE without auth, got {no_auth.status_code}"
        )
        assert_response_matches_openapi_operation(
            no_auth.json(), self.OP_ID, status_code="401"
        )

        bad_token = delete_with_bad_token(self.client, fake_path)
        assert bad_token.status_code == 401, (
            f"Expected 401 for DELETE with bad token, got {bad_token.status_code}"
        )
        assert_response_matches_openapi_operation(
            bad_token.json(), self.OP_ID, status_code="401"
        )

        nonexistent = delete(
            self.client,
            f"{self.OAUTH_CONFIGS_PATH}/nonexistent_toolset_zzz/{FAKE_OBJECT_ID}",
        )
        assert nonexistent.status_code == 404, (
            f"Expected 404 for non-existent config, got {nonexistent.status_code}"
        )
        assert_response_matches_openapi_operation(
            nonexistent.json(), self.OP_ID, status_code="404"
        )