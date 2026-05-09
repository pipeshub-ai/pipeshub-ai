"""
User Groups API – Response Validation Integration Tests
========================================================

Tests every JSON-returning route under /api/v1/userGroups against its YAML
response schema.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response

Routes covered:
  GET    /api/v1/userGroups                    — getAllUserGroups
  GET    /api/v1/userGroups/:groupId           — getUserGroupById
  POST   /api/v1/userGroups                    — createUserGroup
  PUT    /api/v1/userGroups/:groupId           — updateGroup
  DELETE /api/v1/userGroups/:groupId           — deleteGroup
  POST   /api/v1/userGroups/add-users          — addUsersToGroups
  POST   /api/v1/userGroups/remove-users       — removeUsersFromGroups
  GET    /api/v1/userGroups/:groupId/users     — getUsersInGroup
  GET    /api/v1/userGroups/users/:userId      — getGroupsForUser
  GET    /api/v1/userGroups/stats/list         — getGroupStatistics
  GET    /api/v1/userGroups/health             — health check

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
from response_validator import (  # noqa: E402
    assert_response_matches_schema,
    load_yaml_schemas,
)

logger = logging.getLogger("user-groups-integration-test")

# ------------------------------------------------------------------ #
# Load all user group response schemas from the merged YAML file
# ------------------------------------------------------------------ #
_SCHEMAS = load_yaml_schemas(
    "response-validation/schemas/user_management/user-group-response-schemas.yaml"
)

_SCHEMA_CREATE = _SCHEMAS["UserGroupCreateResponse"]
_SCHEMA_DOCUMENT = _SCHEMAS["UserGroupDocumentResponse"]
_SCHEMA_GET_ALL = _SCHEMAS["UserGroupGetAllResponse"]
_SCHEMA_GET_USERS_IN_GROUP = _SCHEMAS["UserGroupGetUsersInGroupResponse"]
_SCHEMA_GET_GROUPS_FOR_USER = _SCHEMAS["UserGroupGetGroupsForUserResponse"]
_SCHEMA_ADD_USERS = _SCHEMAS["UserGroupAddUsersResponse"]
_SCHEMA_REMOVE_USERS = _SCHEMAS["UserGroupRemoveUsersResponse"]
_SCHEMA_STATISTICS = _SCHEMAS["UserGroupGetStatisticsResponse"]
_SCHEMA_HEALTH = _SCHEMAS["UserGroupHealthResponse"]

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

    def test_response_schema(self) -> None:
        """Response must match UserGroupHealthResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_HEALTH)

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

    def test_response_schema(self) -> None:
        """Response must be a paginated object matching UserGroupGetAllResponse schema."""
        resp = _get(self.client)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, dict), f"Expected object, got {type(body)}"
        assert "groups" in body, "Expected 'groups' key in response"
        assert "pagination" in body, "Expected 'pagination' key in response"
        assert isinstance(body["groups"], list), "Expected 'groups' to be an array"
        assert_response_matches_schema(body, _SCHEMA_GET_ALL)

    def test_no_auth_returns_401(self) -> None:
        """Request without a Bearer token must be rejected with 401."""
        resp = requests.get(_url(self.client), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
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

    def test_response_schema(self) -> None:
        """Fetch an existing group — response must match schema."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = _get(self.client, f"/{group['_id']}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_DOCUMENT)

    def test_error_cases(self) -> None:
        """401 without auth · 400 for malformed ObjectId · 404 for non-existent id."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = requests.get(_url(self.client, f"/{group['_id']}"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _get(self.client, f"/{_MALFORMED_ID}")
        assert resp.status_code == 400, f"Expected 400 (malformed id), got {resp.status_code}: {resp.text}"

        resp = _get(self.client, f"/{_NONEXISTENT_ID}")
        assert resp.status_code == 404, f"Expected 404 (nonexistent id), got {resp.status_code}: {resp.text}"


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
        """Create a custom group — 201 response must match schema."""
        body = _create_group(self.client, "rv-test-custom-create", "custom")
        assert_response_matches_schema(body, _SCHEMA_CREATE)
        assert body["name"] == "rv-test-custom-create"
        assert body["type"] == "custom"
        _delete_group(self.client, body["_id"])

    def test_error_cases(self) -> None:
        """401 no auth · 400 missing name · 400 missing type · 400 unknown type · 400 reserved type · 400 duplicate name."""
        resp = requests.post(
            _url(self.client),
            json={"name": "rv-test-no-auth", "type": "custom"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, json={"type": "custom"})
        assert resp.status_code == 400, f"Expected 400 (missing name), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, json={"name": "rv-test-no-type"})
        assert resp.status_code == 400, f"Expected 400 (missing type), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, json={"name": "rv-test-bad-type", "type": "unknown"})
        assert resp.status_code == 400, f"Expected 400 (unknown type), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, json={"name": "rv-test-admin-type", "type": "admin"})
        assert resp.status_code == 400, f"Expected 400 (reserved type), got {resp.status_code}: {resp.text}"

        body = _create_group(self.client, "rv-test-duplicate-name", "custom")
        try:
            resp = _post(self.client, json={"name": "rv-test-duplicate-name", "type": "custom"})
            assert resp.status_code == 400, f"Expected 400 (duplicate name), got {resp.status_code}: {resp.text}"
        finally:
            _delete_group(self.client, body["_id"])


# ====================================================================
# PUT /api/v1/userGroups/:groupId
# ====================================================================
@pytest.mark.integration
class TestUpdateUserGroup:
    """PUT /api/v1/userGroups/:groupId — rename a group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_update_name_response_schema(self) -> None:
        """Rename a custom group — response must match DocumentResponse schema."""
        body = _create_group(self.client, "rv-test-update-name", "custom")
        resp = _put(self.client, f"/{body['_id']}", json={"name": "rv-test-renamed"})
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_schema(result, _SCHEMA_DOCUMENT)
        assert result["name"] == "rv-test-renamed"
        _delete_group(self.client, result["_id"])

    def test_error_cases(self) -> None:
        """401 no auth · 400 malformed id · 400 missing name · 404 nonexistent id · 403 protected group types."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = requests.put(
            _url(self.client, f"/{group['_id']}"),
            json={"name": "rv-test-no-auth-rename"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _put(self.client, f"/{_MALFORMED_ID}", json={"name": "rv-test-rename"})
        assert resp.status_code == 400, f"Expected 400 (malformed id), got {resp.status_code}: {resp.text}"

        resp = _put(self.client, f"/{group['_id']}", json={})
        assert resp.status_code == 400, f"Expected 400 (missing name), got {resp.status_code}: {resp.text}"

        resp = _put(self.client, f"/{_NONEXISTENT_ID}", json={"name": "rv-test-rename"})
        assert resp.status_code == 404, f"Expected 404 (nonexistent id), got {resp.status_code}: {resp.text}"

        admin_group = _find_group_by_type(self.client, "admin")
        if admin_group is not None:
            resp = _put(self.client, f"/{admin_group['_id']}", json={"name": "rv-test-admin-rename"})
            assert resp.status_code == 403, f"Expected 403 (admin group), got {resp.status_code}: {resp.text}"

        everyone_group = _find_group_by_type(self.client, "everyone")
        if everyone_group is not None:
            resp = _put(self.client, f"/{everyone_group['_id']}", json={"name": "rv-test-everyone-rename"})
            assert resp.status_code == 403, f"Expected 403 (everyone group), got {resp.status_code}: {resp.text}"


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
        """Create then delete a custom group — response must match schema."""
        body = _create_group(self.client, "rv-test-delete-me", "custom")
        resp = _delete(self.client, f"/{body['_id']}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        result = resp.json()
        assert_response_matches_schema(result, _SCHEMA_DOCUMENT)
        assert result["isDeleted"] is True

    def test_error_cases(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent id · 403 built-in group."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = requests.delete(_url(self.client, f"/{group['_id']}"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _delete(self.client, f"/{_MALFORMED_ID}")
        assert resp.status_code == 400, f"Expected 400 (malformed id), got {resp.status_code}: {resp.text}"

        resp = _delete(self.client, f"/{_NONEXISTENT_ID}")
        assert resp.status_code == 404, f"Expected 404 (nonexistent id), got {resp.status_code}: {resp.text}"

        builtin = _find_group_by_type(self.client, "admin") or _find_group_by_type(self.client, "everyone")
        if builtin is not None:
            resp = _delete(self.client, f"/{builtin['_id']}")
            assert resp.status_code == 403, f"Expected 403 (built-in group), got {resp.status_code}: {resp.text}"


# ====================================================================
# GET /api/v1/userGroups/:groupId/users
# ====================================================================
@pytest.mark.integration
class TestGetUsersInGroup:
    """GET /api/v1/userGroups/:groupId/users — list user IDs in a group."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must match UserGroupGetUsersInGroupResponse schema."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found"

        resp = _get(self.client, f"/{group['_id']}/users")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_USERS_IN_GROUP)

    def test_error_cases(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent group."""
        group = _find_any_group(self.client)
        assert group is not None, "No groups found to test"

        resp = requests.get(_url(self.client, f"/{group['_id']}/users"), timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _get(self.client, f"/{_MALFORMED_ID}/users")
        assert resp.status_code == 400, f"Expected 400 (malformed id), got {resp.status_code}: {resp.text}"

        resp = _get(self.client, f"/{_NONEXISTENT_ID}/users")
        assert resp.status_code == 404, f"Expected 404 (nonexistent group), got {resp.status_code}: {resp.text}"


# ====================================================================
# GET /api/v1/userGroups/users/:userId
# ====================================================================
@pytest.mark.integration
class TestGetGroupsForUser:
    """GET /api/v1/userGroups/users/:userId — groups a user belongs to."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must be an array matching UserGroupGetGroupsForUserResponse."""
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found in any group")

        resp = _get(self.client, f"/users/{user_id}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list)
        assert_response_matches_schema(body, _SCHEMA_GET_GROUPS_FOR_USER)

    def test_no_auth_returns_401(self) -> None:
        """GET /users/:userId without Bearer token must return 401."""
        user_id = _find_user_id(self.client) or _NONEXISTENT_ID
        resp = requests.get(
            _url(self.client, f"/users/{user_id}"),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
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

    def test_add_users_response_schema(self) -> None:
        """Add a user to a custom group — response must match schema."""
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found")

        body = _create_group(self.client, "rv-test-add-users", "custom")
        group_id = body["_id"]

        resp = _post(self.client, "/add-users", json={
            "userIds": [user_id],
            "groupIds": [group_id],
        })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_ADD_USERS)
        _delete_group(self.client, group_id)

    def test_remove_users_response_schema(self) -> None:
        """Add then remove a user from a group — response must match schema."""
        user_id = _find_user_id(self.client)
        if not user_id:
            pytest.skip("No user ID found")

        body = _create_group(self.client, "rv-test-remove-users", "custom")
        group_id = body["_id"]

        _post(self.client, "/add-users", json={
            "userIds": [user_id],
            "groupIds": [group_id],
        })

        resp = _post(self.client, "/remove-users", json={
            "userIds": [user_id],
            "groupIds": [group_id],
        })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_REMOVE_USERS)
        _delete_group(self.client, group_id)


    def test_add_users_error_cases(self) -> None:
        """401 no auth · 400 missing userIds · 400 empty userIds · 400 missing groupIds · 400 empty groupIds."""
        resp = requests.post(
            _url(self.client, "/add-users"),
            json={"userIds": [_NONEXISTENT_ID], "groupIds": [_NONEXISTENT_ID]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        body = _create_group(self.client, "rv-test-add-err", "custom")
        try:
            resp = _post(self.client, "/add-users", json={"groupIds": [body["_id"]]})
            assert resp.status_code == 400, f"Expected 400 (missing userIds), got {resp.status_code}: {resp.text}"

            resp = _post(self.client, "/add-users", json={"userIds": [], "groupIds": [body["_id"]]})
            assert resp.status_code == 400, f"Expected 400 (empty userIds), got {resp.status_code}: {resp.text}"

            resp = _post(self.client, "/add-users", json={"userIds": [_NONEXISTENT_ID]})
            assert resp.status_code == 400, f"Expected 400 (missing groupIds), got {resp.status_code}: {resp.text}"

            resp = _post(self.client, "/add-users", json={"userIds": [_NONEXISTENT_ID], "groupIds": []})
            assert resp.status_code == 400, f"Expected 400 (empty groupIds), got {resp.status_code}: {resp.text}"
        finally:
            _delete_group(self.client, body["_id"])

    def test_remove_users_error_cases(self) -> None:
        """401 no auth · 400 missing userIds · 400 empty groupIds."""
        resp = requests.post(
            _url(self.client, "/remove-users"),
            json={"userIds": [_NONEXISTENT_ID], "groupIds": [_NONEXISTENT_ID]},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, "/remove-users", json={"groupIds": [_NONEXISTENT_ID]})
        assert resp.status_code == 400, f"Expected 400 (missing userIds), got {resp.status_code}: {resp.text}"

        resp = _post(self.client, "/remove-users", json={"userIds": [_NONEXISTENT_ID], "groupIds": []})
        assert resp.status_code == 400, f"Expected 400 (empty groupIds), got {resp.status_code}: {resp.text}"


# ====================================================================
# GET /api/v1/userGroups/stats/list
# ====================================================================
@pytest.mark.integration
class TestGetGroupStatistics:
    """GET /api/v1/userGroups/stats/list — aggregate stats."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must be an array matching UserGroupGetStatisticsResponse."""
        resp = _get(self.client, "/stats/list")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list)
        assert_response_matches_schema(body, _SCHEMA_STATISTICS)

    def test_no_auth_returns_401(self) -> None:
        """GET /stats/list without Bearer token must return 401."""
        resp = requests.get(
            _url(self.client, "/stats/list"),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
