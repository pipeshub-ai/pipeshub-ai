"""
User Groups API – Response Validation Integration Tests
========================================================

Tests JSON-returning routes under /api/v1/userGroups against OpenAPI response
schemas in ``pipeshub-openapi.yaml``.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response
  - Error envelope shape, machine-readable code, and human-readable message

Routes covered:
  GET    /api/v1/userGroups                    — getAllUserGroups
  GET    /api/v1/userGroups/:groupId           — getUserGroupById
  POST   /api/v1/userGroups                    — createUserGroup
  PUT    /api/v1/userGroups/:groupId           — updateUserGroup
  DELETE /api/v1/userGroups/:groupId           — deleteUserGroup
  POST   /api/v1/userGroups/add-users          — addUsersToGroup
  POST   /api/v1/userGroups/remove-users       — removeUsersFromGroup
  GET    /api/v1/userGroups/:groupId/users     — getUsersInGroup
  GET    /api/v1/userGroups/users/:userId      — getGroupsForUser
  GET    /api/v1/userGroups/stats/list         — getGroupStatistics
  GET    /api/v1/userGroups/health             — getUserGroupsHealth

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

logger = logging.getLogger("user-groups-integration-test")

_BASE_PATH = "/api/v1/userGroups"


# ====================================================================
# Helpers
# ====================================================================

def _url(client: PipeshubClient, path: str = "") -> str:
    return f"{client.base_url}{_BASE_PATH}{path}"


def _get(client: PipeshubClient, path: str = "") -> requests.Response:
    return requests.get(
        _url(client, path),
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )


def _post(client: PipeshubClient, path: str = "", json: object = None) -> requests.Response:
    return requests.post(
        _url(client, path),
        headers=client._headers(),
        json=json,
        timeout=client.timeout_seconds,
    )


def _put(client: PipeshubClient, path: str = "", json: object = None) -> requests.Response:
    return requests.put(
        _url(client, path),
        headers=client._headers(),
        json=json,
        timeout=client.timeout_seconds,
    )


def _delete(client: PipeshubClient, path: str = "") -> requests.Response:
    return requests.delete(
        _url(client, path),
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )


def _delete_group(client: PipeshubClient, group_id: str) -> None:
    """Best-effort cleanup — ignore errors."""
    try:
        _delete(client, f"/{group_id}")
    except Exception:
        pass


def _cleanup_stale_group(client: PipeshubClient, name: str) -> None:
    """Delete any existing non-deleted group with this name (leftover from prior runs)."""
    resp = _get(client)
    if resp.status_code != 200:
        return
    for g in resp.json().get("groups", []):
        if g.get("name") == name and not g.get("isDeleted"):
            _delete_group(client, g["_id"])


def _create_group(client: PipeshubClient, name: str, group_type: str = "custom") -> dict[str, object]:
    """Create a group and return the response body. Asserts 201.

    Cleans up any stale group with the same name from prior runs first.
    """
    _cleanup_stale_group(client, name)
    resp = _post(client, json={"name": name, "type": group_type})
    assert resp.status_code == 201, (
        f"Failed to create group '{name}': {resp.status_code}: {resp.text}"
    )
    return resp.json()


def _find_any_group(client: PipeshubClient) -> Optional[dict[str, object]]:
    """Find any non-deleted group."""
    resp = _get(client)
    if resp.status_code != 200:
        return None
    for g in resp.json().get("groups", []):
        if not g.get("isDeleted"):
            return g
    return None


def _find_user_id(client: PipeshubClient) -> Optional[str]:
    """Get a userId by fetching users from the first group that has members."""
    resp = _get(client)
    if resp.status_code != 200:
        return None
    for g in resp.json().get("groups", []):
        if g.get("userCount", 0) > 0:
            users_resp = _get(client, f"/{g['_id']}/users")
            if users_resp.status_code == 200:
                users = users_resp.json().get("users", [])
                if users:
                    return str(users[0]["_id"])
    return None


def _find_group_by_type(client: PipeshubClient, group_type: str) -> Optional[dict[str, object]]:
    """Return the first non-deleted group whose type matches group_type."""
    resp = _get(client)
    if resp.status_code != 200:
        return None
    for g in resp.json().get("groups", []):
        if g.get("type") == group_type and not g.get("isDeleted"):
            return g
    return None


# A valid-format ObjectId that should never exist in any test organisation.
_NONEXISTENT_ID = "000000000000000000000000"
# A string that intentionally fails the 24-char hex ObjectId regex.
_MALFORMED_ID = "not-a-valid-objectid"


# ====================================================================
# GET /api/v1/userGroups/health
# ====================================================================
@pytest.mark.integration
class TestUserGroupHealth:
    """GET /api/v1/userGroups/health — no auth required."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}{_BASE_PATH}/health"

    def test_user_groups_health_response_schema(self) -> None:
        """Response must match UserGroupHealthResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUserGroupsHealth")

    def test_unsupported_method_returns_4xx(self) -> None:
        """POST to /health is not a registered method — must return 4xx."""
        resp = requests.post(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code >= 400, (
            f"Expected 4xx for unsupported POST method, got {resp.status_code}"
        )


# ====================================================================
# GET /api/v1/userGroups
# ====================================================================
@pytest.mark.integration
class TestGetAllUserGroups:
    """GET /api/v1/userGroups — list all groups for the org."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_all_user_groups_response_schema(self) -> None:
        """Response must be a paginated object matching getAllUserGroups schema."""
        # Default call — no query params, server applies its defaults (page=1, limit=25).
        resp = _get(self.client)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, dict), f"Expected object, got {type(body)}"
        assert "groups" in body, "Expected 'groups' key in response"
        assert "pagination" in body, "Expected 'pagination' key in response"
        assert isinstance(body["groups"], list), "Expected 'groups' to be an array"
        assert_response_matches_openapi_operation(body, "getAllUserGroups")

        # Explicit page + limit — pagination metadata must reflect the requested values.
        resp = requests.get(
            _url(self.client),
            headers=self.client._headers(),
            params={"page": 1, "limit": 5},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=1,limit=5] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUserGroups")
        assert body["pagination"]["limit"] == 5, (
            f"[page=1,limit=5] Expected pagination.limit=5, got {body['pagination']['limit']}"
        )
        assert len(body["groups"]) <= 5, (
            f"[page=1,limit=5] Expected at most 5 groups, got {len(body['groups'])}"
        )

        # limit=1 — smallest valid page; response still satisfies schema.
        resp = requests.get(
            _url(self.client),
            headers=self.client._headers(),
            params={"page": 1, "limit": 1},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[limit=1] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUserGroups")
        assert body["pagination"]["limit"] == 1
        assert len(body["groups"]) <= 1

        # search — case-insensitive substring match; all returned names must contain the term.
        any_group = _find_any_group(self.client)
        if any_group:
            search_term = str(any_group["name"])[:3]
            resp = requests.get(
                _url(self.client),
                headers=self.client._headers(),
                params={"search": search_term},
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, (
                f"[search] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "getAllUserGroups")
            for g in body["groups"]:
                assert search_term.lower() in str(g["name"]).lower(), (
                    f"[search] Group name {g['name']!r} does not contain search term {search_term!r}"
                )

        # page=2 — may return empty groups array if total ≤ limit; schema must still pass.
        resp = requests.get(
            _url(self.client),
            headers=self.client._headers(),
            params={"page": 2, "limit": 100},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=2] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUserGroups")

    def test_get_all_user_groups_negative_tests(self) -> None:
        """Unauthenticated request must be rejected with 401."""
        # Missing Authorization header — server must reject before querying any groups.
        resp = requests.get(_url(self.client), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUserGroups", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/userGroups/:groupId
# ====================================================================
@pytest.mark.integration
class TestGetUserGroupById:
    """GET /api/v1/userGroups/:groupId — get single group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_user_group_by_id_response_schema(self) -> None:
        """Fetch an existing group — response must match getUserGroupById schema."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = _get(self.client, f"/{group['_id']}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUserGroupById")

    def test_get_user_group_by_id_negative_tests(self) -> None:
        """401 without auth · 400 for malformed ObjectId · 404 for non-existent id."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        # Missing Authorization header — auth check runs before DB lookup.
        resp = requests.get(_url(self.client, f"/{group['_id']}"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserGroupById", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed groupId — ValidationMiddleware rejects the 24-hex regex before the controller.
        resp = _get(self.client, f"/{_MALFORMED_ID}")
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserGroupById", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist in this org.
        resp = _get(self.client, f"/{_NONEXISTENT_ID}")
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserGroupById", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "UserGroup not found", (
            f"[nonexistent id] Expected 'UserGroup not found', got {body['error']['message']!r}"
        )


# ====================================================================
# POST /api/v1/userGroups (create)
# ====================================================================
@pytest.mark.integration
class TestCreateUserGroup:
    """POST /api/v1/userGroups — create a custom group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_create_custom_group_response_schema(self) -> None:
        """Create groups of each allowed type — 201 response must match createUserGroup schema."""
        # type="custom" — the primary use-case for user-defined groups.
        body = _create_group(self.client, "rv-test-custom-create", "custom")
        assert_response_matches_openapi_operation(
            body, "createUserGroup", status_code="201"
        )
        assert body["name"] == "rv-test-custom-create"
        assert body["type"] == "custom"
        del_resp = _delete(self.client, f"/{body['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup custom] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )


    def test_create_user_group_negative_tests(self) -> None:
        """401 no auth · 400 missing name · 400 missing type · 400 unknown type · 400 reserved type · 400 duplicate name."""
        # Missing Authorization header — auth middleware rejects before Zod validation.
        resp = requests.post(
            _url(self.client),
            json={"name": "rv-test-no-auth", "type": "custom"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUserGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Missing name — Zod schema requires name.min(1); rejected before controller.
        resp = _post(self.client, json={"type": "custom"})
        assert resp.status_code == 400, (
            f"[missing name] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUserGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing name] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing name] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing type — Zod schema requires type.min(1); rejected before controller.
        resp = _post(self.client, json={"name": "rv-test-no-type"})
        assert resp.status_code == 400, (
            f"[missing type] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUserGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing type] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing type] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Unknown type value — passes Zod (no enum), rejected in controller logic.
        resp = _post(self.client, json={"name": "rv-test-bad-type", "type": "unknown"})
        assert resp.status_code == 400, (
            f"[unknown type] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUserGroup", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[unknown type] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "type(Type of the Group) unknown", (
            f"[unknown type] Expected 'type(Type of the Group) unknown', got {body['error']['message']!r}"
        )

        # Reserved type 'admin' — controller explicitly forbids creating admin groups.
        resp = _post(self.client, json={"name": "rv-test-admin-type", "type": "admin"})
        assert resp.status_code == 400, (
            f"[reserved type] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUserGroup", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[reserved type] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "this type of group cannot be created", (
            f"[reserved type] Expected 'this type of group cannot be created', got {body['error']['message']!r}"
        )

        # Duplicate name — controller checks for existing non-deleted group with same name.
        created = _create_group(self.client, "rv-test-duplicate-name", "custom")
        try:
            resp = _post(self.client, json={"name": "rv-test-duplicate-name", "type": "custom"})
            assert resp.status_code == 400, (
                f"[duplicate name] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "createUserGroup", status_code="400")
            assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
                f"[duplicate name] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "Group already exists", (
                f"[duplicate name] Expected 'Group already exists', got {body['error']['message']!r}"
            )
        finally:
            del_resp = _delete(self.client, f"/{created['_id']}")
            assert del_resp.status_code == 200, (
                f"[cleanup duplicate-name] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
            )


# ====================================================================
# PUT /api/v1/userGroups/:groupId
# ====================================================================
@pytest.mark.integration
class TestUpdateUserGroup:
    """PUT /api/v1/userGroups/:groupId — rename a group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_update_user_group_name_response_schema(self) -> None:
        """Rename a custom group with different name shapes — each response must match updateUserGroup schema."""
        # Plain rename — baseline case.
        body = _create_group(self.client, "rv-test-update-name", "custom")
        resp = _put(self.client, f"/{body['_id']}", json={"name": "rv-test-renamed"})
        assert resp.status_code == 200, (
            f"[plain rename] Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_openapi_operation(result, "updateUserGroup")
        assert result["name"] == "rv-test-renamed"
        del_resp = _delete(self.client, f"/{result['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup plain rename] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

        # Name with surrounding whitespace — controller trims before saving.
        body = _create_group(self.client, "rv-test-trim-source", "custom")
        resp = _put(self.client, f"/{body['_id']}", json={"name": "  rv-test-trimmed  "})
        assert resp.status_code == 200, (
            f"[whitespace trim] Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_openapi_operation(result, "updateUserGroup")
        assert result["name"] == "rv-test-trimmed", (
            f"[whitespace trim] Expected trimmed name, got {result['name']!r}"
        )
        del_resp = _delete(self.client, f"/{result['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup whitespace trim] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

        # Name rename back to same value — idempotent PUT, must still return 200.
        body = _create_group(self.client, "rv-test-same-name", "custom")
        resp = _put(self.client, f"/{body['_id']}", json={"name": "rv-test-same-name"})
        assert resp.status_code == 200, (
            f"[same name] Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_openapi_operation(result, "updateUserGroup")
        assert result["name"] == "rv-test-same-name"
        del_resp = _delete(self.client, f"/{result['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup same-name] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

    def test_update_user_group_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing name · 404 nonexistent id · 403 protected group types."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        # Missing Authorization header.
        resp = requests.put(
            _url(self.client, f"/{group['_id']}"),
            json={"name": "rv-test-no-auth-rename"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed groupId — Zod regex rejects non-hex-24 params.
        resp = _put(self.client, f"/{_MALFORMED_ID}", json={"name": "rv-test-rename"})
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing name — Zod schema requires name.min(1).
        resp = _put(self.client, f"/{group['_id']}", json={})
        assert resp.status_code == 400, (
            f"[missing name] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing name] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing name] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = _put(self.client, f"/{_NONEXISTENT_ID}", json={"name": "rv-test-rename"})
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User group not found", (
            f"[nonexistent id] Expected 'User group not found', got {body['error']['message']!r}"
        )

        # admin-type group — controller throws ForbiddenError('Not Allowed').
        admin_group = _find_group_by_type(self.client, "admin")
        if admin_group is not None:
            resp = _put(self.client, f"/{admin_group['_id']}", json={"name": "rv-test-admin-rename"})
            assert resp.status_code == 403, (
                f"[admin group] Expected 403, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="403")
            assert body["error"]["code"] == "HTTP_FORBIDDEN", (
                f"[admin group] Expected 'HTTP_FORBIDDEN', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "Not Allowed", (
                f"[admin group] Expected 'Not Allowed', got {body['error']['message']!r}"
            )

        # everyone-type group — same ForbiddenError.
        everyone_group = _find_group_by_type(self.client, "everyone")
        if everyone_group is not None:
            resp = _put(self.client, f"/{everyone_group['_id']}", json={"name": "rv-test-everyone-rename"})
            assert resp.status_code == 403, (
                f"[everyone group] Expected 403, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateUserGroup", status_code="403")
            assert body["error"]["code"] == "HTTP_FORBIDDEN", (
                f"[everyone group] Expected 'HTTP_FORBIDDEN', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "Not Allowed", (
                f"[everyone group] Expected 'Not Allowed', got {body['error']['message']!r}"
            )


# ====================================================================
# DELETE /api/v1/userGroups/:groupId
# ====================================================================
@pytest.mark.integration
class TestDeleteUserGroup:
    """DELETE /api/v1/userGroups/:groupId — soft-delete a custom group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_delete_custom_group_response_schema(self) -> None:
        """Create then delete a custom group — response must match deleteUserGroup schema."""
        body = _create_group(self.client, "rv-test-delete-me", "custom")
        resp = _delete(self.client, f"/{body['_id']}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_openapi_operation(result, "deleteUserGroup")
        assert result["isDeleted"] is True

    def test_delete_user_group_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent id · 403 built-in group."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        # Missing Authorization header.
        resp = requests.delete(_url(self.client, f"/{group['_id']}"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUserGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed groupId — Zod regex rejects before the controller.
        resp = _delete(self.client, f"/{_MALFORMED_ID}")
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUserGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = _delete(self.client, f"/{_NONEXISTENT_ID}")
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUserGroup", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User group not found", (
            f"[nonexistent id] Expected 'User group not found', got {body['error']['message']!r}"
        )

        # Built-in group (admin or everyone) — controller throws ForbiddenError.
        builtin = _find_group_by_type(self.client, "admin") or _find_group_by_type(self.client, "everyone")
        if builtin is not None:
            resp = _delete(self.client, f"/{builtin['_id']}")
            assert resp.status_code == 403, (
                f"[built-in group] Expected 403, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "deleteUserGroup", status_code="403")
            assert body["error"]["code"] == "HTTP_FORBIDDEN", (
                f"[built-in group] Expected 'HTTP_FORBIDDEN', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "Only custom groups can be deleted", (
                f"[built-in group] Expected 'Only custom groups can be deleted', got {body['error']['message']!r}"
            )


# ====================================================================
# GET /api/v1/userGroups/:groupId/users
# ====================================================================
@pytest.mark.integration
class TestGetUsersInGroup:
    """GET /api/v1/userGroups/:groupId/users — list user IDs in a group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_users_in_group_response_schema(self) -> None:
        """Response must match getUsersInGroup schema across different query-param combinations."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found"

        # Default — no query params; server applies defaults (page=1, limit=25).
        resp = _get(self.client, f"/{group['_id']}/users")
        assert resp.status_code == 200, (
            f"[default] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUsersInGroup")

        # Explicit page + limit — pagination metadata must reflect requested values.
        resp = requests.get(
            _url(self.client, f"/{group['_id']}/users"),
            headers=self.client._headers(),
            params={"page": 1, "limit": 5},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[limit=5] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUsersInGroup")
        assert body["pagination"]["limit"] == 5
        assert len(body["users"]) <= 5

        # limit=1 — smallest valid page size.
        resp = requests.get(
            _url(self.client, f"/{group['_id']}/users"),
            headers=self.client._headers(),
            params={"page": 1, "limit": 1},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[limit=1] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUsersInGroup")
        assert len(body["users"]) <= 1

        # search="" empty string — equivalent to no filter; must still return valid schema.
        resp = requests.get(
            _url(self.client, f"/{group['_id']}/users"),
            headers=self.client._headers(),
            params={"search": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[search=''] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUsersInGroup")

        # page=2, limit=100 — may return empty users array; schema still valid.
        resp = requests.get(
            _url(self.client, f"/{group['_id']}/users"),
            headers=self.client._headers(),
            params={"page": 2, "limit": 100},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=2] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUsersInGroup")

    def test_get_users_in_group_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent group."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        # Missing Authorization header.
        resp = requests.get(_url(self.client, f"/{group['_id']}/users"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUsersInGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed groupId — Zod regex rejects before the controller.
        resp = _get(self.client, f"/{_MALFORMED_ID}/users")
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUsersInGroup", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = _get(self.client, f"/{_NONEXISTENT_ID}/users")
        assert resp.status_code == 404, (
            f"[nonexistent group] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUsersInGroup", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent group] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Group not found", (
            f"[nonexistent group] Expected 'Group not found', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/userGroups/users/:userId
# ====================================================================
@pytest.mark.integration
class TestGetGroupsForUser:
    """GET /api/v1/userGroups/users/:userId — groups a user belongs to."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_groups_for_user_response_schema(self) -> None:
        """Response must be an array matching getGroupsForUser schema."""
        # Known user who belongs to at least one group.
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found in any group")

        resp = _get(self.client, f"/users/{user_id}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list)
        assert_response_matches_openapi_operation(body, "getGroupsForUser")

        # Non-existent userId — controller returns empty array (no 404).
        resp = _get(self.client, f"/users/{_NONEXISTENT_ID}")
        assert resp.status_code == 200, (
            f"[nonexistent user] Expected 200 (empty array), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list)
        assert body == [], (
            f"[nonexistent user] Expected empty array, got {body!r}"
        )
        assert_response_matches_openapi_operation(body, "getGroupsForUser")

    def test_get_groups_for_user_negative_tests(self) -> None:
        """GET /users/:userId without Bearer token must return 401."""
        user_id = _find_user_id(self.client) or _NONEXISTENT_ID

        # Missing Authorization header.
        resp = requests.get(
            _url(self.client, f"/users/{user_id}"),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getGroupsForUser", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# POST /api/v1/userGroups/add-users + remove-users
# ====================================================================
@pytest.mark.integration
class TestAddAndRemoveUsersFromGroups:
    """POST /api/v1/userGroups/add-users and /remove-users"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_add_users_to_group_response_schema(self) -> None:
        """Add users to groups in different cardinality combinations — response must match addUsersToGroup schema."""
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found")

        # Single user → single group.
        group = _create_group(self.client, "rv-test-add-users", "custom")
        resp = _post(self.client, "/add-users", json={
            "userIds": [user_id],
            "groupIds": [group["_id"]],
        })
        assert resp.status_code == 200, (
            f"[1 user, 1 group] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "addUsersToGroup")
        del_resp = _delete(self.client, f"/{group['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup 1-user-1-group] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

        # Single user → two groups simultaneously.
        group1 = _create_group(self.client, "rv-test-add-multi-1", "custom")
        group2 = _create_group(self.client, "rv-test-add-multi-2", "custom")
        try:
            resp = _post(self.client, "/add-users", json={
                "userIds": [user_id],
                "groupIds": [group1["_id"], group2["_id"]],
            })
            assert resp.status_code == 200, (
                f"[1 user, 2 groups] Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), "addUsersToGroup")
            # Verify the user actually appears in both groups.
            for gid in (group1["_id"], group2["_id"]):
                users_resp = _get(self.client, f"/{gid}/users")
                user_ids_in_group = [u["_id"] for u in users_resp.json().get("users", [])]
                assert user_id in user_ids_in_group, (
                    f"[1 user, 2 groups] user {user_id} not found in group {gid}"
                )
        finally:
            del_resp1 = _delete(self.client, f"/{group1['_id']}")
            assert del_resp1.status_code == 200, (
                f"[cleanup add-multi-1] Expected 200 deleting group, got {del_resp1.status_code}: {del_resp1.text}"
            )
            del_resp2 = _delete(self.client, f"/{group2['_id']}")
            assert del_resp2.status_code == 200, (
                f"[cleanup add-multi-2] Expected 200 deleting group, got {del_resp2.status_code}: {del_resp2.text}"
            )

        # Re-add an already-member user — $addToSet is idempotent, must still return 200.
        group = _create_group(self.client, "rv-test-add-idempotent", "custom")
        setup_resp = _post(self.client, "/add-users", json={"userIds": [user_id], "groupIds": [group["_id"]]})
        assert setup_resp.status_code == 200, (
            f"[idempotent setup] Expected 200 adding user, got {setup_resp.status_code}: {setup_resp.text}"
        )
        resp = _post(self.client, "/add-users", json={
            "userIds": [user_id],
            "groupIds": [group["_id"]],
        })
        assert resp.status_code == 200, (
            f"[re-add idempotent] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "addUsersToGroup")
        del_resp = _delete(self.client, f"/{group['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup idempotent-add] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

    def test_remove_users_from_group_response_schema(self) -> None:
        """Add then remove users in different combinations — response must match removeUsersFromGroup schema."""
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found")

        # Single user removed from single group.
        group = _create_group(self.client, "rv-test-remove-users", "custom")
        setup_resp = _post(self.client, "/add-users", json={"userIds": [user_id], "groupIds": [group["_id"]]})
        assert setup_resp.status_code == 200, (
            f"[single-remove setup] Expected 200 adding user, got {setup_resp.status_code}: {setup_resp.text}"
        )
        resp = _post(self.client, "/remove-users", json={
            "userIds": [user_id],
            "groupIds": [group["_id"]],
        })
        assert resp.status_code == 200, (
            f"[1 user, 1 group] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "removeUsersFromGroup")
        del_resp = _delete(self.client, f"/{group['_id']}")
        assert del_resp.status_code == 200, (
            f"[cleanup single-remove] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
        )

        # Remove from two groups at once.
        group1 = _create_group(self.client, "rv-test-remove-multi-1", "custom")
        group2 = _create_group(self.client, "rv-test-remove-multi-2", "custom")
        try:
            setup_resp = _post(self.client, "/add-users", json={
                "userIds": [user_id],
                "groupIds": [group1["_id"], group2["_id"]],
            })
            assert setup_resp.status_code == 200, (
                f"[multi-remove setup] Expected 200 adding user to 2 groups, got {setup_resp.status_code}: {setup_resp.text}"
            )
            resp = _post(self.client, "/remove-users", json={
                "userIds": [user_id],
                "groupIds": [group1["_id"], group2["_id"]],
            })
            assert resp.status_code == 200, (
                f"[1 user, 2 groups] Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), "removeUsersFromGroup")
        finally:
            del_resp1 = _delete(self.client, f"/{group1['_id']}")
            assert del_resp1.status_code == 200, (
                f"[cleanup remove-multi-1] Expected 200 deleting group, got {del_resp1.status_code}: {del_resp1.text}"
            )
            del_resp2 = _delete(self.client, f"/{group2['_id']}")
            assert del_resp2.status_code == 200, (
                f"[cleanup remove-multi-2] Expected 200 deleting group, got {del_resp2.status_code}: {del_resp2.text}"
            )

        # Remove a user who is not a member — $pull on a missing element is a no-op;
        # controller must still return 200 (idempotent).
        group = _create_group(self.client, "rv-test-remove-idempotent", "custom")
        try:
            resp = _post(self.client, "/remove-users", json={
                "userIds": [user_id],
                "groupIds": [group["_id"]],
            })
            assert resp.status_code == 200, (
                f"[remove non-member idempotent] Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), "removeUsersFromGroup")
        finally:
            del_resp = _delete(self.client, f"/{group['_id']}")
            assert del_resp.status_code == 200, (
                f"[cleanup idempotent-remove] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
            )

    def test_add_users_negative_tests(self) -> None:
        """401 no auth · 400 missing userIds · 400 empty userIds · 400 missing groupIds · 400 empty groupIds."""
        # Missing Authorization header — auth check runs before controller validation.
        resp = requests.post(
            _url(self.client, "/add-users"),
            json={"userIds": [_NONEXISTENT_ID], "groupIds": [_NONEXISTENT_ID]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "addUsersToGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        created = _create_group(self.client, "rv-test-add-err", "custom")
        try:
            # userIds key absent — controller: 'userIds array is required'.
            resp = _post(self.client, "/add-users", json={"groupIds": [created["_id"]]})
            assert resp.status_code == 400, (
                f"[missing userIds] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "addUsersToGroup", status_code="400")
            assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
                f"[missing userIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "userIds array is required", (
                f"[missing userIds] Expected 'userIds array is required', got {body['error']['message']!r}"
            )

            # userIds is an empty array — same controller guard.
            resp = _post(self.client, "/add-users", json={"userIds": [], "groupIds": [created["_id"]]})
            assert resp.status_code == 400, (
                f"[empty userIds] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "addUsersToGroup", status_code="400")
            assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
                f"[empty userIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "userIds array is required", (
                f"[empty userIds] Expected 'userIds array is required', got {body['error']['message']!r}"
            )

            # groupIds key absent — controller: 'groupIds array is required'.
            resp = _post(self.client, "/add-users", json={"userIds": [_NONEXISTENT_ID]})
            assert resp.status_code == 400, (
                f"[missing groupIds] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "addUsersToGroup", status_code="400")
            assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
                f"[missing groupIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "groupIds array is required", (
                f"[missing groupIds] Expected 'groupIds array is required', got {body['error']['message']!r}"
            )

            # groupIds is an empty array — same controller guard.
            resp = _post(self.client, "/add-users", json={"userIds": [_NONEXISTENT_ID], "groupIds": []})
            assert resp.status_code == 400, (
                f"[empty groupIds] Expected 400, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "addUsersToGroup", status_code="400")
            assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
                f"[empty groupIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
            )
            assert body["error"]["message"] == "groupIds array is required", (
                f"[empty groupIds] Expected 'groupIds array is required', got {body['error']['message']!r}"
            )
        finally:
            del_resp = _delete(self.client, f"/{created['_id']}")
            assert del_resp.status_code == 200, (
                f"[cleanup add-err] Expected 200 deleting group, got {del_resp.status_code}: {del_resp.text}"
            )

    def test_remove_users_negative_tests(self) -> None:
        """401 no auth · 400 missing userIds · 400 empty groupIds."""
        # Missing Authorization header.
        resp = requests.post(
            _url(self.client, "/remove-users"),
            json={"userIds": [_NONEXISTENT_ID], "groupIds": [_NONEXISTENT_ID]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "removeUsersFromGroup", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # userIds key absent — controller: 'User IDs are required'.
        resp = _post(self.client, "/remove-users", json={"groupIds": [_NONEXISTENT_ID]})
        assert resp.status_code == 400, (
            f"[missing userIds] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "removeUsersFromGroup", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[missing userIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User IDs are required", (
            f"[missing userIds] Expected 'User IDs are required', got {body['error']['message']!r}"
        )

        # groupIds is an empty array — controller: 'Group IDs are required'.
        resp = _post(self.client, "/remove-users", json={"userIds": [_NONEXISTENT_ID], "groupIds": []})
        assert resp.status_code == 400, (
            f"[empty groupIds] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "removeUsersFromGroup", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[empty groupIds] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Group IDs are required", (
            f"[empty groupIds] Expected 'Group IDs are required', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/userGroups/stats/list
# ====================================================================
@pytest.mark.integration
class TestGetGroupStatistics:
    """GET /api/v1/userGroups/stats/list — aggregate stats."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_group_statistics_response_schema(self) -> None:
        """Response must be an array matching getGroupStatistics schema."""
        resp = _get(self.client, "/stats/list")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list)
        assert_response_matches_openapi_operation(body, "getGroupStatistics")

    def test_get_group_statistics_negative_tests(self) -> None:
        """GET /stats/list without Bearer token must return 401."""
        # Missing Authorization header.
        resp = requests.get(
            _url(self.client, "/stats/list"),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getGroupStatistics", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )
