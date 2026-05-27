"""
User Account API – Response Validation Integration Tests
==========================================================

Tests JSON-returning routes under /api/v1/userAccount against the
``application/json`` response schemas in ``pipeshub-openapi.yaml``, via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`.
Each test validates HTTP status and JSON shape as documented for the
corresponding ``operationId``.

Routes covered:
  POST /api/v1/userAccount/initAuth        — initAuth
  POST /api/v1/userAccount/authenticate    — authenticate (full login flow)
  POST /api/v1/userAccount/password/reset  — resetPassword (resets then restores)
  POST /api/v1/userAccount/logout/manual   — logout (empty success body; not OpenAPI-validated)
  POST /api/v1/userAccount/refresh/token   — refreshToken (uses refreshToken from login)

Each exercised route above includes at least one negative test (validation, missing auth/session, or invalid token)
and real API error bodies are validated against ``#/components/schemas/ErrorResponse``.
Success responses still use :func:`openapi_schema_validator.assert_response_matches_openapi_operation`.

Skipped (require special tokens, SMTP, or external setup):
  POST /api/v1/userAccount/login/otp/generate   — requires SMTP to send OTP
  POST /api/v1/userAccount/password/forgot       — requires SMTP
  POST /api/v1/userAccount/password/reset/token  — requires PASSWORD_RESET scoped token
  GET  /api/v1/userAccount/internal/password/check — requires FETCH_CONFIG scoped token
  POST /api/v1/userAccount/oauth/exchange        — requires external OAuth provider setup
  PUT  /api/v1/userAccount/validateEmailChange   — requires VALIDATE_EMAIL scoped token

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
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from utils.auth_helpers import (  # noqa: E402
    authenticate_password,
    init_auth,
    login_with_user,
    require_test_user_credentials,
    session_headers,
)


# ====================================================================
# POST /api/v1/userAccount/initAuth
# ====================================================================
@pytest.mark.integration
class TestInitAuth:
    """POST /api/v1/userAccount/initAuth — initialize authentication session."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def test_init_auth_response_schema(self) -> None:
        """initAuth with test user email — schema, x-session-token, allowedMethods, step 0."""
        email, _ = require_test_user_credentials()
        resp = init_auth(self.base_url, email, self.timeout)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert resp.headers.get("x-session-token"), (
            "Expected x-session-token header in initAuth response"
        )
        init_auth_response_body = resp.json()
        assert_response_matches_openapi_operation(
            init_auth_response_body, "initAuth"
        )
        assert len(init_auth_response_body["allowedMethods"]) >= 1, (
            "Expected at least one allowed method"
        )
        assert init_auth_response_body["currentStep"] == 0

    def test_init_auth_negative_tests(self) -> None:
        """Invalid email / wrong body type: ErrorResponse; real init body ≠ authenticate schema."""
        init_url = f"{self.base_url}/api/v1/userAccount/initAuth"

        # Invalid email string — Zod rejects format → 400 Validation failed + ErrorResponse
        resp = requests.post(
            init_url,
            json={"email": "not-a-valid-email"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        init_auth_invalid_email_error_body = resp.json()
        assert "error" in init_auth_invalid_email_error_body, (
            f"Expected error envelope: {resp.text}"
        )
        assert init_auth_invalid_email_error_body["error"]["message"] == (
            "Validation failed"
        ), (
            f"Unexpected message: "
            f"{init_auth_invalid_email_error_body['error'].get('message')!r}"
        )
        assert_response_matches_openapi_ref(
            init_auth_invalid_email_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                init_auth_invalid_email_error_body, "initAuth"
            )

        # Email field wrong JSON type (number) → 400 Validation failed + ErrorResponse
        resp = requests.post(
            init_url,
            json={"email": 123},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        init_auth_email_type_error_body = resp.json()
        assert "error" in init_auth_email_type_error_body
        assert init_auth_email_type_error_body["error"]["message"] == (
            "Validation failed"
        )
        assert_response_matches_openapi_ref(
            init_auth_email_type_error_body, "#/components/schemas/ErrorResponse"
        )

        # Successful initAuth JSON must not satisfy the authenticate success schema
        email, _ = require_test_user_credentials()
        init_auth_success_http_response = init_auth(
            self.base_url, email, self.timeout
        )
        assert init_auth_success_http_response.status_code == 200, (
            init_auth_success_http_response.text
        )
        init_auth_success_body = init_auth_success_http_response.json()
        assert_response_matches_openapi_operation(init_auth_success_body, "initAuth")
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                init_auth_success_body, "authenticate"
            )



# ====================================================================
# POST /api/v1/userAccount/authenticate
# ====================================================================
@pytest.mark.integration
class TestAuthenticate:
    """POST /api/v1/userAccount/authenticate — full password login flow."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def test_authenticate_response_schema(self) -> None:
        """initAuth + password authenticate — OpenAPI schema, accessToken, refreshToken."""
        email, password = require_test_user_credentials()

        init_resp = init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200, (
            f"initAuth failed: {init_resp.status_code}: {init_resp.text}"
        )
        session_token = init_resp.headers.get("x-session-token")
        assert session_token, "initAuth did not return x-session-token"

        auth_resp = authenticate_password(
            self.base_url, session_token, email, password, self.timeout,
        )
        assert auth_resp.status_code == 200, (
            f"Expected 200, got {auth_resp.status_code}: {auth_resp.text}"
        )
        authenticate_success_body = auth_resp.json()
        assert_response_matches_openapi_operation(
            authenticate_success_body, "authenticate"
        )
        access_token = authenticate_success_body.get("accessToken")
        refresh_token = authenticate_success_body.get("refreshToken")
        assert access_token, (
            f"authenticate did not return accessToken: {list(authenticate_success_body.keys())}"
        )
        assert refresh_token, (
            f"authenticate did not return refreshToken: {list(authenticate_success_body.keys())}"
        )
        assert len(access_token) > 0
        assert len(refresh_token) > 0

    def test_authenticate_negative_tests(self) -> None:
        """Session, credential, validation errors; strict body; ErrorResponse; cross-schema."""
        email, password = require_test_user_credentials()

        # No x-session-token header — authSessionMiddleware → 401 Invalid session token
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/authenticate",
            json={
                "method": "password",
                "credentials": {"password": password},
                "email": email,
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        no_sess = resp.json()
        err = no_sess.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        assert err["message"] == "Invalid session token", (
            f"authSessionMiddleware without x-session-token; got {err.get('message')!r}"
        )
        assert_response_matches_openapi_ref(
            no_sess, "#/components/schemas/ErrorResponse"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                no_sess, "authenticate"
            )

        # Valid session but wrong password — app returns 400 + ErrorResponse
        init_resp = init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200, init_resp.text
        session_token = init_resp.headers.get("x-session-token")
        assert session_token

        auth_resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/authenticate",
            headers={"x-session-token": session_token},
            json={
                "method": "password",
                "credentials": {"password": password + "__wrong_suffix__"},
                "email": email,
            },
            timeout=self.timeout,
        )
        assert auth_resp.status_code == 400, (
            f"Expected 400, got {auth_resp.status_code}: {auth_resp.text}"
        )
        authenticate_wrong_password_error_body = auth_resp.json()
        wrong_pw_message = authenticate_wrong_password_error_body.get(
            "error", {}
        ).get("message", "")
        assert "incorrect" in wrong_pw_message.lower(), (
            f"Unexpected error message: {wrong_pw_message!r}"
        )
        assert_response_matches_openapi_ref(
            authenticate_wrong_password_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                authenticate_wrong_password_error_body, "authenticate"
            )

        # Body missing required `method` — Zod → 400 Validation failed + ErrorResponse
        init_resp = init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200
        session_token = init_resp.headers.get("x-session-token")
        assert session_token

        auth_resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/authenticate",
            headers={"x-session-token": session_token},
            json={"credentials": {"password": "x"}},
            timeout=self.timeout,
        )
        assert auth_resp.status_code == 400, (
            f"Expected 400 validation error, got {auth_resp.status_code}: {auth_resp.text}"
        )
        authenticate_missing_method_field_error_body = auth_resp.json()
        assert "error" in authenticate_missing_method_field_error_body, (
            auth_resp.text
        )
        assert authenticate_missing_method_field_error_body["error"]["message"] == (
            "Validation failed"
        )
        assert_response_matches_openapi_ref(
            authenticate_missing_method_field_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                authenticate_missing_method_field_error_body,
                "authenticate",
            )

        # Unknown top-level field — authenticate body schema is .strict() → 400 Validation failed
        init_resp = init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200, init_resp.text
        session_token = init_resp.headers.get("x-session-token")
        assert session_token

        auth_resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/authenticate",
            headers={"x-session-token": session_token},
            json={
                "method": "password",
                "credentials": {"password": "x"},
                "email": email,
                "extraTopLevelField": True,
            },
            timeout=self.timeout,
        )
        assert auth_resp.status_code == 400, (
            f"Expected 400 (authenticate body is .strict()), got {auth_resp.status_code}: {auth_resp.text}"
        )
        authenticate_strict_body_violation_error_body = auth_resp.json()
        assert authenticate_strict_body_violation_error_body["error"]["message"] == (
            "Validation failed"
        )
        assert_response_matches_openapi_ref(
            authenticate_strict_body_violation_error_body,
            "#/components/schemas/ErrorResponse",
        )

        # Successful authenticate JSON must not satisfy the initAuth success schema
        authenticate_success_http_response = authenticate_password(
            self.base_url, session_token, email, password, self.timeout,
        )
        assert authenticate_success_http_response.status_code == 200, (
            authenticate_success_http_response.text
        )
        authenticate_success_body = authenticate_success_http_response.json()
        assert_response_matches_openapi_operation(
            authenticate_success_body, "authenticate"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                authenticate_success_body, "initAuth"
            )


# ====================================================================
# POST /api/v1/userAccount/password/reset
# ====================================================================
@pytest.mark.integration
class TestResetPassword:
    """POST /api/v1/userAccount/password/reset — reset password then restore."""

    TEMP_PASSWORD = "TempP@ssw0rd!Integration#2026"

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def _reset_password(
        self, access_token: str, current: str, new: str,
    ) -> requests.Response:
        return requests.post(
            f"{self.base_url}/api/v1/userAccount/password/reset",
            headers=session_headers(access_token),
            json={
                "currentPassword": current,
                "newPassword": new,
            },
            timeout=self.timeout,
        )

    def test_reset_password_response_schema(self) -> None:
        """Reset password, validate schema, then restore original password."""
        email, original_password = require_test_user_credentials()

        # Login with original password
        access_token, _ = login_with_user(
            self.base_url, email, original_password, self.timeout,
        )

        # Step 1: Change to temporary password
        resp = self._reset_password(
            access_token, original_password, self.TEMP_PASSWORD,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        reset_password_success_body = resp.json()
        assert_response_matches_openapi_operation(
            reset_password_success_body, "resetPassword"
        )
        assert reset_password_success_body["data"] == "password reset"
        assert len(reset_password_success_body["accessToken"]) > 0

        # Step 2: Login with temporary password and restore original
        new_access_token, _ = login_with_user(
            self.base_url, email, self.TEMP_PASSWORD, self.timeout,
        )
        restore_resp = self._reset_password(
            new_access_token, self.TEMP_PASSWORD, original_password,
        )
        assert restore_resp.status_code == 200, (
            f"Restore failed: {restore_resp.status_code}: {restore_resp.text}"
        )
        assert_response_matches_openapi_operation(
            restore_resp.json(), "resetPassword"
        )

    def test_reset_password_negative_tests(self) -> None:
        """Missing Authorization; empty body with token — ErrorResponse."""
        reset_url = f"{self.base_url}/api/v1/userAccount/password/reset"

        # No Authorization header — userValidator / validateJwt → 400 + ErrorResponse
        resp = requests.post(
            reset_url,
            json={
                "currentPassword": "any",
                "newPassword": "AnyOtherP@ssw0rd!",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        reset_password_missing_auth_error_body = resp.json()
        assert "error" in reset_password_missing_auth_error_body, resp.text
        assert reset_password_missing_auth_error_body["error"]["message"] == (
            "Authorization header not found"
        )
        assert_response_matches_openapi_ref(
            reset_password_missing_auth_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                reset_password_missing_auth_error_body, "resetPassword"
            )

        # Logged in but empty JSON body — Zod requires currentPassword/newPassword → 400
        email, original_password = require_test_user_credentials()
        access_token, _ = login_with_user(
            self.base_url, email, original_password, self.timeout,
        )
        resp = requests.post(
            reset_url,
            headers=session_headers(access_token),
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        reset_password_empty_body_validation_error_body = resp.json()
        assert "error" in reset_password_empty_body_validation_error_body
        assert reset_password_empty_body_validation_error_body["error"][
            "message"
        ] == "Validation failed"
        assert_response_matches_openapi_ref(
            reset_password_empty_body_validation_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                reset_password_empty_body_validation_error_body,
                "resetPassword",
            )


# ====================================================================
# POST /api/v1/userAccount/logout/manual
# ====================================================================
@pytest.mark.integration
class TestLogoutManual:
    """POST /api/v1/userAccount/logout/manual — logout then re-login."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def test_logout_returns_200_empty_body(self) -> None:
        """Logout must return 200 with empty body, then re-login succeeds."""
        email, password = require_test_user_credentials()

        # Login
        access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )

        # Logout
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/logout/manual",
            headers=session_headers(access_token),
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        # logoutSession returns res.status(200).end() — empty body
        assert resp.text == "" or resp.content == b"", (
            f"Expected empty body, got: {resp.text!r}"
        )

        # Re-login to confirm session was properly ended and new login works
        new_access_token, _ = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        assert len(new_access_token) > 0, "Re-login after logout must succeed"

    def test_logout_negative_tests(self) -> None:
        """Without Authorization — ErrorResponse; success-shaped operation rejects error body."""
        logout_url = f"{self.base_url}/api/v1/userAccount/logout/manual"
        # No Authorization — userValidator → 400 Authorization header not found + ErrorResponse
        resp = requests.post(logout_url, timeout=self.timeout)
        assert resp.status_code == 400, (
            f"Expected 400 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        logout_missing_authorization_error_body = resp.json()
        assert "error" in logout_missing_authorization_error_body, resp.text
        assert logout_missing_authorization_error_body["error"]["message"] == (
            "Authorization header not found"
        )
        assert_response_matches_openapi_ref(
            logout_missing_authorization_error_body,
            "#/components/schemas/ErrorResponse",
        )
        # Error envelope must not validate as refreshToken 200 response (wrong operation)
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                logout_missing_authorization_error_body, "refreshToken"
            )


# ====================================================================
# POST /api/v1/userAccount/refresh/token
# ====================================================================
@pytest.mark.integration
class TestRefreshToken:
    """POST /api/v1/userAccount/refresh/token — use refreshToken from login."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def test_refresh_token_response_schema(self) -> None:
        """Use refreshToken from authenticate as Bearer — response must match schema."""
        email, password = require_test_user_credentials()

        # Login to get both tokens
        _, refresh_token = login_with_user(
            self.base_url, email, password, self.timeout,
        )

        # Use refreshToken as Bearer to get a new accessToken
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/refresh/token",
            headers=session_headers(refresh_token),
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        refresh_token_success_body = resp.json()
        assert_response_matches_openapi_operation(
            refresh_token_success_body, "refreshToken"
        )

    def test_refresh_token_negative_tests(self) -> None:
        """No token, invalid Bearer — ErrorResponse; real refresh body ≠ resetPassword schema."""
        refresh_url = f"{self.base_url}/api/v1/userAccount/refresh/token"

        # No Authorization — scopedTokenValidator → 401 No token provided + ErrorResponse
        resp = requests.post(refresh_url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        refresh_token_no_bearer_error_body = resp.json()
        assert "error" in refresh_token_no_bearer_error_body, resp.text
        assert refresh_token_no_bearer_error_body["error"]["message"] == (
            "No token provided"
        )
        assert_response_matches_openapi_ref(
            refresh_token_no_bearer_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                refresh_token_no_bearer_error_body, "refreshToken"
            )

        # Malformed / unverifiable Bearer JWT — verifyScopedToken → 401 + ErrorResponse
        resp = requests.post(
            refresh_url,
            headers={
                "Authorization": "Bearer not-a-valid-jwt",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        refresh_token_invalid_jwt_error_body = resp.json()
        assert "error" in refresh_token_invalid_jwt_error_body, resp.text
        assert_response_matches_openapi_ref(
            refresh_token_invalid_jwt_error_body,
            "#/components/schemas/ErrorResponse",
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                refresh_token_invalid_jwt_error_body, "refreshToken"
            )

        # Valid refresh token — 200 matches refreshToken; same payload must not match resetPassword
        email, password = require_test_user_credentials()
        _, refresh_token = login_with_user(
            self.base_url, email, password, self.timeout,
        )
        resp = requests.post(
            refresh_url,
            headers=session_headers(refresh_token),
            timeout=self.timeout,
        )
        assert resp.status_code == 200, resp.text
        refresh_token_success_body = resp.json()
        assert_response_matches_openapi_operation(
            refresh_token_success_body, "refreshToken"
        )
        # Cross-schema: real 200 refresh payload must not match resetPassword success schema
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                refresh_token_success_body, "resetPassword"
            )
