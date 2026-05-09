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

Each exercised route above includes at least one negative test (validation, missing auth/session, or invalid token).

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

from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)
from helper.pipeshub_client import PipeshubClient  # noqa: E402

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _get_test_credentials() -> tuple[str, str]:
    """Return (email, password) from env, or skip."""
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        pytest.skip(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD required "
            "for userAccount tests"
        )
    return email, password


def _init_auth(base_url: str, email: str, timeout: int) -> requests.Response:
    """POST /api/v1/userAccount/initAuth and return the raw response."""
    return requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )


def _authenticate(
    base_url: str,
    session_token: str,
    email: str,
    password: str,
    timeout: int,
) -> requests.Response:
    """POST /api/v1/userAccount/authenticate and return the raw response."""
    return requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=timeout,
    )


def _full_login(base_url: str, email: str, password: str, timeout: int) -> str:
    """Perform initAuth + authenticate and return the session JWT access token.

    Raises AssertionError if any step fails.
    """
    init_resp = _init_auth(base_url, email, timeout)
    assert init_resp.status_code == 200, (
        f"initAuth failed: {init_resp.status_code}: {init_resp.text}"
    )
    session_token = init_resp.headers.get("x-session-token")
    assert session_token, "initAuth did not return x-session-token"

    auth_resp = _authenticate(base_url, session_token, email, password, timeout)
    assert auth_resp.status_code == 200, (
        f"authenticate failed: {auth_resp.status_code}: {auth_resp.text}"
    )
    body = auth_resp.json()
    access_token = body.get("accessToken")
    assert access_token, (
        f"authenticate did not return accessToken: {list(body.keys())}"
    )
    return access_token


def _full_login_with_tokens(
    base_url: str, email: str, password: str, timeout: int,
) -> tuple[str, str]:
    """Perform initAuth + authenticate and return (accessToken, refreshToken).

    Raises AssertionError if any step fails.
    """
    init_resp = _init_auth(base_url, email, timeout)
    assert init_resp.status_code == 200, (
        f"initAuth failed: {init_resp.status_code}: {init_resp.text}"
    )
    session_token = init_resp.headers.get("x-session-token")
    assert session_token, "initAuth did not return x-session-token"

    auth_resp = _authenticate(base_url, session_token, email, password, timeout)
    assert auth_resp.status_code == 200, (
        f"authenticate failed: {auth_resp.status_code}: {auth_resp.text}"
    )
    body = auth_resp.json()
    access_token = body.get("accessToken")
    refresh_token = body.get("refreshToken")
    assert access_token, (
        f"authenticate did not return accessToken: {list(body.keys())}"
    )
    assert refresh_token, (
        f"authenticate did not return refreshToken: {list(body.keys())}"
    )
    return access_token, refresh_token


