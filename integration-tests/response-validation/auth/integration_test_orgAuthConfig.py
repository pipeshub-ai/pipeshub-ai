"""
Org Auth Config API – Response Validation Integration Tests
=============================================================

Tests every JSON-returning route under /api/v1/orgAuthConfig against its
YAML response schema.  Each test validates:
  - HTTP status code
  - Required / optional fields
  - Field types, formats, and enum constraints
  - No unexpected extra fields in the response

Routes covered:
  GET  /api/v1/orgAuthConfig/authMethods      — getAuthMethod
  POST /api/v1/orgAuthConfig                   — setUpAuthConfig (already configured)
  POST /api/v1/orgAuthConfig/updateAuthMethod  — updateAuthMethod

Notes:
  - These routes use session-based JWT auth (userValidator / adminValidator),
    NOT OAuth tokens.  The tests obtain an access token via the
    initAuth -> authenticate flow using PIPESHUB_TEST_USER_EMAIL and
    PIPESHUB_TEST_USER_PASSWORD.

Requires:
  - PIPESHUB_BASE_URL in .env / .env.local
  - PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD in .env.local
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

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

# ------------------------------------------------------------------ #
# Load all response schemas
# ------------------------------------------------------------------ #
_SCHEMAS = load_yaml_schemas(
    "response-validation/schemas/auth/orgAuthConfig-response-schemas.yaml"
)

_SCHEMA_GET_AUTH_METHODS = _SCHEMAS["GetAuthMethodsResponse"]
_SCHEMA_SETUP_ALREADY_DONE = _SCHEMAS["SetUpAuthConfigAlreadyDoneResponse"]
_SCHEMA_UPDATE_AUTH_METHOD = _SCHEMAS["UpdateAuthMethodResponse"]


# ------------------------------------------------------------------ #
# Session-based auth helper
# ------------------------------------------------------------------ #

def _obtain_session_access_token(base_url: str, timeout: int = 30) -> str:
    """
    Obtain a session-based JWT access token via initAuth -> authenticate.

    These routes use session JWT auth (not OAuth), so we must log in
    with PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD.
    """
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        pytest.skip(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD required "
            "for orgAuthConfig tests (session-based JWT auth)"
        )

    # Step 1: initAuth — get session token
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    assert resp.status_code < 400, (
        f"initAuth failed: HTTP {resp.status_code}: {resp.text}"
    )
    session_token = resp.headers.get("x-session-token")
    assert session_token, "initAuth did not return x-session-token header"

    # Step 2: authenticate — get access token
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=timeout,
    )
    assert resp.status_code < 400, (
        f"authenticate failed: HTTP {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    access_token = data.get("accessToken")
    assert access_token, f"authenticate did not return accessToken: {list(data.keys())}"
    return access_token


def _session_headers(access_token: str) -> dict[str, str]:
    """Build headers for session-based JWT auth."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def session_access_token(pipeshub_client: PipeshubClient) -> str:
    """Module-scoped session JWT access token for orgAuthConfig routes."""
    return _obtain_session_access_token(pipeshub_client.base_url)


@pytest.fixture(scope="module")
def base_url(pipeshub_client: PipeshubClient) -> str:
    return pipeshub_client.base_url


@pytest.fixture(scope="module")
def timeout(pipeshub_client: PipeshubClient) -> int:
    return pipeshub_client.timeout_seconds


# ====================================================================
# GET /api/v1/orgAuthConfig/authMethods
# ====================================================================
@pytest.mark.integration
class TestGetAuthMethods:
    """GET /api/v1/orgAuthConfig/authMethods — retrieve org auth methods."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        session_access_token: str,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = _session_headers(session_access_token)
        self.url = f"{self.base_url}/api/v1/orgAuthConfig/authMethods"

    def test_response_schema(self) -> None:
        """Response must match GetAuthMethodsResponse schema."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_GET_AUTH_METHODS)



# ====================================================================
# POST /api/v1/orgAuthConfig  (setup — expect already configured)
# ====================================================================
@pytest.mark.integration
class TestSetUpAuthConfig:
    """POST /api/v1/orgAuthConfig — setUpAuthConfig.

    In a running test environment, the org config is already set up,
    so we expect the 200 "already done" response.
    """

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        session_access_token: str,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = _session_headers(session_access_token)
        self.url = f"{self.base_url}/api/v1/orgAuthConfig"

    def test_already_configured_response_schema(self) -> None:
        """Response must match SetUpAuthConfigAlreadyDoneResponse schema."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_schema(resp.json(), _SCHEMA_SETUP_ALREADY_DONE)


# ====================================================================
# POST /api/v1/orgAuthConfig/updateAuthMethod
# ====================================================================
@pytest.mark.integration
class TestUpdateAuthMethod:
    """POST /api/v1/orgAuthConfig/updateAuthMethod — update auth method."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        session_access_token: str,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = _session_headers(session_access_token)
        self.auth_methods_url = (
            f"{self.base_url}/api/v1/orgAuthConfig/authMethods"
        )
        self.update_url = (
            f"{self.base_url}/api/v1/orgAuthConfig/updateAuthMethod"
        )

    def _get_current_auth_method(self) -> list:
        """Fetch current authMethods so we can restore after update."""
        resp = requests.get(
            self.auth_methods_url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200
        return resp.json()["authMethods"]

    def _update_auth_method(self, auth_method: list) -> requests.Response:
        return requests.post(
            self.update_url,
            headers=self.headers,
            json={"authMethod": auth_method},
            timeout=self.timeout,
        )

    def test_update_password_only_response_schema(self) -> None:
        """Update to password-only — response must match schema, then restore."""
        original = self._get_current_auth_method()
        try:
            new_method = [
                {
                    "order": 1,
                    "allowedMethods": [{"type": "password"}],
                },
            ]
            resp = self._update_auth_method(new_method)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_schema(resp.json(), _SCHEMA_UPDATE_AUTH_METHOD)
        finally:
            # Restore
            self._update_auth_method(original)

    def test_update_multiple_methods_response_schema(self) -> None:
        """Update to password + otp — response must match schema, then restore."""
        original = self._get_current_auth_method()

        new_method = [
            {
                "order": 1,
                "allowedMethods": [{"type": "password"}],
            },
            {
                "order": 2,
                "allowedMethods": [{"type": "otp"}],
            },
        ]
        resp = self._update_auth_method(new_method)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_UPDATE_AUTH_METHOD)
        assert body["message"] == "Auth method updated"

        # Restore
        self._update_auth_method(original)

    def test_response_reflects_submitted_methods(self) -> None:
        """Verify the response echoes back the submitted auth method config."""
        original = self._get_current_auth_method()

        new_method = [
            {
                "order": 1,
                "allowedMethods": [{"type": "password"}, {"type": "google"}],
            },
        ]
        resp = self._update_auth_method(new_method)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_schema(body, _SCHEMA_UPDATE_AUTH_METHOD)

        returned_methods = body["authMethod"]
        assert len(returned_methods) == 1
        assert returned_methods[0]["order"] == 1
        returned_types = {m["type"] for m in returned_methods[0]["allowedMethods"]}
        assert returned_types == {"password", "google"}

        # Restore
        self._update_auth_method(original)
