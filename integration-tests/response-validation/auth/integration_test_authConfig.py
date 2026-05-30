"""
Authentication Configuration API – Response Validation Integration Tests
========================================================================

Tests every JSON-returning route under ``/api/v1/configurationManager/authConfig``
(tag ``Authentication Configuration`` in ``pipeshub-openapi.yaml``) against the
``application/json`` response schemas declared in the spec, via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`.

Each test validates HTTP status and JSON shape (required fields, types) as
documented for the corresponding ``operationId``.

Routes covered:
  POST /api/v1/configurationManager/authConfig/azureAd    — setAzureAdAuthConfig
  GET  /api/v1/configurationManager/authConfig/azureAd    — getAzureAdAuthConfig
  POST /api/v1/configurationManager/authConfig/microsoft   — setMicrosoftAuthConfig
  GET  /api/v1/configurationManager/authConfig/microsoft   — getMicrosoftAuthConfig
  POST /api/v1/configurationManager/authConfig/google      — setGoogleAuthConfig
  GET  /api/v1/configurationManager/authConfig/google      — getGoogleAuthConfig
  POST /api/v1/configurationManager/authConfig/sso         — setSsoAuthConfig
  GET  /api/v1/configurationManager/authConfig/sso         — getSsoAuthConfig
  POST /api/v1/configurationManager/authConfig/oauth       — setOAuthConfig
  GET  /api/v1/configurationManager/authConfig/oauth       — getGenericOAuthConfig

Auth:
  Uses the standard IT ``pipeshub_client`` fixture from the root ``conftest.py``,
  which mints a JWT via ``POST /api/v1/oauth2/token`` (grant=client_credentials).

Requires (handled by the root conftest):
  - ``PIPESHUB_BASE_URL``
  - either ``CLIENT_ID`` + ``CLIENT_SECRET``, or ``PIPESHUB_TEST_USER_EMAIL``
    + ``PIPESHUB_TEST_USER_PASSWORD``
"""

from __future__ import annotations

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
from helper.second_user_auth import second_pipeshub_client  # noqa: E402, F401

_BASE_PATH = "/api/v1/configurationManager/authConfig"

# ------------------------------------------------------------------ #
# Valid request bodies
# ------------------------------------------------------------------ #

_VALID_AZURE_AD_BODY = {
    "clientId": "11111111-1111-1111-1111-111111111111",
    "tenantId": "common",
}

_VALID_MICROSOFT_BODY = {
    "clientId": "22222222-2222-2222-2222-222222222222",
    "tenantId": "common",
}

_VALID_GOOGLE_BODY = {
    "clientId": "google-client-id-123456789.apps.googleusercontent.com",
}

_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIDazCCAlMCFH0IyHPGQ2ZqSfWaOPpxvSg+YwBXMA0GCSqGSIb3DQEBCwUAMHgx\n"
    "CzAJBgNVBAYTAlVTMRMwEQYDVQQIDApDYWxpZm9ybmlhMRIwEAYDVQQHDAlTYW4g\n"
    "Sm9zZTEaMBgGA1UECgwRSWRlbnRpdHkgUHJvdmlkZXIxDjAMBgNVBAsMBVNBTUwx\n"
    "FDAOBgNVBAMMB0V4YW1wbGUwHhcNMjQwMTAxMDAwMDAwWhcNMzQwMTAxMDAwMDAw\n"
    "WjB4MQswCQYDVQQGEwJVUzETMBEGA1UECAwKQ2FsaWZvcm5pYTESMBAGA1UEBwwJ\n"
    "U2FuIEpvc2UxGjAYBgNVBAoMEUlkZW50aXR5IFByb3ZpZGVyMQ4wDAYDVQQLDAVT\n"
    "QU1MMRQwEgYDVQQDDAtFeGFtcGxlQ2VydDCCASIwDQYJKoZIhvcNAQEBBQADggEP\n"
    "ADCCAQoCggEBAK0A4x8HxGOo+3BhOxTGf/TBu1DiqG6eHBKNGhp9q6G8RjTBlVUD\n"
    "B+whC+8xKHlkQRibKC1O9iHFP7tHmAKD1LV+H4uHrgPXYEmd6QFoHq4sppSVFEM8\n"
    "A4FRHthRAr30P4GOUN+jN6lqLQ4w3+qF7Fq2mhJqAJ5SohqnVqsThiCwBxJtBWl+\n"
    "sQXsCepf6J0Ksx7vCMqDSCcqFY6ys0ArKQL+EipDvEEhRm0pEmUnK6C+MQdsk4FC\n"
    "ZEGRIQWBosEwB1NfY5a0LwgYKAt3/qPYbAGo+nJQG4B+HWvFQ5DcHZc/BdmGV0Xj\n"
    "+QFLGuF6EEqLYCjjR6V72QOd/GhMcLmE7/MCAwEAATANBgkqhkiG9w0BAQsFAAOC\n"
    "AQEAbJQHXkPUuOg0p1jvJzPGbkkfWxEuf+GQFqYm7LmPvsdY9j5v4OaGkLhNFJKz\n"
    "i0QoRJwCEMGmQCK2Hj4Bc5NJ1LGEpPvA0lLTHaB5R0eOe4KpMkL1gHlXb3cM8oA\n"
    "SFTWUq2mC3gO5iMpNzJx5fM6HkRqE2p1vZtLwKjQs6y2oH7d5M8FxPmNvKpAqJ1\n"
    "rEoY0aU2xHtMqM6DWcJxM3gKjS4RpTq8kF2oJ5vHr8W6BmNx1eMwO3T0L2s5vKj\n"
    "RlCvMbK8mE1iF2gPj3NtLxEAw4tS5RmUm0OaHkLpI1xQkVsJwN6rG8oM5f0yHpZ\n"
    "1j2x4KqM8RlTnB0iCGcwARAQAB\n"
    "-----END CERTIFICATE-----"
)