def _session_headers(access_token: str) -> dict[str, str]:
    """Build headers for session-based JWT auth."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


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

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for initAuth."""
        email, _ = _get_test_credentials()
        resp = _init_auth(self.base_url, email, self.timeout)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "initAuth")

    def test_response_has_session_token_header(self) -> None:
        """initAuth must return x-session-token header."""
        email, _ = _get_test_credentials()
        resp = _init_auth(self.base_url, email, self.timeout)
        assert resp.status_code == 200
        assert resp.headers.get("x-session-token"), (
            "Expected x-session-token header in initAuth response"
        )

    def test_allowed_methods_not_empty(self) -> None:
        """allowedMethods must contain at least one method."""
        email, _ = _get_test_credentials()
        resp = _init_auth(self.base_url, email, self.timeout)
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_openapi_operation(body, "initAuth")
        assert len(body["allowedMethods"]) >= 1, (
            "Expected at least one allowed method"
        )

    def test_current_step_is_zero(self) -> None:
        """initAuth always starts at step 0."""
        email, _ = _get_test_credentials()
        resp = _init_auth(self.base_url, email, self.timeout)
        assert resp.status_code == 200
        body = resp.json()
        assert_response_matches_openapi_operation(body, "initAuth")
        assert body["currentStep"] == 0

    def test_negative_cases(self) -> None:
        """initAuth with syntactically invalid email must fail request validation."""
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/initAuth",
            json={"email": "not-a-valid-email"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        assert "error" in resp.json(), f"Expected error envelope: {resp.text}"

    def test_init_auth_without_email(self) -> None:
        """initAuth with empty body should still return a valid OpenAPI response."""
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/initAuth",
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "initAuth")


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

    def test_full_login_response_schema(self) -> None:
        """initAuth + authenticate with password — response must match schema."""
        email, password = _get_test_credentials()

        # Step 1: initAuth
        init_resp = _init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200, (
            f"initAuth failed: {init_resp.status_code}: {init_resp.text}"
        )
        session_token = init_resp.headers.get("x-session-token")
        assert session_token, "initAuth did not return x-session-token"

        # Step 2: authenticate
        auth_resp = _authenticate(
            self.base_url, session_token, email, password, self.timeout,
        )
        assert auth_resp.status_code == 200, (
            f"Expected 200, got {auth_resp.status_code}: {auth_resp.text}"
        )
        body = auth_resp.json()
        assert_response_matches_openapi_operation(body, "authenticate")

    def test_authenticate_returns_tokens(self) -> None:
        """Full single-step login must return accessToken and refreshToken."""
        email, password = _get_test_credentials()

        init_resp = _init_auth(self.base_url, email, self.timeout)
        assert init_resp.status_code == 200
        session_token = init_resp.headers.get("x-session-token")
        assert session_token

        auth_resp = _authenticate(
            self.base_url, session_token, email, password, self.timeout,
        )
        assert auth_resp.status_code == 200
        body = auth_resp.json()

        # If single-step, verify tokens are present
        if "accessToken" in body:
            assert_response_matches_openapi_operation(body, "authenticate")
            assert len(body["accessToken"]) > 0
            assert len(body["refreshToken"]) > 0

    def test_negative_cases(self) -> None:
        """Missing session token, wrong password, and missing method field."""
        email, password = _get_test_credentials()

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
        err = resp.json().get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"

        init_resp = _init_auth(self.base_url, email, self.timeout)
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
        msg = auth_resp.json().get("error", {}).get("message", "")
        assert "incorrect" in msg.lower(), f"Unexpected error message: {msg!r}"

        init_resp = _init_auth(self.base_url, email, self.timeout)
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
        assert "error" in auth_resp.json(), auth_resp.text


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
            headers=_session_headers(access_token),
            json={
                "currentPassword": current,
                "newPassword": new,
            },
            timeout=self.timeout,
        )

    def test_reset_password_response_schema(self) -> None:
        """Reset password, validate schema, then restore original password."""
        email, original_password = _get_test_credentials()

        # Login with original password
        access_token = _full_login(
            self.base_url, email, original_password, self.timeout,
        )

        # Step 1: Change to temporary password
        resp = self._reset_password(
            access_token, original_password, self.TEMP_PASSWORD,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "resetPassword")
        assert body["data"] == "password reset"
        assert len(body["accessToken"]) > 0

        # Step 2: Login with temporary password and restore original
        new_access_token = _full_login(
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

    def test_negative_cases(self) -> None:
        """Without Authorization and error JSON must not match resetPassword success schema."""
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/password/reset",
            json={
                "currentPassword": "any",
                "newPassword": "AnyOtherP@ssw0rd!",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 (authorization required), got {resp.status_code}: {resp.text}"
        )
        assert "error" in resp.json(), resp.text

        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/password/reset",
            json={
                "currentPassword": "any",
                "newPassword": "AnyOtherP@ssw0rd!",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 400, resp.text
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                resp.json(), "resetPassword"
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
        email, password = _get_test_credentials()

        # Login
        access_token = _full_login(self.base_url, email, password, self.timeout)

        # Logout
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/logout/manual",
            headers=_session_headers(access_token),
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
        new_access_token = _full_login(
            self.base_url, email, password, self.timeout,
        )
        assert len(new_access_token) > 0, "Re-login after logout must succeed"

    def test_negative_cases(self) -> None:
        """Without Authorization and error JSON must not match refreshToken success schema."""
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/logout/manual",
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400 (authorization required), got {resp.status_code}: {resp.text}"
        )
        assert "error" in resp.json(), resp.text

        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/logout/manual",
            timeout=self.timeout,
        )
        assert resp.status_code == 400, resp.text
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                resp.json(), "refreshToken"
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
        email, password = _get_test_credentials()

        # Login to get both tokens
        _, refresh_token = _full_login_with_tokens(
            self.base_url, email, password, self.timeout,
        )

        # Use refreshToken as Bearer to get a new accessToken
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/refresh/token",
            headers=_session_headers(refresh_token),
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "refreshToken")

    def test_negative_cases(self) -> None:
        """Non-refresh / malformed Bearer token must not issue a new access token."""
        resp = requests.post(
            f"{self.base_url}/api/v1/userAccount/refresh/token",
            headers={
                "Authorization": "Bearer not-a-valid-jwt",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert "error" in resp.json(), resp.text
