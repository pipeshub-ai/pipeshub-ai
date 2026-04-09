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
    for g in resp.json():
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
    for g in resp.json():
        if not g.get("isDeleted"):
            return g
    return None


def _find_user_id(client: PipeshubClient) -> Optional[str]:
    """Get a userId from the everyone group's users list."""
    resp = _get(client)
    if resp.status_code != 200:
        return None
    for g in resp.json():
        if g.get("type") == "everyone" and g.get("users"):
            return str(g["users"][0])
    for g in resp.json():
        if g.get("users"):
            return str(g["users"][0])
    return None


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
        """Response must be an array matching UserGroupGetAllResponse schema."""
        resp = _get(self.client)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert isinstance(body, list), f"Expected array, got {type(body)}"
        assert_response_matches_schema(body, _SCHEMA_GET_ALL)


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
