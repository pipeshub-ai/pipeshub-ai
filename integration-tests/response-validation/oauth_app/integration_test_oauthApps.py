"""
OAuth Apps API – Response Validation Integration Tests
=======================================================

Tests every JSON-returning route under ``/api/v1/oauth-clients`` (tag
``OAuth Apps`` in ``pipeshub-openapi.yaml``) against the ``application/json``
response schemas declared in the spec, via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`.

Each test validates HTTP status and JSON shape (required fields, types) as
documented for the corresponding ``operationId``.  Error bodies are validated
against ``#/components/schemas/ErrorResponse``.

Routes covered:
  GET    /api/v1/oauth-clients                                 — listOAuthApps (defaults, pagination, status filter, combined filters)
  POST   /api/v1/oauth-clients                                 — createOAuthApp
  GET    /api/v1/oauth-clients/scopes                          — listOAuthScopes
  GET    /api/v1/oauth-clients/{appId}                         — getOAuthApp
  PUT    /api/v1/oauth-clients/{appId}                         — updateOAuthApp
  DELETE /api/v1/oauth-clients/{appId}                         — deleteOAuthApp
  POST   /api/v1/oauth-clients/{appId}/regenerate-secret       — regenerateOAuthAppSecret
  POST   /api/v1/oauth-clients/{appId}/suspend                 — suspendOAuthApp
  POST   /api/v1/oauth-clients/{appId}/activate                — activateOAuthApp
  GET    /api/v1/oauth-clients/{appId}/tokens                  — listOAuthAppTokens
  POST   /api/v1/oauth-clients/{appId}/revoke-all-tokens       — revokeAllOAuthAppTokens

Auth:
  Uses the session ``oauth_apps_client`` fixture from the root ``conftest.py``,
  backed by ``PipeshubClient`` OAuth credentials (``POST /api/v1/oauth2/token``).

Notes:
  - ``TestGetOAuthApp``, ``TestUpdateOAuthApp``, ``TestListOAuthAppTokens`` share
    the module-scoped ``seeded_apps["shared"]`` app for read/update tests.
  - Destructive test classes create dedicated apps and clean up via ``_delete_app``.

Requires (handled by the root conftest):
  - ``PIPESHUB_BASE_URL``
  - either ``CLIENT_ID`` + ``CLIENT_SECRET``, **or`` ``PIPESHUB_TEST_USER_EMAIL``
    + ``PIPESHUB_TEST_USER_PASSWORD`` (bootstrap via ``local_auth``).
"""

from __future__ import annotations

import logging
import sys
import uuid
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

from helper.clients.oauth_client import OAuthAppsClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user_auth import second_pipeshub_client  # noqa: E402, F401
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
    assert_response_matches_openapi_ref,
)

_logger = logging.getLogger(__name__)

NONEXISTENT_APP_ID = "000000000000000000000000"


# ------------------------------------------------------------------ #
# Base test classes
# ------------------------------------------------------------------ #
class OAuthAppsTestBase:
    """Base class with shared oauth_apps_client fixture."""

    @pytest.fixture(autouse=True)
    def _setup(self, oauth_apps_client: OAuthAppsClient) -> None:
        self.oauth = oauth_apps_client


class OAuthAppsSeededTestBase(OAuthAppsTestBase):
    """Extends base with module-scoped shared app from ``seeded_apps``."""

    @pytest.fixture(autouse=True)
    def _setup_seeded(self, seeded_apps: dict) -> None:
        self.app_data = seeded_apps["shared"]
        self.app_id = self.app_data["app"]["id"]


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _make_app_body(grant_types: list[str] | None = None) -> dict:
    """Build a minimal ``createOAuthApp`` request body."""
    name = f"integration-oauth-apps-{uuid.uuid4().hex[:12]}"
    body: dict = {
        "name": name,
        "allowedScopes": ["openid", "profile"],
    }
    if grant_types is not None:
        body["allowedGrantTypes"] = grant_types
    return body


