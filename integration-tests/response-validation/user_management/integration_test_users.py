"""
Users API – Response Validation Integration Tests
===================================================

Tests JSON-returning routes under /api/v1/users against OpenAPI response
schemas in ``pipeshub-openapi.yaml``.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response
  - Error envelope shape, machine-readable code, and human-readable message

Routes covered:
  GET    /api/v1/users                    — getAllUsers
  GET    /api/v1/users?blocked=true       — getAllUsers (blocked)
  GET    /api/v1/users/fetch/with-groups  — getAllUsersWithGroups
  GET    /api/v1/users/:id                — getUserById
  GET    /api/v1/users/:id/email          — getUserEmailByUserId
  GET    /api/v1/users/:id/adminCheck     — adminCheck
  GET    /api/v1/users/health             — health check
  GET    /api/v1/users/graph/list         — listUsers (defaults, page+limit, page2, search)
  PUT    /api/v1/users/dp                 — uploadUserDisplayPicture (PNG + JPEG, lifecycle)
  GET    /api/v1/users/dp                 — getUserDisplayPicture (binary, no-dp sentinel)
  DELETE /api/v1/users/dp                 — removeUserDisplayPicture
  PATCH  /api/v1/users/:id/fullname       — updateFullName
  PATCH  /api/v1/users/:id/firstName      — updateFirstName
  PATCH  /api/v1/users/:id/lastName       — updateLastName
  PATCH  /api/v1/users/:id/designation    — updateDesignation
  PATCH  /api/v1/users/:id/email          — updateEmail
  PUT    /api/v1/users/:id                — updateUser
  DELETE /api/v1/users/:id                — deleteUser (via create + delete)

Skipped (non-JSON or destructive / special-purpose):
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
import base64
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

logger = logging.getLogger("users-integration-test")


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


# A valid-format ObjectId guaranteed not to exist in any test organisation.
_NONEXISTENT_ID = "000000000000000000000000"
# Intentionally fails the 24-char hex ObjectId regex.
_MALFORMED_ID = "not-a-valid-objectid"

# Minimal 1×1 red-pixel PNG (same verified base64 used across image-upload tests).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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

    def test_users_health_response_schema(self) -> None:
        """Response must match HealthResponse schema."""
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getUsersHealth")

    def test_unsupported_method_returns_4xx(self) -> None:
        """POST to /health is not a registered method — must return 4xx."""
        resp = requests.post(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code >= 400, (
            f"Expected 4xx for unsupported POST, got {resp.status_code}"
        )


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

    def test_get_all_users_response_schema(self) -> None:
        """Response must match GetAllUsersResponse array schema across query-param combinations."""
        # Default call — no params, server uses its defaults.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[default] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

        # blocked=true — must return the blocked-users list (same schema).
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"blocked": "true"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[blocked=true] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

        # Explicit page + limit — result must respect the page-size cap.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"page": 1, "limit": 5},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=1,limit=5] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUsers")
        assert len(body) <= 5, (
            f"[page=1,limit=5] Expected at most 5 users, got {len(body)}"
        )

        # search — may return a subset; schema must still hold.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"search": "a", "page": 1, "limit": 10},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[search] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

        # groupIds — comma-separated group IDs filter users by group membership.
        groups_resp = requests.get(
            f"{self.client.base_url}/api/v1/userGroups",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        if groups_resp.status_code == 200:
            groups = groups_resp.json().get("groups", [])
            active_groups = [g for g in groups if not g.get("isDeleted")]

            if active_groups:
                # Single group ID — only members of that group are returned.
                single_id = active_groups[0]["_id"]
                resp = requests.get(
                    self.url,
                    headers=self.client._headers(),
                    params={"groupIds": single_id, "page": 1, "limit": 25},
                    timeout=self.client.timeout_seconds,
                )
                assert resp.status_code == 200, (
                    f"[groupIds single] Expected 200, got {resp.status_code}: {resp.text}"
                )
                assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

            if len(active_groups) >= 2:
                # Multiple group IDs (comma-separated) — returns union of members across both groups.
                two_ids = f"{active_groups[0]['_id']},{active_groups[1]['_id']}"
                resp = requests.get(
                    self.url,
                    headers=self.client._headers(),
                    params={"groupIds": two_ids, "page": 1, "limit": 25},
                    timeout=self.client.timeout_seconds,
                )
                assert resp.status_code == 200, (
                    f"[groupIds two] Expected 200, got {resp.status_code}: {resp.text}"
                )
                assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

        # Nonexistent group ID — valid ObjectId format, no matching group; must return valid (empty) response.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"groupIds": "000000000000000000000000", "page": 1, "limit": 25},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[groupIds nonexistent] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUsers")

    def test_get_all_users_negative_tests(self) -> None:
        """Request without a Bearer token must return 401."""
        # Missing Authorization header — auth middleware rejects before any DB query.
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUsers", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )


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

    def test_get_all_users_with_groups_response_schema(self) -> None:
        """Response must match GetAllUsersWithGroupsResponse array schema."""
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAllUsersWithGroups")

    def test_get_all_users_with_groups_negative_tests(self) -> None:
        """Request without a Bearer token must return 401."""
        # Missing Authorization header — auth middleware rejects immediately.
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getAllUsersWithGroups", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/users/:id
# ====================================================================
@pytest.mark.integration
class TestGetUserById:
    """GET /api/v1/users/:id — retrieve a single user by ID."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_user_by_id_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "getUserById")

    def test_get_user_by_id_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header — auth check runs before DB lookup.
        resp = requests.get(f"{base}/{user_id}", timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserById", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects non-hex-24 params before the controller.
        resp = requests.get(
            f"{base}/{_MALFORMED_ID}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserById", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.get(
            f"{base}/{_NONEXISTENT_ID}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserById", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/users/:id/email
# ====================================================================
@pytest.mark.integration
class TestGetEmailByUserId:
    """GET /api/v1/users/:id/email — get user email (admin only)."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_get_email_by_user_id_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "getUserEmailById")

    def test_get_email_by_user_id_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header — auth check runs before DB lookup.
        resp = requests.get(
            f"{base}/{user_id}/email",
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserEmailById", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before reaching the controller.
        resp = requests.get(
            f"{base}/{_MALFORMED_ID}/email",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserEmailById", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.get(
            f"{base}/{_NONEXISTENT_ID}/email",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getUserEmailById", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
        )


# ====================================================================
# GET /api/v1/users/:id/adminCheck
# ====================================================================
@pytest.mark.integration
class TestAdminCheck:
    """GET /api/v1/users/:id/adminCheck — verify user has admin access."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_admin_check_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "adminCheck")

    def test_admin_check_negative_tests(self) -> None:
        """401 no auth · 400 malformed id."""
        user_id = _get_first_user_id(self.client)

        # Missing Authorization header — auth middleware rejects before the controller.
        resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{user_id}/adminCheck",
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "adminCheck", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects the non-hex-24 param.
        resp = requests.get(
            f"{self.client.base_url}/api/v1/users/{_MALFORMED_ID}/adminCheck",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "adminCheck", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )


# ====================================================================
# PATCH /api/v1/users/:id/fullname
# ====================================================================
@pytest.mark.integration
class TestUpdateFullName:
    """PATCH /api/v1/users/:id/fullname"""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_update_full_name_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "updateFullName")

        # Restore original value — assert success so a failed restore is visible.
        restore_resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/fullname",
            headers=self.client._headers(),
            json={"fullName": original_name},
            timeout=self.client.timeout_seconds,
        )
        assert restore_resp.status_code == 200, (
            f"[restore] Expected 200 restoring fullName, got {restore_resp.status_code}: {restore_resp.text}"
        )

    def test_update_full_name_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing fullName · 400 empty fullName · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header — auth check fires before Zod validation.
        resp = requests.patch(
            f"{base}/{user_id}/fullname",
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFullName", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.patch(
            f"{base}/{_MALFORMED_ID}/fullname",
            headers=self.client._headers(),
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFullName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing fullName field — Zod requires fullName.min(1).
        resp = requests.patch(
            f"{base}/{user_id}/fullname",
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing fullName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFullName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing fullName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing fullName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Empty string for fullName — Zod min(1) rejects zero-length values.
        resp = requests.patch(
            f"{base}/{user_id}/fullname",
            headers=self.client._headers(),
            json={"fullName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[empty fullName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFullName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty fullName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[empty fullName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.patch(
            f"{base}/{_NONEXISTENT_ID}/fullname",
            headers=self.client._headers(),
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFullName", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
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

    def test_update_first_name_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "updateFirstName")

        # Restore original value — assert success so a failed restore is visible.
        restore_resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/firstName",
            headers=self.client._headers(),
            json={"firstName": original_first},
            timeout=self.client.timeout_seconds,
        )
        assert restore_resp.status_code == 200, (
            f"[restore] Expected 200 restoring firstName, got {restore_resp.status_code}: {restore_resp.text}"
        )

    def test_update_first_name_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing firstName · 400 empty firstName · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header.
        resp = requests.patch(
            f"{base}/{user_id}/firstName",
            json={"firstName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFirstName", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.patch(
            f"{base}/{_MALFORMED_ID}/firstName",
            headers=self.client._headers(),
            json={"firstName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFirstName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing firstName field — Zod requires firstName.min(1).
        resp = requests.patch(
            f"{base}/{user_id}/firstName",
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing firstName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFirstName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing firstName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing firstName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Empty string for firstName — Zod min(1) rejects zero-length values.
        resp = requests.patch(
            f"{base}/{user_id}/firstName",
            headers=self.client._headers(),
            json={"firstName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[empty firstName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFirstName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty firstName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[empty firstName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = requests.patch(
            f"{base}/{_NONEXISTENT_ID}/firstName",
            headers=self.client._headers(),
            json={"firstName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateFirstName", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
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

    def test_update_last_name_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "updateLastName")

        # Restore original value — assert success so a failed restore is visible.
        restore_resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/lastName",
            headers=self.client._headers(),
            json={"lastName": original_last},
            timeout=self.client.timeout_seconds,
        )
        assert restore_resp.status_code == 200, (
            f"[restore] Expected 200 restoring lastName, got {restore_resp.status_code}: {restore_resp.text}"
        )

    def test_update_last_name_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing lastName · 400 empty lastName · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header.
        resp = requests.patch(
            f"{base}/{user_id}/lastName",
            json={"lastName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateLastName", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.patch(
            f"{base}/{_MALFORMED_ID}/lastName",
            headers=self.client._headers(),
            json={"lastName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateLastName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing lastName field — Zod requires lastName.min(1).
        resp = requests.patch(
            f"{base}/{user_id}/lastName",
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing lastName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateLastName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing lastName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing lastName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Empty string for lastName — Zod min(1) rejects zero-length values.
        resp = requests.patch(
            f"{base}/{user_id}/lastName",
            headers=self.client._headers(),
            json={"lastName": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[empty lastName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateLastName", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty lastName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[empty lastName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = requests.patch(
            f"{base}/{_NONEXISTENT_ID}/lastName",
            headers=self.client._headers(),
            json={"lastName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateLastName", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
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

    def test_update_designation_response_schema(self) -> None:
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
        assert_response_matches_openapi_operation(resp.json(), "updateDesignation")

        # Restore original value — assert success so a failed restore is visible.
        restore_resp = requests.patch(
            f"{self.client.base_url}/api/v1/users/{user_id}/designation",
            headers=self.client._headers(),
            json={"designation": original_designation},
            timeout=self.client.timeout_seconds,
        )
        assert restore_resp.status_code == 200, (
            f"[restore] Expected 200 restoring designation, got {restore_resp.status_code}: {restore_resp.text}"
        )

    def test_update_designation_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing designation · 400 empty designation · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header.
        resp = requests.patch(
            f"{base}/{user_id}/designation",
            json={"designation": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateDesignation", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.patch(
            f"{base}/{_MALFORMED_ID}/designation",
            headers=self.client._headers(),
            json={"designation": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateDesignation", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing designation field — Zod requires designation.min(1).
        resp = requests.patch(
            f"{base}/{user_id}/designation",
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing designation] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateDesignation", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing designation] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing designation] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Empty string for designation — Zod min(1) rejects zero-length values.
        resp = requests.patch(
            f"{base}/{user_id}/designation",
            headers=self.client._headers(),
            json={"designation": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[empty designation] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateDesignation", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty designation] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[empty designation] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist.
        resp = requests.patch(
            f"{base}/{_NONEXISTENT_ID}/designation",
            headers=self.client._headers(),
            json={"designation": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateDesignation", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
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

    def test_update_email_same_value_response_schema(self) -> None:
        """Update email to current email (idempotent no-op) — response must match schema."""
        user_id = _get_first_user_id(self.client)
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
        assert_response_matches_openapi_operation(resp.json(), "updateEmail")

    def test_update_email_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 missing email · 400 empty email · 400 invalid format · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header.
        resp = requests.patch(
            f"{base}/{user_id}/email",
            json={"email": "test@example.com"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.patch(
            f"{base}/{_MALFORMED_ID}/email",
            headers=self.client._headers(),
            json={"email": "test@example.com"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing email field entirely — Zod requires the email key.
        resp = requests.patch(
            f"{base}/{user_id}/email",
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing email] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing email] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing email] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Empty string email — Zod email() rejects zero-length / non-email values.
        resp = requests.patch(
            f"{base}/{user_id}/email",
            headers=self.client._headers(),
            json={"email": ""},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[empty email] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[empty email] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[empty email] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Invalid email format — Zod email() validator rejects before the controller.
        resp = requests.patch(
            f"{base}/{user_id}/email",
            headers=self.client._headers(),
            json={"email": "not-a-valid-email"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[invalid email] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid email] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[invalid email] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.patch(
            f"{base}/{_NONEXISTENT_ID}/email",
            headers=self.client._headers(),
            json={"email": "test@example.com"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateEmail", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
        )


# ====================================================================
# PUT /api/v1/users/:id
# ====================================================================
@pytest.mark.integration
class TestUpdateUser:
    """PUT /api/v1/users/:id — full user update."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client

    def test_update_user_response_schema(self) -> None:
        """Update user with every valid single-field and multi-field combination."""
        user_id = _get_first_user_id(self.client)
        original = _get_user_by_id(self.client, user_id)
        base = f"{self.client.base_url}/api/v1/users/{user_id}"

        # fullName only — the minimal valid body.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"fullName": original.get("fullName", "Test User")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[fullName only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # designation only — optional string field.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"designation": original.get("designation", "Engineer")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[designation only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # firstName + lastName together.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={
                "firstName": original.get("firstName", "Test"),
                "lastName": original.get("lastName", "User"),
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[firstName+lastName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # lastName only — isolated update of last name.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"lastName": original.get("lastName", "User")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[lastName only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # middleName only — optional middle name field.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"middleName": "Integration"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[middleName only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # firstName + lastName + middleName — all three name parts at once.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={
                "firstName": original.get("firstName", "Test"),
                "middleName": "Middle",
                "lastName": original.get("lastName", "User"),
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[firstName+middleName+lastName] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # mobile only — valid E.164-style number; regex ^\+?[0-9]{10,15}$.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"mobile": "+15551234567"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[mobile only] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # address partial (city + country) — subset of address sub-fields.
        # "India" is a valid jurisdiction enum value used by the Users schema.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"address": {"city": "Mumbai", "country": "India"}},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[address partial] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # Full address — all five address sub-fields populated.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={
                "address": {
                    "addressLine1": "123 Test Street",
                    "city": "Bengaluru",
                    "state": "Karnataka",
                    "postCode": "560001",
                    "country": "India",
                },
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[full address] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # dataCollectionConsent boolean flag — false (safe, non-destructive value).
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"dataCollectionConsent": False},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[dataCollectionConsent false] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # hasLoggedIn boolean flag — false (safe, non-destructive value).
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={"hasLoggedIn": False},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[hasLoggedIn false] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # All updatable fields together — fullName + name parts + mobile + designation +
        # address (all sub-fields) + consent flags — exercises the entire schema in one shot.
        resp = requests.put(
            base,
            headers=self.client._headers(),
            json={
                "fullName": original.get("fullName", "Test User"),
                "firstName": original.get("firstName", "Test"),
                "middleName": "All",
                "lastName": original.get("lastName", "User"),
                "designation": original.get("designation", "Engineer"),
                "mobile": "+15559876543",
                "address": {
                    "addressLine1": "456 Combined Lane",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "postCode": "411001",
                    "country": "India",
                },
                "dataCollectionConsent": True,
                "hasLoggedIn": True,
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[all fields] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "updateUser")

        # Restore the original state across all fields that were touched.
        # Only include optional fields (mobile, middleName, designation) if they
        # were actually set on the original user — sending "" would store an
        # empty string in the DB, which is not a valid value for these fields.
        original_address = original.get("address") or {}
        restore_payload: dict = {
            "fullName": original.get("fullName", "Test User"),
            "dataCollectionConsent": original.get("dataCollectionConsent", False),
            "hasLoggedIn": original.get("hasLoggedIn", True),
        }
        for optional_field in ("firstName", "lastName", "middleName", "designation", "mobile"):
            if original.get(optional_field):
                restore_payload[optional_field] = original[optional_field]
        if original_address:
            restore_payload["address"] = original_address
        restore_resp = requests.put(
            base,
            headers=self.client._headers(),
            json=restore_payload,
            timeout=self.client.timeout_seconds,
        )
        assert restore_resp.status_code == 200, (
            f"[restore] Expected 200 restoring user, got {restore_resp.status_code}: {restore_resp.text}"
        )

    def test_update_user_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 400 unknown field (strict schema) · 404 nonexistent id."""
        user_id = _get_first_user_id(self.client)
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header.
        resp = requests.put(
            f"{base}/{user_id}",
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.put(
            f"{base}/{_MALFORMED_ID}",
            headers=self.client._headers(),
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Unknown field — updateUserBody uses .strict(), so extra keys are rejected.
        resp = requests.put(
            f"{base}/{user_id}",
            headers=self.client._headers(),
            json={"unknownField": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[unknown field] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[unknown field] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[unknown field] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.put(
            f"{base}/{_NONEXISTENT_ID}",
            headers=self.client._headers(),
            json={"fullName": "x"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
        )

        # Invalid mobile format — Zod refine check: must match ^\+?[0-9]{10,15}$.
        resp = requests.put(
            f"{base}/{user_id}",
            headers=self.client._headers(),
            json={"mobile": "not-a-phone"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[invalid mobile] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid mobile] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[invalid mobile] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Invalid email inside the body — Zod email() rejects before the controller.
        resp = requests.put(
            f"{base}/{user_id}",
            headers=self.client._headers(),
            json={"email": "not-an-email"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[invalid email in body] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid email in body] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[invalid email in body] Expected 'Validation failed', got {body['error']['message']!r}"
        )


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

    def test_create_user_response_schema(self) -> None:
        """Create users with every valid field combination — response must match schema, then clean up."""
        base = f"{self.client.base_url}/api/v1/users"

        def _cleanup(user_id: str, label: str) -> None:
            del_resp = requests.delete(
                f"{base}/{user_id}",
                headers=self.client._headers(),
                timeout=self.client.timeout_seconds,
            )
            assert del_resp.status_code == 200, (
                f"[{label} cleanup] Expected 200, got {del_resp.status_code}: {del_resp.text}"
            )
            assert_response_matches_openapi_operation(del_resp.json(), "deleteUser")

        # Required fields only — fullName + email.
        unique = uuid.uuid4().hex[:8]
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={
                "fullName": f"Integration Test {unique}",
                "email": f"integration-test-{unique}@test-pipeshub.com",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"[required only] Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="201")
        _cleanup(body["_id"], "required only")

        # With mobile — valid E.164-style number; regex ^\+?[0-9]{10,15}$.
        unique = uuid.uuid4().hex[:8]
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={
                "fullName": f"Mobile Test {unique}",
                "email": f"mobile-test-{unique}@test-pipeshub.com",
                "mobile": "+15551234567",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"[with mobile] Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="201")
        _cleanup(body["_id"], "with mobile")

        # With designation — optional job-title field.
        unique = uuid.uuid4().hex[:8]
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={
                "fullName": f"Designation Test {unique}",
                "email": f"designation-test-{unique}@test-pipeshub.com",
                "designation": "QA Engineer",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"[with designation] Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="201")
        _cleanup(body["_id"], "with designation")

        # All optional fields — fullName + email + mobile + designation.
        unique = uuid.uuid4().hex[:8]
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={
                "fullName": f"Full Test {unique}",
                "email": f"full-test-{unique}@test-pipeshub.com",
                "mobile": "+919876543210",
                "designation": "Staff Engineer",
            },
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 201, (
            f"[all fields] Expected 201, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="201")
        _cleanup(body["_id"], "all fields")

    def test_create_user_negative_tests(self) -> None:
        """401 no auth · 400 missing fullName · 400 missing email · 400 invalid email format."""
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header — auth middleware rejects before Zod.
        resp = requests.post(
            base,
            json={"fullName": "x", "email": "x@test.com"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Missing fullName — Zod requires fullName.min(1).
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={"email": "x@test.com"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing fullName] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing fullName] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing fullName] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Missing email — Zod requires email.
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={"fullName": "Test User"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[missing email] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[missing email] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[missing email] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Invalid email format — Zod email() validator rejects malformed addresses.
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={"fullName": "Test User", "email": "not-an-email"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[invalid email] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid email] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[invalid email] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Invalid mobile format — Zod refine: must match ^\+?[0-9]{10,15}$.
        resp = requests.post(
            base,
            headers=self.client._headers(),
            json={"fullName": "Test User", "email": "valid@example.com", "mobile": "abc123"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[invalid mobile] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "createUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[invalid mobile] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[invalid mobile] Expected 'Validation failed', got {body['error']['message']!r}"
        )

    def test_delete_user_negative_tests(self) -> None:
        """401 no auth · 400 malformed id · 404 nonexistent id."""
        base = f"{self.client.base_url}/api/v1/users"

        # Missing Authorization header — auth middleware rejects before DB lookup.
        resp = requests.delete(
            f"{base}/{_NONEXISTENT_ID}",
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUser", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # Malformed userId — Zod regex rejects before the controller.
        resp = requests.delete(
            f"{base}/{_MALFORMED_ID}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[malformed id] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUser", status_code="400")
        assert body["error"]["code"] == "VALIDATION_ERROR", (
            f"[malformed id] Expected 'VALIDATION_ERROR', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Validation failed", (
            f"[malformed id] Expected 'Validation failed', got {body['error']['message']!r}"
        )

        # Valid-format ObjectId that does not exist — controller throws NotFoundError.
        resp = requests.delete(
            f"{base}/{_NONEXISTENT_ID}",
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 404, (
            f"[nonexistent id] Expected 404, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteUser", status_code="404")
        assert body["error"]["code"] == "HTTP_NOT_FOUND", (
            f"[nonexistent id] Expected 'HTTP_NOT_FOUND', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "User not found", (
            f"[nonexistent id] Expected 'User not found', got {body['error']['message']!r}"
        )


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

    def test_list_users_graph_response_schema(self) -> None:
        """Response must match listUsersGraph schema across different query-param combinations."""
        # Default call — no params, server applies defaults (page=1, limit=10).
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[default] Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "listUsersGraph")

        # Explicit page + limit — response must respect the requested page size.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"page": "1", "limit": "2"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=1,limit=2] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph")
        assert len(body["users"]) <= 2, (
            f"[page=1,limit=2] Expected at most 2 users, got {len(body['users'])}"
        )
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 2

        # page=2, limit=1 — second page, single item.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"page": "2", "limit": "1"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[page=2,limit=1] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph")
        assert len(body["users"]) <= 1, (
            f"[page=2,limit=1] Expected at most 1 user, got {len(body['users'])}"
        )
        assert body["pagination"]["page"] == 2
        assert body["pagination"]["limit"] == 1

        # search + page + limit — may return empty list; schema must still hold.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"search": "integration", "page": "1", "limit": "5"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 200, (
            f"[search] Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph")
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 5

    def test_list_users_graph_negative_tests(self) -> None:
        """401 no auth · 400 XSS in search · 400 search too long."""
        # Missing Authorization header — auth middleware rejects before the controller.
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph", status_code="401")
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # XSS content in search — validateNoXSS throws BadRequestError with field context.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"search": "<script>alert(1)</script>"},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[xss search] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[xss search] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "HTML tags, scripts, and XSS content are not allowed. Please remove any HTML tags and try again.", (
            f"[xss search] Expected message to mention 'HTML tags, scripts, and XSS content are not allowed. Please remove any HTML tags and try again.', got {body['error']['message']!r}"
        )

        # Search exceeds 1000-char limit — explicit length guard in the controller.
        resp = requests.get(
            self.url,
            headers=self.client._headers(),
            params={"search": "a" * 1001},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[search too long] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listUsersGraph", status_code="400")
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[search too long] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "Search parameter too long (max 1000 characters)", (
            f"[search too long] Expected exact too-long message, got {body['error']['message']!r}"
        )


# ====================================================================
# PUT / GET / DELETE /api/v1/users/dp
# ====================================================================
@pytest.mark.integration
class TestDisplayPicture:
    """PUT/GET/DELETE /api/v1/users/dp — upload, retrieve, and remove display picture."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.url = f"{pipeshub_client.base_url}/api/v1/users/dp"

    def _upload_dp(
        self, file_bytes: bytes, filename: str, content_type: str
    ) -> requests.Response:
        """PUT a display picture; strips Content-Type so requests sets the multipart boundary."""
        headers = self.client._headers()
        headers.pop("Content-Type", None)
        return requests.put(
            self.url,
            headers=headers,
            files={"file": (filename, file_bytes, content_type)},
            timeout=self.client.timeout_seconds,
        )

    def test_display_picture_lifecycle(self) -> None:
        """Full PUT→GET→DELETE→GET lifecycle with PNG and JPEG uploads."""
        # Snapshot initial state so we can restore it at the end.
        initial_resp = requests.get(
            self.url,
            headers=self.client._headers(),
            timeout=self.client.timeout_seconds,
        )
        assert initial_resp.status_code == 200
        initial_ct = initial_resp.headers.get("Content-Type", "")
        initial_has_dp = initial_ct.startswith("image/")
        initial_dp_bytes = initial_resp.content if initial_has_dp else None

        try:
            # -- PUT: PNG upload --------------------------------------------------
            # Server compresses any supported image to JPEG via sharp and returns
            # the binary JPEG buffer with Content-Type: image/jpeg.
            resp = self._upload_dp(_TINY_PNG, "avatar.png", "image/png")
            assert resp.status_code == 201, (
                f"[PUT PNG] Expected 201, got {resp.status_code}: {resp.text}"
            )
            assert resp.headers.get("Content-Type", "").startswith("image/jpeg"), (
                f"[PUT PNG] Expected Content-Type image/jpeg, got {resp.headers.get('Content-Type')!r}"
            )
            assert len(resp.content) > 0, "[PUT PNG] Expected non-empty binary body"

            # -- PUT: JPEG upload -------------------------------------------------
            # Multer filters by declared MIME type; sharp processes actual bytes.
            # Reuse the PNG bytes with image/jpeg MIME — the pipeline handles both
            # formats identically (sharp detects format from magic bytes).
            resp = self._upload_dp(_TINY_PNG, "avatar.jpg", "image/jpeg")
            assert resp.status_code == 201, (
                f"[PUT JPEG] Expected 201, got {resp.status_code}: {resp.text}"
            )
            assert resp.headers.get("Content-Type", "").startswith("image/jpeg"), (
                f"[PUT JPEG] Expected Content-Type image/jpeg, got {resp.headers.get('Content-Type')!r}"
            )
            assert len(resp.content) > 0, "[PUT JPEG] Expected non-empty binary body"

            # -- GET after upload -------------------------------------------------
            # Must return binary image, not the "no dp" JSON sentinel.
            resp = requests.get(
                self.url,
                headers=self.client._headers(),
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, (
                f"[GET after upload] Expected 200, got {resp.status_code}: {resp.text}"
            )
            ct = resp.headers.get("Content-Type", "")
            assert ct.startswith("image/"), (
                f"[GET after upload] Expected image/* Content-Type, got {ct!r}"
            )
            assert len(resp.content) > 0, (
                "[GET after upload] Expected non-empty binary image"
            )

            # -- DELETE -----------------------------------------------------------
            # Nullifies pic/mimeType on the UserDisplayPicture document; response
            # is the updated doc (or an errorMessage JSON if no record existed).
            resp = requests.delete(
                self.url,
                headers=self.client._headers(),
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, (
                f"[DELETE] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "removeUserDisplayPicture")

            # -- GET after delete -------------------------------------------------
            # No dp → server returns the 200 JSON sentinel instead of binary.
            resp = requests.get(
                self.url,
                headers=self.client._headers(),
                timeout=self.client.timeout_seconds,
            )
            assert resp.status_code == 200, (
                f"[GET after delete] Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("errorMessage") == "User pic not found", (
                f"[GET after delete] Expected errorMessage 'User pic not found', got {body!r}"
            )

        finally:
            # Restore: if the user originally had a DP, re-upload those bytes.
            if initial_has_dp and initial_dp_bytes:
                self._upload_dp(initial_dp_bytes, "restore.jpg", "image/jpeg")

    def test_display_picture_negative_tests(self) -> None:
        """401 no auth (PUT/GET/DELETE) · 400 no file · 400 unsupported MIME type."""
        # -- PUT: missing auth ----------------------------------------------------
        resp = requests.put(
            self.url,
            files={"file": ("avatar.png", _TINY_PNG, "image/png")},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 401, (
            f"[no auth PUT] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadUserDisplayPicture", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth PUT] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth PUT] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # -- PUT: no file field (JSON body) ----------------------------------------
        # FileProcessorFactory with strictFileUpload=true rejects before the controller.
        headers = self.client._headers()
        headers.pop("Content-Type", None)
        resp = requests.put(
            self.url,
            headers=self.client._headers(),
            json={},
            timeout=self.client.timeout_seconds,
        )
        assert resp.status_code == 400, (
            f"[no file] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadUserDisplayPicture", status_code="400"
        )
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[no file] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No files available for processing", (
            f"[no file] Expected 'No files available for processing', got {body['error']['message']!r}"
        )

        # -- PUT: unsupported MIME type -------------------------------------------
        # fileFilter in FileProcessorService checks the declared Content-Type of the
        # multipart part; text/plain is not in the allowed-types list.
        resp = self._upload_dp(b"not an image", "file.txt", "text/plain")
        assert resp.status_code == 400, (
            f"[wrong MIME] Expected 400, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "uploadUserDisplayPicture", status_code="400"
        )
        assert body["error"]["code"] == "HTTP_BAD_REQUEST", (
            f"[wrong MIME] Expected 'HTTP_BAD_REQUEST', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == (
            "File upload failed: Invalid file type. Allowed types: image/png, image/jpeg, image/jpg, image/webp, image/gif"
        ), (
            f"[wrong MIME] Unexpected message: {body['error']['message']!r}"
        )

        # -- GET: missing auth ----------------------------------------------------
        resp = requests.get(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth GET] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "getUserDisplayPicture", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth GET] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth GET] Expected 'No token provided', got {body['error']['message']!r}"
        )

        # -- DELETE: missing auth -------------------------------------------------
        resp = requests.delete(self.url, timeout=self.client.timeout_seconds)
        assert resp.status_code == 401, (
            f"[no auth DELETE] Expected 401, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "removeUserDisplayPicture", status_code="401"
        )
        assert body["error"]["code"] == "HTTP_UNAUTHORIZED", (
            f"[no auth DELETE] Expected 'HTTP_UNAUTHORIZED', got {body['error']['code']!r}"
        )
        assert body["error"]["message"] == "No token provided", (
            f"[no auth DELETE] Expected 'No token provided', got {body['error']['message']!r}"
        )
