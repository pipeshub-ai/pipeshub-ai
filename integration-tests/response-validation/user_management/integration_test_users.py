"""
Users API – Response Validation Integration Tests
===================================================

Tests JSON-returning routes under /api/v1/users against their YAML
response schemas.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response

Routes covered:
  GET    /api/v1/users                    — getAllUsers
  GET    /api/v1/users?blocked=true       — getAllUsers (blocked)
  GET    /api/v1/users/fetch/with-groups  — getAllUsersWithGroups
  GET    /api/v1/users/:id                — getUserById
  GET    /api/v1/users/:id/email          — getUserEmailByUserId
  GET    /api/v1/users/:id/adminCheck     — adminCheck
  GET    /api/v1/users/health             — health check
  GET    /api/v1/users/graph/list         — listUsers
  PATCH  /api/v1/users/:id/fullname       — updateFullName
  PATCH  /api/v1/users/:id/firstName      — updateFirstName
  PATCH  /api/v1/users/:id/lastName       — updateLastName
  PATCH  /api/v1/users/:id/designation    — updateDesignation
  PATCH  /api/v1/users/:id/email          — updateEmail
  PUT    /api/v1/users/:id                — updateUser
  DELETE /api/v1/users/:id                — deleteUser (via create + delete)

Skipped (non-JSON or destructive / special-purpose):
  PUT    /api/v1/users/dp                 — upload display picture (binary response)
  GET    /api/v1/users/dp                 — returns raw image bytes
  DELETE /api/v1/users/dp                 — requires existing picture
  PUT    /api/v1/users/:id/unblock        — requires a blocked user
  POST   /api/v1/users/bulk/invite        — requires SMTP config
  POST   /api/v1/users/:id/resend-invite  — requires SMTP config
  GET    /api/v1/users/email/exists        — internal scoped-token endpoint
  GET    /api/v1/users/internal/:id        — internal scoped-token endpoint
  GET    /api/v1/users/internal/admin-users — internal scoped-token endpoint
  POST   /api/v1/users/updateAppConfig     — internal scoped-token endpoint

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - Valid OAuth credentials (CLIENT_ID + CLIENT_SECRET) or test-user login
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
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

logger = logging.getLogger("users-integration-test")

# ------------------------------------------------------------------ #
# Load all user response schemas from the single merged YAML file
# ------------------------------------------------------------------ #
_SCHEMAS = load_yaml_schemas(
    "response-validation/schemas/user_management/user-response-schemas.yaml"
)

_SCHEMA_GET_ALL = _SCHEMAS["GetAllUsersResponse"]
_SCHEMA_GET_ALL_BLOCKED = _SCHEMAS["GetAllUsersBlockedResponse"]
_SCHEMA_GET_ALL_WITH_GROUPS = _SCHEMAS["GetAllUsersWithGroupsResponse"]
_SCHEMA_GET_EMAIL_BY_ID = _SCHEMAS["GetEmailByIdResponse"]
_SCHEMA_GET_BY_ID = _SCHEMAS["GetUserByIdResponse"]
_SCHEMA_CREATE = _SCHEMAS["CreateResponse"]
_SCHEMA_UPDATE_FULLNAME = _SCHEMAS["UpdateFullNameResponse"]
_SCHEMA_UPDATE_FIRSTNAME = _SCHEMAS["UpdateFirstNameResponse"]
_SCHEMA_UPDATE_LASTNAME = _SCHEMAS["UpdateLastNameResponse"]
_SCHEMA_UPDATE_DESIGNATION = _SCHEMAS["UpdateDesignationResponse"]
_SCHEMA_UPDATE_EMAIL = _SCHEMAS["UpdateEmailResponse"]
_SCHEMA_UPDATE_PUT = _SCHEMAS["UpdatePutResponse"]
_SCHEMA_DELETE = _SCHEMAS["DeleteResponse"]
_SCHEMA_ADMIN_CHECK = _SCHEMAS["AdminCheckResponse"]
_SCHEMA_HEALTH = _SCHEMAS["HealthResponse"]
_SCHEMA_GRAPH_LIST = _SCHEMAS["GraphListResponse"]


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _get_first_user_id(client: PipeshubClient) -> str:
    """Fetch the list of users and return the first user's _id."""
    resp = requests.get(
        f"{client.base_url}/api/v1/users",
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 200, f"Failed to list users: {resp.status_code}"
    users = resp.json()
    assert len(users) > 0, "No users found — cannot run user-specific tests"
    return users[0]["_id"]


def _get_user_by_id(client: PipeshubClient, user_id: str) -> dict:
    """Fetch a single user document by id."""
    resp = requests.get(
        f"{client.base_url}/api/v1/users/{user_id}",
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 200
    return resp.json()


# ====================================================================
# GET /api/v1/users/health
# ====================================================================
@pytest.mark.integration
class TestUsersHealth:
    """GET /api/v1/users/health — no auth required."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/users/health"

    def test_response_schema(self) -> None:
        """Response must match HealthResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_HEALTH)


# ====================================================================
# GET /api/v1/users
# ====================================================================
@pytest.mark.integration
class TestGetAllUsers:
    """GET /api/v1/users — list all non-blocked users."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/users"

    def test_response_schema(self) -> None:
        """Response must match GetAllUsersResponse array schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_ALL)

    def test_blocked_response_schema(self) -> None:
        """GET /api/v1/users?blocked=true — response must match blocked schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"blocked": "true"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_ALL_BLOCKED)


# ====================================================================
# GET /api/v1/users/fetch/with-groups
# ====================================================================
@pytest.mark.integration
class TestGetAllUsersWithGroups:
    """GET /api/v1/users/fetch/with-groups"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/users/fetch/with-groups"

    def test_response_schema(self) -> None:
        """Response must match GetAllUsersWithGroupsResponse array schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_ALL_WITH_GROUPS)


# ====================================================================
# GET /api/v1/users/:id
# ====================================================================
@pytest.mark.integration
class TestGetUserById:
    """GET /api/v1/users/:id — retrieve a single user by ID."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must match GetUserByIdResponse schema."""
        user_id = _get_first_user_id(self.client)
        resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{user_id}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_BY_ID)