def _create_app(oauth: OAuthAppsClient) -> dict:
    """Create an OAuth app and return its full response plus the name used."""
    body = _make_app_body(grant_types=["client_credentials"])
    r = oauth.create_app(**body)
    assert r.status_code == 201, f"Setup createOAuthApp failed: {r.status_code} {r.text}"
    payload = r.json()
    return {"create_response": payload, "app": payload["app"], "name": body["name"]}


def _delete_app(oauth: OAuthAppsClient, app_id: str) -> None:
    """Best-effort delete of an OAuth app."""
    try:
        oauth.delete_app(app_id)
    except Exception:  # noqa: BLE001
        _logger.warning("Failed to delete OAuth app %s", app_id, exc_info=True)


def _other_oauth(second_pipeshub_client: PipeshubClient) -> OAuthAppsClient:
    """OAuth apps client authenticated as a different user."""
    return OAuthAppsClient(second_pipeshub_client)


# ------------------------------------------------------------------ #
# Module-scoped shared app (read-only / non-destructive)
# ------------------------------------------------------------------ #
@pytest.fixture(scope="module")
def seeded_apps(oauth_apps_client: OAuthAppsClient) -> Iterator[dict]:
    """Create 25 OAuth apps + 1 shared app for pagination/read tests."""
    ids: list[str] = []
    for _ in range(25):
        app = _create_app(oauth_apps_client)
        ids.append(app["app"]["id"])
    first = _create_app(oauth_apps_client)
    ids.append(first["app"]["id"])
    yield {"total": len(ids), "shared": first}
    for app_id in ids:
        _delete_app(oauth_apps_client, app_id)