_VALID_SSO_BODY = {
    "entryPoint": "https://idp.example.com/sso/saml",
    "certificate": _CERT,
    "emailKey": "email",
    "enableJit": True,
    "samlPlatform": "Okta",
}

_VALID_OAUTH_BODY = {
    "providerName": "Test OAuth Provider",
    "clientId": "oauth-client-id-123",
    "clientSecret": "oauth-secret-123",
    "authorizationUrl": "https://provider.example.com/oauth/authorize",
    "tokenEndpoint": "https://provider.example.com/oauth/token",
    "userInfoEndpoint": "https://provider.example.com/oauth/userinfo",
    "scope": "openid profile email",
    "redirectUri": "https://app.example.com/callback",
}


# ====================================================================
# POST /api/v1/configurationManager/authConfig/azureAd — setAzureAdAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestSetAzureAdAuthConfig:
    """POST /api/v1/configurationManager/authConfig/azureAd — configure Azure AD."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.pipeshub_client = pipeshub_client
        self.url = f"{self.base_url}{_BASE_PATH}/azureAd"

    def test_response_schema(self) -> None:
        """POST with valid body → 200, then GET validates persisted config."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_AZURE_AD_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        # Verify the config was persisted and matches the GET response schema
        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert_response_matches_openapi_operation(body, "getAzureAdAuthConfig")
        assert body.get("clientId") == _VALID_AZURE_AD_BODY["clientId"]

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token, 400 empty body."""
        resp = requests.post(
            self.url,
            json=_VALID_AZURE_AD_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setAzureAdAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            json=_VALID_AZURE_AD_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setAzureAdAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setAzureAdAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )


# ====================================================================
# GET /api/v1/configurationManager/authConfig/azureAd — getAzureAdAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestGetAzureAdAuthConfig:
    """GET /api/v1/configurationManager/authConfig/azureAd — retrieve Azure AD config."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.url = f"{self.base_url}{_BASE_PATH}/azureAd"

        # Ensure config exists
        requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_AZURE_AD_BODY,
            timeout=self.timeout,
        )

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getAzureAdAuthConfig."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getAzureAdAuthConfig")

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token."""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getAzureAdAuthConfig", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getAzureAdAuthConfig", status_code="401"
        )


# ====================================================================
# POST /api/v1/configurationManager/authConfig/microsoft — setMicrosoftAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestSetMicrosoftAuthConfig:
    """POST /api/v1/configurationManager/authConfig/microsoft — configure Microsoft."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.pipeshub_client = pipeshub_client
        self.url = f"{self.base_url}{_BASE_PATH}/microsoft"

    def test_response_schema(self) -> None:
        """POST with valid body → 200, then GET validates persisted config."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_MICROSOFT_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert_response_matches_openapi_operation(body, "getMicrosoftAuthConfig")
        assert body.get("clientId") == _VALID_MICROSOFT_BODY["clientId"]

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token, 400 empty body."""
        resp = requests.post(
            self.url,
            json=_VALID_MICROSOFT_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setMicrosoftAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            json=_VALID_MICROSOFT_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setMicrosoftAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setMicrosoftAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )


# ====================================================================
# GET /api/v1/configurationManager/authConfig/microsoft — getMicrosoftAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestGetMicrosoftAuthConfig:
    """GET /api/v1/configurationManager/authConfig/microsoft — retrieve Microsoft config."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.url = f"{self.base_url}{_BASE_PATH}/microsoft"

        requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_MICROSOFT_BODY,
            timeout=self.timeout,
        )

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getMicrosoftAuthConfig."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getMicrosoftAuthConfig"
        )

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token."""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getMicrosoftAuthConfig", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getMicrosoftAuthConfig", status_code="401"
        )