# ====================================================================
# GET /api/v1/users/:id/email
# ====================================================================
@pytest.mark.integration
class TestGetEmailByUserId:
    """GET /api/v1/users/:id/email — get user email (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must match GetEmailByIdResponse schema."""
        user_id = _get_first_user_id(self.client)
        resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{user_id}/email",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_EMAIL_BY_ID)


# ====================================================================
# GET /api/v1/users/:id/adminCheck
# ====================================================================
@pytest.mark.integration
class TestAdminCheck:
    """GET /api/v1/users/:id/adminCheck — verify user has admin access."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Response must match AdminCheckResponse schema."""
        user_id = _get_first_user_id(self.client)
        resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{user_id}/adminCheck",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_ADMIN_CHECK)


# ====================================================================
# PATCH /api/v1/users/:id/fullname
# ====================================================================
@pytest.mark.integration
class TestUpdateFullName:
    """PATCH /api/v1/users/:id/fullname"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Update fullName — response must match UpdateFullNameResponse schema."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)
        original_name = original.get("fullName", "Test User")

        resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/fullname",
            headers=self.client._headers(),
            json={"fullName": "Integration Test User"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_FULLNAME)

        # Restore
        requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/fullname",
            headers=self.client._headers(),
            json={"fullName": original_name},
            timeout=self.client.timeout_seconds,
        )


# ====================================================================
# PATCH /api/v1/users/:id/firstName
# ====================================================================
@pytest.mark.integration
class TestUpdateFirstName:
    """PATCH /api/v1/users/:id/firstName"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Update firstName — response must match UpdateFirstNameResponse schema."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)
        original_first = original.get("firstName", "Test")

        resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/firstName",
            headers=self.client._headers(),
            json={"firstName": "IntegrationFirst"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_FIRSTNAME)

        # Restore
        requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/firstName",
            headers=self.client._headers(),
            json={"firstName": original_first},
            timeout=self.client.timeout_seconds,
        )


