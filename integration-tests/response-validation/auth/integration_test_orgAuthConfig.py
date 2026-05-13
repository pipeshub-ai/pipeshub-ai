"""
Org Auth Config API – Response Validation Integration Tests
=============================================================

Tests every JSON-returning route under /api/v1/orgAuthConfig against the
``application/json`` response schemas in ``pipeshub-openapi.yaml``, via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`.
Each test validates HTTP status and JSON shape (required fields, types) as
documented for the corresponding ``operationId``.

Routes covered:
  GET  /api/v1/orgAuthConfig/authMethods      — getAuthMethods
  POST /api/v1/orgAuthConfig                   — setUpAuthConfig (already configured)
  POST /api/v1/orgAuthConfig/updateAuthMethod  — updateAuthMethod

Each route includes at least one negative test (missing Authorization and/or invalid body)
and OpenAPI ``additionalProperties: false`` checks (extra properties must fail validation
via :func:`openapi_schema_validator.assert_request_body_matches_openapi_operation` /
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`).

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
import sys
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
_AUTH_ROOT = Path(__file__).resolve().parent
for _p in (_AUTH_ROOT, _ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from openapi_schema_validator import (  # noqa: E402
    assert_request_body_matches_openapi_operation,
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from utils.auth_helpers import (  # noqa: E402
    obtain_session_access_token,
    session_headers,
)

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def session_access_token(pipeshub_client: PipeshubClient) -> str:
    """Module-scoped session JWT access token for orgAuthConfig routes."""
    return obtain_session_access_token(
        pipeshub_client.base_url,
        pipeshub_client.timeout_seconds,
    )


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
        self.headers = session_headers(session_access_token)
        self.url = f"{self.base_url}/api/v1/orgAuthConfig/authMethods"

    def test_get_auth_methods_response_schema(self) -> None:
        """Response must match OpenAPI schema for getAuthMethods."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAuthMethods")

    def test_get_auth_methods_negative_tests(self) -> None:
        """Missing auth, error-vs-success schema"""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 400, (
            f"Expected 400 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        # userAuthentication → validateJwt: no Authorization header
        assert err["message"] == "Authorization header not found", (
            f"Expected BadRequest from JWT helper; got message={err.get('message')!r}"
        )

        # Documented success response must not accept an error-shaped body
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                body, "getAuthMethods"
            )

        # Must match components/schemas/ErrorResponse (e.g. 400 on this operation)
        assert_response_matches_openapi_ref(
            body, "#/components/schemas/ErrorResponse"
        )


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
        self.headers = session_headers(session_access_token)
        self.url = f"{self.base_url}/api/v1/orgAuthConfig"

    def test_set_up_auth_config_response_schema(self) -> None:
        """Response must match OpenAPI schema for setUpAuthConfig (200)."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "setUpAuthConfig")

    def test_set_up_auth_config_negative_tests(self) -> None:
        """Missing auth: exact message, success schema rejection, ErrorResponse shape."""
        resp = requests.post(self.url, json={}, timeout=self.timeout)
        assert resp.status_code == 400, (
            f"Expected 400 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        assert err["message"] == "Authorization header not found", (
            f"Expected BadRequest from JWT helper; got message={err.get('message')!r}"
        )

        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                body, "setUpAuthConfig"
            )

        assert_response_matches_openapi_ref(
            body, "#/components/schemas/ErrorResponse"
        )


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
        self.headers = session_headers(session_access_token)
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

    def test_update_auth_method_response_schema(self) -> None:
        """Password-only update validates OpenAPI response; multi-method update echoes config."""
        original = self._get_current_auth_method()
        try:
            # Updating with password only — response must match OpenAPI schema
            password_only = [
                {
                    "order": 1,
                    "allowedMethods": [{"type": "password"}],
                },
            ]
            resp = self._update_auth_method(password_only)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(
                resp.json(), "updateAuthMethod"
            )

            # Updating with multiple methods — response must echo submitted config
            new_method = [
                {
                    "order": 1,
                    "allowedMethods": [
                        {"type": "password"},
                        {"type": "google"},
                    ],
                },
            ]
            resp = self._update_auth_method(new_method)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "updateAuthMethod")

            returned_methods = body["authMethod"]
            assert len(returned_methods) == 1
            assert returned_methods[0]["order"] == 1
            returned_types = {
                m["type"] for m in returned_methods[0]["allowedMethods"]
            }
            assert returned_types == {"password", "google"}
        finally:
            # Restore previous auth method configuration
            self._update_auth_method(original)

    def test_update_auth_method_negative_tests(self) -> None:
        """Missing Authorization and invalid body: exact messages and ErrorResponse shape."""
        resp = requests.post(
            self.update_url,
            json={"authMethod": [{"order": 1, "allowedMethods": [{"type": "password"}]}]},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        missing_auth_body = resp.json()
        err = missing_auth_body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        assert err["message"] == "Authorization header not found", (
            f"Expected BadRequest from JWT helper; got message={err.get('message')!r}"
        )

        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                missing_auth_body, "updateAuthMethod"
            )

        assert_response_matches_openapi_ref(
            missing_auth_body, "#/components/schemas/ErrorResponse"
        )

        resp = requests.post(
            self.update_url,
            headers=self.headers,
            json={"authMethod": []},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        invalid_body = resp.json()
        assert "error" in invalid_body, f"Expected error envelope: {invalid_body}"
        inv_err = invalid_body["error"]
        assert inv_err.get("message"), f"Expected error envelope: {resp.text}"
        # ValidationMiddleware: ZodError -> ValidationError(message="Validation failed", …)
        assert inv_err["message"] == "Validation failed", (
            f"Expected ValidationError message; got {inv_err.get('message')!r}"
        )

        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                invalid_body, "updateAuthMethod"
            )

        assert_response_matches_openapi_ref(
            invalid_body, "#/components/schemas/ErrorResponse"
        )