# ==================================================================== #
# GET /api/v1/oauth-clients — listOAuthApps
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestListOAuthApps(OAuthAppsTestBase):
    """GET /api/v1/oauth-clients — list registered OAuth apps."""

    def test_pagination(self, seeded_apps: dict) -> None:
        """Default (page=1, limit=20) and explicit paging."""
        total = seeded_apps["total"]

        resp = self.oauth.list_apps()
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 20

        resp = self.oauth.list_apps(page="1", limit="10")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 10
        assert body["pagination"]["total"] >= total

        resp = self.oauth.list_apps(page="3", limit="10")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert body["pagination"]["page"] == 3
        assert body["pagination"]["limit"] == 10
        assert len(body["data"]) <= 10
        assert body["pagination"]["total"] >= total

    def test_filters(self) -> None:
        """Status, search, and combined filters."""
        resp = self.oauth.list_apps(status="active")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        for app in body["data"]:
            assert app["status"] == "active", f"Expected active, got {app['status']!r}"

        resp = self.oauth.list_apps(search="integration-oauth-apps")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert len(body["data"]) > 0
        for app in body["data"]:
            assert "integration-oauth-apps" in app["name"].lower()

        resp = self.oauth.list_apps(search="zzzzzzzzzzzzzzzzzzzz")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert len(body["data"]) == 0

        resp = self.oauth.list_apps(
            status="active",
            search="integration-oauth-apps",
            page="1",
            limit="5",
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["limit"] == 5
        for app in body["data"]:
            assert app["status"] == "active"

    def test_error_responses(self) -> None:
        """401 when unauthenticated, 400 for invalid params."""
        resp = self.oauth.list_apps(auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = self.oauth.list_apps(page="-1")
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")


# ==================================================================== #
# POST /api/v1/oauth-clients — createOAuthApp
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestCreateOAuthApp(OAuthAppsTestBase):
    """POST /api/v1/oauth-clients — register a new OAuth app."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for createOAuthApp (201)."""
        app_data = _create_app(self.oauth)
        try:
            assert_response_matches_openapi_operation(
                app_data["create_response"], "createOAuthApp", status_code="201"
            )
        finally:
            _delete_app(self.oauth, app_data["app"]["id"])

    def test_error_responses(self) -> None:
        """401 missing auth, 400 missing name, 400 auth_code without redirectUris."""
        resp = self.oauth.create_app(
            **_make_app_body(grant_types=["client_credentials"]),
            auth=False,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "createOAuthApp", status_code="201")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = self.oauth.create_app(allowedScopes=["openid"])
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "createOAuthApp", status_code="201")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = self.oauth.create_app(
            name="no-redirect-app",
            allowedScopes=["openid"],
            allowedGrantTypes=["authorization_code"],
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "createOAuthApp", status_code="201")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")


# ==================================================================== #
# GET /api/v1/oauth-clients/scopes — listOAuthScopes
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestListOAuthScopes(OAuthAppsTestBase):
    """GET /api/v1/oauth-clients/scopes — scopes the caller may register."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for listOAuthScopes."""
        resp = self.oauth.list_scopes()
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthScopes")
        assert isinstance(body["scopes"], dict)
        for category, items in body["scopes"].items():
            assert isinstance(items, list), (
                f"scopes[{category!r}] expected list, got {type(items).__name__}"
            )

    def test_no_auth_returns_401(self) -> None:
        """Missing Authorization header returns 401."""
        resp = self.oauth.list_scopes(auth=False)
        assert resp.status_code == 401, (
            f"Expected 401 (missing Authorization), got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"

        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "listOAuthScopes")

        assert_response_matches_openapi_ref(
            body, "#/components/schemas/ErrorResponse"
        )


# ==================================================================== #
# GET /api/v1/oauth-clients/{appId} — getOAuthApp
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestGetOAuthApp(OAuthAppsSeededTestBase):
    """GET /api/v1/oauth-clients/{appId} — retrieve a single app."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for getOAuthApp."""
        resp = self.oauth.get_app(self.app_id)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getOAuthApp")
        assert body.get("id") == self.app_data["app"]["id"]
        assert "clientSecret" not in body, "GET must never echo back clientSecret"

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """401 missing auth, 404 nonexistent, 404 cross-user."""
        resp = self.oauth.get_app(self.app_id, auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "getOAuthApp")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = self.oauth.get_app(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "getOAuthApp")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = _other_oauth(second_pipeshub_client).get_app(self.app_id)
        assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")


# ==================================================================== #
# PUT /api/v1/oauth-clients/{appId} — updateOAuthApp
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestUpdateOAuthApp(OAuthAppsSeededTestBase):
    """PUT /api/v1/oauth-clients/{appId} — update an existing app."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for updateOAuthApp (200)."""
        new_name = f"{self.app_data['name']}-updated"
        resp = self.oauth.update_app(self.app_id, name=new_name)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOAuthApp")
        assert body["app"].get("name") == new_name
        assert "clientSecret" not in body["app"], (
            "Update must never include clientSecret"
        )

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """401 missing auth, 400 auth_code without redirectUris, 404 cross-user."""
        resp = self.oauth.update_app(self.app_id, name="anything", auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "updateOAuthApp")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = self.oauth.update_app(
            self.app_id,
            allowedGrantTypes=["authorization_code"],
            redirectUris=[],
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "updateOAuthApp")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        resp = _other_oauth(second_pipeshub_client).update_app(
            self.app_id, name="hacked-name"
        )
        assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")


# ==================================================================== #
# POST /api/v1/oauth-clients/{appId}/regenerate-secret
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestRegenerateOAuthAppSecret(OAuthAppsTestBase):
    """POST /api/v1/oauth-clients/{appId}/regenerate-secret — rotate the secret."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for regenerateOAuthAppSecret."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        original_secret = app_data["app"]["clientSecret"]
        try:
            resp = self.oauth.regenerate_secret(app_id)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "regenerateOAuthAppSecret")
            assert body["clientId"] == app_data["app"]["clientId"], (
                "clientId must remain stable across secret rotation"
            )
            assert body["clientSecret"] and body["clientSecret"] != original_secret, (
                "regenerate must return a fresh clientSecret"
            )
        finally:
            _delete_app(self.oauth, app_id)

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """404 nonexistent app, 404 cross-user."""
        resp = self.oauth.regenerate_secret(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", {})
        assert err.get("message"), f"Expected error envelope: {resp.text}"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(body, "regenerateOAuthAppSecret")
        assert_response_matches_openapi_ref(body, "#/components/schemas/ErrorResponse")

        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = _other_oauth(second_pipeshub_client).regenerate_secret(app_id)
            assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")
        finally:
            _delete_app(self.oauth, app_id)


# ==================================================================== #
# POST suspend / activate
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestSuspendActivateOAuthApp(OAuthAppsTestBase):
    """Suspend → double-suspend (400) → activate → double-activate (400)."""

    def test_suspend_flow(self) -> None:
        """Suspend → 200, double-suspend → 400, activate → 200, double-activate → 400."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = self.oauth.suspend_app(app_id)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), "suspendOAuthApp")

            resp = self.oauth.suspend_app(app_id)
            assert resp.status_code == 400, (
                f"Expected 400, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_ref(
                resp.json(), "#/components/schemas/ErrorResponse"
            )
            with pytest.raises(AssertionError):
                assert_response_matches_openapi_operation(
                    resp.json(), "suspendOAuthApp"
                )

            resp = self.oauth.activate_app(app_id)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(resp.json(), "activateOAuthApp")

            resp = self.oauth.activate_app(app_id)
            assert resp.status_code == 400, (
                f"Expected 400, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_ref(
                resp.json(), "#/components/schemas/ErrorResponse"
            )
            with pytest.raises(AssertionError):
                assert_response_matches_openapi_operation(
                    resp.json(), "activateOAuthApp"
                )
        finally:
            _delete_app(self.oauth, app_id)

    def test_error_on_nonexistent_app(self) -> None:
        """Suspend and activate on non-existent app both return 404."""
        resp = self.oauth.suspend_app(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        resp = self.oauth.activate_app(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

    def test_cross_user_errors(self, second_pipeshub_client: PipeshubClient) -> None:
        """Another user cannot suspend or activate this app (creator-only)."""
        other = _other_oauth(second_pipeshub_client)

        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = other.suspend_app(app_id)
            assert resp.status_code == 404, f"Expected 404 cross-user suspend, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")
        finally:
            _delete_app(self.oauth, app_id)

        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            self.oauth.suspend_app(app_id)
            resp = other.activate_app(app_id)
            assert resp.status_code == 404, f"Expected 404 cross-user activate, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")
        finally:
            _delete_app(self.oauth, app_id)

    def test_status_filter_after_suspend(self) -> None:
        """Suspended app appears in list with status=suspended filter."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        app_name = app_data["name"]
        try:
            resp = self.oauth.suspend_app(app_id)
            assert resp.status_code == 200, (
                f"Suspend failed: {resp.status_code}: {resp.text}"
            )

            resp = self.oauth.list_apps(status="suspended", search=app_name)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert_response_matches_openapi_operation(body, "listOAuthApps")
            assert len(body["data"]) > 0, (
                "Expected at least one suspended app in results"
            )
            for app in body["data"]:
                assert app["status"] == "suspended", (
                    f"Expected suspended status, got {app['status']!r}"
                )

            self.oauth.activate_app(app_id)
        finally:
            _delete_app(self.oauth, app_id)


# ==================================================================== #
# GET /api/v1/oauth-clients/{appId}/tokens — listOAuthAppTokens
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestListOAuthAppTokens(OAuthAppsSeededTestBase):
    """GET /api/v1/oauth-clients/{appId}/tokens — list app's tokens."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for listOAuthAppTokens."""
        resp = self.oauth.list_tokens(self.app_id)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "listOAuthAppTokens")

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """401 missing auth, 404 nonexistent, 404 cross-user."""
        resp = self.oauth.list_tokens(self.app_id, auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        resp = self.oauth.list_tokens(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        resp = _other_oauth(second_pipeshub_client).list_tokens(self.app_id)
        assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")


# ==================================================================== #
# POST /api/v1/oauth-clients/{appId}/revoke-all-tokens
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestRevokeAllOAuthAppTokens(OAuthAppsTestBase):
    """POST /api/v1/oauth-clients/{appId}/revoke-all-tokens — emergency rotation."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for revokeAllOAuthAppTokens."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = self.oauth.revoke_all_tokens(app_id)
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}: {resp.text}"
            )
            assert_response_matches_openapi_operation(
                resp.json(), "revokeAllOAuthAppTokens"
            )
        finally:
            _delete_app(self.oauth, app_id)

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """404 nonexistent app, 404 cross-user."""
        resp = self.oauth.revoke_all_tokens(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = _other_oauth(second_pipeshub_client).revoke_all_tokens(app_id)
            assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")
        finally:
            _delete_app(self.oauth, app_id)


# ==================================================================== #
# DELETE /api/v1/oauth-clients/{appId} — deleteOAuthApp
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestDeleteOAuthApp(OAuthAppsTestBase):
    """DELETE /api/v1/oauth-clients/{appId} — soft-delete an app."""

    def test_response_schema(self) -> None:
        """Response must match OpenAPI schema for deleteOAuthApp."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        resp = self.oauth.delete_app(app_id)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(resp.json(), "deleteOAuthApp")

    def test_error_responses(self, second_pipeshub_client: PipeshubClient) -> None:
        """401 missing auth, 404 nonexistent, 404 cross-user."""
        resp = self.oauth.delete_app(NONEXISTENT_APP_ID, auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        resp = self.oauth.delete_app(NONEXISTENT_APP_ID)
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")

        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        try:
            resp = _other_oauth(second_pipeshub_client).delete_app(app_id)
            assert resp.status_code == 404, f"Expected 404 cross-user, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_ref(resp.json(), "#/components/schemas/ErrorResponse")
        finally:
            _delete_app(self.oauth, app_id)

    def test_deleted_app_not_gettable_or_searchable(self) -> None:
        """After deletion, GET returns 404 and search excludes the app."""
        app_data = _create_app(self.oauth)
        app_id = app_data["app"]["id"]
        app_name = app_data["name"]

        resp = self.oauth.delete_app(app_id)
        assert resp.status_code == 200, (
            f"Delete failed: {resp.status_code}: {resp.text}"
        )

        resp = self.oauth.get_app(app_id)
        assert resp.status_code == 404, (
            f"Expected 404 after delete, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_ref(
            resp.json(), "#/components/schemas/ErrorResponse"
        )

        resp = self.oauth.list_apps(search=app_name)
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthApps")
        assert len(body["data"]) == 0, (
            f"Expected empty list after delete, got {len(body['data'])} items"
        )


# ==================================================================== #
# Rate limiting
# ==================================================================== #
@pytest.mark.integration
@pytest.mark.oauth_clients
class TestOAuthAppRateLimiting(OAuthAppsTestBase):
    """Rate-limited routes return 429 with the proper error shape when burst-exceeded."""

    def test_rate_limit_error_schema(self) -> None:
        """Sequential requests — any 429 must match OAuthClientManagementRateLimitError."""
        TOTAL = 1010

        rate_limited: list[requests.Response] = []
        for _ in range(TOTAL):
            resp = self.oauth.list_apps(limit=1)
            if resp.status_code == 429:
                rate_limited.append(resp)
            if len(rate_limited) >= 5:
                break

        if not rate_limited:
            pytest.skip(
                f"No 429 responses from {TOTAL} sequential requests — "
                f"rate limiter window may be fresh in this environment"
            )

        for resp in rate_limited:
            assert_response_matches_openapi_operation(
                resp.json(), "listOAuthApps", status_code="429"
            )