# ====================================================================
# POST /api/v1/configurationManager/authConfig/google — setGoogleAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestSetGoogleAuthConfig:
    """POST /api/v1/configurationManager/authConfig/google — configure Google."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.pipeshub_client = pipeshub_client
        self.url = f"{self.base_url}{_BASE_PATH}/google"

    def test_response_schema(self) -> None:
        """POST with valid body → 200, then GET validates persisted config."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_GOOGLE_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert_response_matches_openapi_operation(body, "getGoogleAuthConfig")
        assert body.get("clientId") == _VALID_GOOGLE_BODY["clientId"]

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token, 400 empty body."""
        resp = requests.post(
            self.url,
            json=_VALID_GOOGLE_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setGoogleAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            json=_VALID_GOOGLE_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setGoogleAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setGoogleAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )


# ====================================================================
# GET /api/v1/configurationManager/authConfig/google — getGoogleAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestGetGoogleAuthConfig:
    """GET /api/v1/configurationManager/authConfig/google — retrieve Google config."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.url = f"{self.base_url}{_BASE_PATH}/google"

        requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_GOOGLE_BODY,
            timeout=self.timeout,
        )

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getGoogleAuthConfig."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getGoogleAuthConfig")

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token."""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getGoogleAuthConfig", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getGoogleAuthConfig", status_code="401"
        )


# ====================================================================
# POST /api/v1/configurationManager/authConfig/sso — setSsoAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestSetSsoAuthConfig:
    """POST /api/v1/configurationManager/authConfig/sso — configure SAML SSO."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.pipeshub_client = pipeshub_client
        self.url = f"{self.base_url}{_BASE_PATH}/sso"

    def test_response_schema(self) -> None:
        """POST with valid body → 200, then GET validates persisted config."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_SSO_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert_response_matches_openapi_operation(body, "getSsoAuthConfig")
        assert body.get("entryPoint") == _VALID_SSO_BODY["entryPoint"]

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token, 400 empty body, 400 missing required fields."""
        resp = requests.post(
            self.url,
            json=_VALID_SSO_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setSsoAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            json=_VALID_SSO_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setSsoAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setSsoAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )

        # Missing required field `entryPoint`
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"certificate": _CERT},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setSsoAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )


# ====================================================================
# GET /api/v1/configurationManager/authConfig/sso — getSsoAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestGetSsoAuthConfig:
    """GET /api/v1/configurationManager/authConfig/sso — retrieve SSO config."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.url = f"{self.base_url}{_BASE_PATH}/sso"

        requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_SSO_BODY,
            timeout=self.timeout,
        )

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getSsoAuthConfig."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "getSsoAuthConfig")

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token."""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getSsoAuthConfig", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getSsoAuthConfig", status_code="401"
        )


# ====================================================================
# POST /api/v1/configurationManager/authConfig/oauth — setOAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestSetOAuthConfig:
    """POST /api/v1/configurationManager/authConfig/oauth — configure generic OAuth."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.pipeshub_client = pipeshub_client
        self.url = f"{self.base_url}{_BASE_PATH}/oauth"

    def test_response_schema(self) -> None:
        """POST with valid body → 200, then GET validates persisted config."""
        resp = requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_OAUTH_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        get_resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert_response_matches_openapi_operation(body, "getGenericOAuthConfig")
        assert body.get("providerName") == _VALID_OAUTH_BODY["providerName"]

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token, 400 empty body, 400 missing required fields."""
        resp = requests.post(
            self.url,
            json=_VALID_OAUTH_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setOAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            json=_VALID_OAUTH_BODY,
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setOAuthConfig", status_code="401"
        )

        resp = requests.post(
            self.url,
            headers=self.headers,
            json={},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setOAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )

        # Missing required field `clientId`
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={"providerName": "NoClientId"},
            timeout=self.timeout,
        )
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "setOAuthConfig", status_code="400"
        )
        err_code = resp.json().get("error", {}).get("code")
        assert err_code == "VALIDATION_ERROR", (
            f"Expected VALIDATION_ERROR, got {err_code!r}"
        )


# ====================================================================
# GET /api/v1/configurationManager/authConfig/oauth — getGenericOAuthConfig
# ====================================================================
@pytest.mark.integration
@pytest.mark.auth_config
class TestGetGenericOAuthConfig:
    """GET /api/v1/configurationManager/authConfig/oauth — retrieve generic OAuth config."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.headers = pipeshub_client._headers()
        self.url = f"{self.base_url}{_BASE_PATH}/oauth"

        requests.post(
            self.url,
            headers=self.headers,
            json=_VALID_OAUTH_BODY,
            timeout=self.timeout,
        )

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getGenericOAuthConfig."""
        resp = requests.get(
            self.url,
            headers=self.headers,
            timeout=self.timeout,
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getGenericOAuthConfig"
        )

    def test_error_responses(self) -> None:
        """401 missing auth, 401 invalid token."""
        resp = requests.get(self.url, timeout=self.timeout)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getGenericOAuthConfig", status_code="401"
        )

        resp = requests.get(
            self.url,
            headers={"Authorization": "Bearer invalid-token-deadbeef"},
            timeout=self.timeout,
        )
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getGenericOAuthConfig", status_code="401"
        )