# ====================================================================
# PATCH /api/v1/users/:id/lastName
# ====================================================================
@pytest.mark.integration
class TestUpdateLastName:
    """PATCH /api/v1/users/:id/lastName"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Update lastName — response must match UpdateLastNameResponse schema."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)
        original_last = original.get("lastName", "User")

        resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/lastName",
            headers=self.client._headers(),
            json={"lastName": "IntegrationLast"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_LASTNAME)

        # Restore
        requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/lastName",
            headers=self.client._headers(),
            json={"lastName": original_last},
            timeout=self.client.timeout_seconds,
        )


# ====================================================================
# PATCH /api/v1/users/:id/designation
# ====================================================================
@pytest.mark.integration
class TestUpdateDesignation:
    """PATCH /api/v1/users/:id/designation"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Update designation — response must match UpdateDesignationResponse schema."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)
        original_designation = original.get("designation", "Engineer")

        resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/designation",
            headers=self.client._headers(),
            json={"designation": "Integration Tester"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_DESIGNATION)

        # Restore
        requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/designation",
            headers=self.client._headers(),
            json={"designation": original_designation},
            timeout=self.client.timeout_seconds,
        )


# ====================================================================
# PATCH /api/v1/users/:id/email
# ====================================================================
@pytest.mark.integration
class TestUpdateEmail:
    """PATCH /api/v1/users/:id/email"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema_same_email(self) -> None:
        """Update email to current email (no-op) — response must match schema."""
        user_id = _get_first_user_id(self.client)
        # Get current email via admin endpoint
        email_resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{user_id}/email",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert email_resp.status_code == 200
        current_email = email_resp.json()["email"]

        resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/email",
            headers=self.client._headers(),
            json={"email": current_email},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_EMAIL)


# ====================================================================
# PUT /api/v1/users/:id
# ====================================================================
@pytest.mark.integration
class TestUpdateUser:
    """PUT /api/v1/users/:id — full user update."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_response_schema(self) -> None:
        """Update user — response must match UpdatePutResponse schema."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)

        resp = requests.put(
            f"{self.client.base_url}/api/v1/users/{user_id}",
            headers=self.client._headers(),
            json={"fullName": original.get("fullName", "Test User")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_PUT)


# ====================================================================
# POST /api/v1/users + DELETE /api/v1/users/:id
# ====================================================================
@pytest.mark.integration
class TestCreateAndDeleteUser:
    """POST /api/v1/users + DELETE /api/v1/users/:id — create then delete."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self._created_user_id: Optional[str] = None

    def test_create_response_schema(self) -> None:
        """Create a user — response must match CreateResponse schema, then delete."""
        unique = uuid.uuid4().hex[:8]
        email = f"integration-test-{unique}@test-pipeshub.com"

        resp = requests.post(
            f"{self.client.base_url}/api/v1/users",
            headers=self.client._headers(),
            json={
                "fullName": f"Integration Test {unique}",
                "email": email,
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_CREATE)

        # Clean up — delete the created user
        created_id = body["_id"]
        del_resp = requests.delete(
            f"{self.client.base_url}/api/v1/users/{created_id}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert del_resp.status_code == 200, (
            f"Cleanup failed: {del_resp.status_code}: {del_resp.text}"
        )
        assert_response_matches_schema(del_resp.json(), _SCHEMA_DELETE)


# ====================================================================
# GET /api/v1/users/graph/list
# ====================================================================
@pytest.mark.integration
class TestGraphList:
    """GET /api/v1/users/graph/list — connector entity/user/list."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/users/graph/list"

    def test_response_schema(self) -> None:
        """Response must match GraphListResponse schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GRAPH_LIST)
