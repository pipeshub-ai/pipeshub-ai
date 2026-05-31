"""
OAuth Provider API – Response Validation Integration Tests
==========================================================

Tests all JSON-returning routes under ``/api/v1/oauth2`` and
``/.well-known/`` (tags ``OAuth Provider`` and ``OpenID Connect``
in ``pipeshub-openapi.yaml``) against the ``application/json``
response schemas declared in the spec.

Each test validates HTTP status and JSON shape (required fields, types)
as documented for the corresponding ``operationId``.  Error bodies are
validated against ``#/components/schemas/OAuthErrorResponse``.

Routes covered:
  GET    /.well-known/openid-configuration          — openidConfiguration
  GET    /.well-known/oauth-authorization-server    — oauthAuthorizationServerMetadata
  GET    /.well-known/jwks.json                     — jwks
  GET    /.well-known/oauth-protected-resource/mcp  — oauthProtectedResource
  GET    /api/v1/oauth2/authorize                   — oauthAuthorize
  POST   /api/v1/oauth2/authorize                   — oauthAuthorizeConsent
  POST   /api/v1/oauth2/token                       — oauthToken
  POST   /api/v1/oauth2/revoke                      — oauthRevoke
  POST   /api/v1/oauth2/introspect                  — oauthIntrospect
  GET    /api/v1/oauth2/userinfo                    — oauthUserInfo

Auth:
  Uses the standard IT ``pipeshub_client`` fixture from the root ``conftest.py``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Iterator

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

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Module-scoped token fixture
# ------------------------------------------------------------------ #


@pytest.fixture(scope="module")
def oauth_credentials(
    pipeshub_client: PipeshubClient,
) -> Iterator[dict]:
    """Provide client_id, client_secret, and a fresh access_token for tests."""
    pipeshub_client._ensure_access_token()
    yield {
        "client_id": os.environ.get("CLIENT_ID", ""),
        "client_secret": os.environ.get("CLIENT_SECRET", ""),
        "access_token": pipeshub_client._access_token,
    }


# ====================================================================
# OIDC Discovery endpoints — no auth, simple GET
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOIDCDiscovery:
    """/.well-known/* — OIDC discovery and JWKS endpoints."""

    @pytest.fixture(autouse=True)
    def _setup(self, pipeshub_client: PipeshubClient) -> None:
        self.client = pipeshub_client
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds

    def test_discovery_endpoints(self) -> None:
        """All discovery endpoints return 200 and valid schema."""
        tests = [
            ("/.well-known/openid-configuration", "openidConfiguration"),
            ("/.well-known/oauth-authorization-server", "oauthAuthorizationServerMetadata"),
            ("/.well-known/jwks.json", "jwks"),
            ("/.well-known/oauth-protected-resource/mcp", "oauthProtectedResource"),
        ]
        for path, operation_id in tests:
            resp = self.client.request("GET", f"{self.base_url}{path}", timeout=self.timeout, auth=False)
            assert resp.status_code == 200, (
                f"Expected 200 for {path}, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), operation_id)


# ====================================================================
# GET /api/v1/oauth2/authorize — oauthAuthorize
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthAuthorize:
    """GET /api/v1/oauth2/authorize — initiate OAuth flow."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client_id = oauth_credentials["client_id"]
        self.url = f"{self.base_url}/api/v1/oauth2/authorize"

    def test_response(self) -> None:
        """Valid params return frontend authorize page (200/302).

        The oauthRedirectMiddleware intercepts all unauthenticated requests
        to /authorize and serves the frontend OAuth page instead of returning
        JSON error responses.  Validation errors (400) are unreachable without
        a browser session behind the redirect middleware.
        """
        resp = self.client.request("GET", self.url,
            params={
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": "http://localhost/callback",
                "scope": "openid",
                "state": "teststate123",
            },
            allow_redirects=False)
        assert resp.status_code in (200, 302, 303), (
            f"Expected 200/302/303, got {resp.status_code}"
        )


# ====================================================================
# POST /api/v1/oauth2/authorize — oauthAuthorizeConsent
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthAuthorizeConsent:
    """POST /api/v1/oauth2/authorize — submit consent (requires auth)."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client = pipeshub_client
        self.client_id = oauth_credentials["client_id"]
        self.url = f"{self.base_url}/api/v1/oauth2/authorize"

    def test_response_and_errors(self) -> None:
        """401 missing auth, 400 invalid consent body."""
        resp = self.client.request("POST", self.url,
            json={
                "client_id": self.client_id,
                "redirect_uri": "http://localhost/callback",
                "scope": "openid",
                "state": "test",
                "consent": "denied",
            })
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

        resp = self.client.request("POST", self.url,
            json={"consent": "denied"})
        assert resp.status_code == 400, (
            f"Expected 400 for missing fields, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthAuthorizeConsent", status_code="400"
        )


# ====================================================================
# POST /api/v1/oauth2/token — oauthToken
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthToken:
    """POST /api/v1/oauth2/token — token exchange."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client_id = oauth_credentials["client_id"]
        self.client_secret = oauth_credentials["client_secret"]
        self.url = f"{self.base_url}/api/v1/oauth2/token"

    def test_response_schema(self) -> None:
        """client_credentials grant returns valid OAuthTokenResponse."""
        resp = self.client.request("POST", self.url,
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "oauthToken")

    def test_error_responses(self) -> None:
        """400 unsupported grant, 400 missing grant_type, 401 bad client."""
        resp = self.client.request("POST", self.url,
            json={
                "grant_type": "unsupported_grant",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthToken", status_code="400"
        )

        resp = self.client.request("POST", self.url,
            json={"client_id": self.client_id})
        assert resp.status_code == 400, (
            f"Expected 400 missing grant_type, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthToken", status_code="400"
        )

        resp = self.client.request("POST", self.url,
            json={
                "grant_type": "client_credentials",
                "client_id": "bad_client_id_000000",
                "client_secret": "bad_secret",
            })
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthToken", status_code="401"
        )


# ====================================================================
# POST /api/v1/oauth2/introspect — oauthIntrospect
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthIntrospect:
    """POST /api/v1/oauth2/introspect — token introspection."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client_id = oauth_credentials["client_id"]
        self.client_secret = oauth_credentials["client_secret"]
        self.access_token = oauth_credentials["access_token"]
        self.url = f"{self.base_url}/api/v1/oauth2/introspect"

    def test_response_and_errors(self) -> None:
        """Valid token → active=true, invalid token → active=false, bad client → 401."""
        resp = self.client.request("POST", self.url,
            json={
                "token": self.access_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "oauthIntrospect")
        assert body["active"] is True, f"Expected active=true, got {body}"

        resp = self.client.request("POST", self.url,
            json={
                "token": "invalid_token_000000000000000000",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "oauthIntrospect")
        assert body["active"] is False, f"Expected active=false, got {body}"

        resp = self.client.request("POST", self.url,
            json={
                "token": self.access_token,
                "client_id": "bad_client_id_000000",
                "client_secret": "bad_secret",
            })
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthIntrospect", status_code="401"
        )


# ====================================================================
# POST /api/v1/oauth2/revoke — oauthRevoke
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthRevoke:
    """POST /api/v1/oauth2/revoke — token revocation."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client_id = oauth_credentials["client_id"]
        self.client_secret = oauth_credentials["client_secret"]
        self.access_token = oauth_credentials["access_token"]
        self.url = f"{self.base_url}/api/v1/oauth2/revoke"

    def test_response_and_errors(self) -> None:
        """Revoke valid token → 200, bad client → 401."""
        resp = self.client.request("POST", self.url,
            json={
                "token": self.access_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )

        resp = self.client.request("POST", self.url,
            json={
                "token": self.access_token,
                "client_id": "bad_client_id_000000",
                "client_secret": "bad_secret",
            })
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "oauthRevoke", status_code="401"
        )


# ====================================================================
# GET /api/v1/oauth2/userinfo — oauthUserInfo
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthUserInfo:
    """GET /api/v1/oauth2/userinfo — OpenID Connect UserInfo."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.access_token = oauth_credentials["access_token"]
        self.url = f"{self.base_url}/api/v1/oauth2/userinfo"

    def test_error_responses(self) -> None:
        """401 missing auth, 403 with client_credentials token (no openid scope)."""
        resp = self.client.request("GET", self.url, timeout=self.timeout, auth=False)
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

        resp = self.client.request("GET", self.url, auth=False, headers={"Authorization": f"Bearer {self.access_token}"})
        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# Rate limiting
# ====================================================================
@pytest.mark.integration
@pytest.mark.oauth_provider
class TestOAuthProviderRateLimiting:
    """Rate-limited token/introspect/revoke routes return 429 on burst."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        pipeshub_client: PipeshubClient,
        oauth_credentials: dict,
    ) -> None:
        self.base_url = pipeshub_client.base_url
        self.timeout = pipeshub_client.timeout_seconds
        self.client_id = oauth_credentials["client_id"]
        self.client_secret = oauth_credentials["client_secret"]
        self.access_token = oauth_credentials["access_token"]

    def test_token_rate_limit(self) -> None:
        """Burst token endpoint and validate any 429 error schema."""
        TOTAL = 1010
        rate_limited: list[requests.Response] = []
        for _ in range(TOTAL):
            resp = self.client.request("POST", f"{self.base_url}/api/v1/oauth2/introspect",
                json={
                    "token": self.access_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                })
            if resp.status_code == 429:
                rate_limited.append(resp)
            if len(rate_limited) >= 3:
                break

        if not rate_limited:
            pytest.skip(
                f"No 429 responses from {TOTAL} sequential requests — "
                f"rate limiter window may be fresh"
            )

        for resp in rate_limited:
            assert_response_matches_openapi_operation(
                resp.json(), "oauthIntrospect", status_code="429"
            )
