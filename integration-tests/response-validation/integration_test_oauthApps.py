"""
OAuth Apps API – Response Validation Integration Tests
=======================================================

Tests every JSON-returning route under ``/api/v1/oauth-clients`` (tag
``OAuth Apps`` in ``pipeshub-openapi.yaml``) against the ``application/json``
response schemas declared in the spec, via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`.

Each test validates HTTP status and JSON shape (required fields, types) as
documented for the corresponding ``operationId``. Editing
``pipeshub-openapi.yaml`` is enough — no test code changes are needed when a
response shape evolves, only when a route is added or removed.

Routes covered:
  GET    /api/v1/oauth-clients                                 — listOAuthApps
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

Each route includes at least one negative test (missing auth, invalid body, or
deliberate conflict — e.g. suspending an already-suspended app).

Auth:
  Uses the standard IT ``pipeshub_client`` fixture from the root ``conftest.py``,
  which mints a JWT via ``POST /api/v1/oauth2/token`` (grant=client_credentials).
  ``response-validation/conftest.py`` exposes this as ``auth_headers`` and
  ``oauth_api_base_url``; tests never touch credentials directly.

Notes:
  - Tests share one OAuth app per pytest module to keep API load down; the
    fixture cleans it up at the end.
  - ``TestCreateOAuthApp`` and ``TestDeleteOAuthApp`` create dedicated apps so
    the shared one stays available for sibling tests in this file.

Requires (handled by the root conftest):
  - ``PIPESHUB_BASE_URL``
  - either ``CLIENT_ID`` + ``CLIENT_SECRET``, **or** ``PIPESHUB_TEST_USER_EMAIL``
    + ``PIPESHUB_TEST_USER_PASSWORD`` (the latter is used once to bootstrap an
    OAuth app via ``local_auth.obtain_local_oauth_credentials``; the user must
    be allowed to register OAuth apps — typically an org admin).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest
import requests

_HELPER_DIR = Path(__file__).resolve().parent / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

pytestmark = [pytest.mark.integration, pytest.mark.oauth_clients]


# ------------------------------------------------------------------ #
# Helpers (module-local)
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


# ------------------------------------------------------------------ #
# Module-scoped shared app
# ------------------------------------------------------------------ #


@pytest.fixture(scope="module")
def shared_app(
    oauth_api_base_url: str,
    pipeshub_client,  # noqa: ANN001 — session-scoped PipeshubClient from root conftest
) -> Iterator[dict]:
    """Create one OAuth app for the module; delete at teardown (best-effort).

    Takes ``pipeshub_client`` (session-scoped) directly rather than the
    function-scoped ``auth_headers`` to avoid a ScopeMismatch, and resolves
    the Bearer header at request time so an expired JWT auto-refreshes.
    Uses a local ``requests.Session`` so this module-scoped fixture is
    self-contained.
    """
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    body = _make_app_body(grant_types=["client_credentials"])
    r = session.post(
        f"{oauth_api_base_url}/api/v1/oauth-clients",
        headers=pipeshub_client._headers(),
        json=body,
        timeout=60,
    )
    assert r.status_code == 201, f"Setup createOAuthApp failed: {r.status_code} {r.text}"
    payload = r.json()
    yield {"create_response": payload, "app": payload["app"], "name": body["name"]}

    try:
        session.delete(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{payload['app']['id']}",
            headers=pipeshub_client._headers(),
            timeout=60,
        )
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
    finally:
        session.close()


# ====================================================================
# GET /api/v1/oauth-clients — listOAuthApps
# ====================================================================
class TestListOAuthApps:
    """GET /api/v1/oauth-clients — list registered OAuth apps."""

    @pytest.mark.order(1)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients",
            headers=auth_headers,
            params={"page": 1, "limit": 5},
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        assert_response_matches_openapi_operation(resp.json(), "listOAuthApps")

    @pytest.mark.order(1)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        http: requests.Session,
    ) -> None:
        resp = http.get(f"{oauth_api_base_url}/api/v1/oauth-clients", timeout=60)
        assert resp.status_code == 401, (
            f"Expected 401 unauthorized, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "listOAuthApps", status_code="401"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(resp.json(), "listOAuthApps")


# ====================================================================
# POST /api/v1/oauth-clients — createOAuthApp
# ====================================================================
class TestCreateOAuthApp:
    """POST /api/v1/oauth-clients — register a new OAuth app."""

    @pytest.mark.order(2)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients",
            headers=auth_headers,
            json=_make_app_body(grant_types=["client_credentials"]),
            timeout=60,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(
            body, "createOAuthApp", status_code="201"
        )
        app = body["app"]
        assert app.get("clientId"), "clientId required on create response"
        assert app.get("clientSecret"), "clientSecret required on create response"

        # Cleanup
        http.delete(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app['id']}",
            headers=auth_headers,
            timeout=60,
        )

    @pytest.mark.order(2)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients",
            json=_make_app_body(grant_types=["client_credentials"]),
            timeout=60,
        )
        assert resp.status_code == 401, (
            f"Expected 401 unauthorized, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "createOAuthApp", status_code="401"
        )

        # Missing required `name` field — validation must fail.
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients",
            headers=auth_headers,
            json={"allowedScopes": ["openid"]},
            timeout=60,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "createOAuthApp", status_code="400"
        )
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                resp.json(), "createOAuthApp", status_code="201"
            )


# ====================================================================
# GET /api/v1/oauth-clients/scopes — listOAuthScopes
# ====================================================================
class TestListOAuthScopes:
    """GET /api/v1/oauth-clients/scopes — scopes the caller may register."""

    @pytest.mark.order(3)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/scopes",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthScopes")
        # Every value in `scopes` must be a list (per OAuthScopesGroupedResponse).
        assert isinstance(body["scopes"], dict)
        for category, items in body["scopes"].items():
            assert isinstance(items, list), (
                f"scopes[{category!r}] expected list, got {type(items).__name__}"
            )

    @pytest.mark.order(3)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        http: requests.Session,
    ) -> None:
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/scopes",
            timeout=60,
        )
        assert resp.status_code == 401, (
            f"Expected 401 unauthorized, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "listOAuthScopes", status_code="401"
        )


# ====================================================================
# GET /api/v1/oauth-clients/{appId} — getOAuthApp
# ====================================================================
class TestGetOAuthApp:
    """GET /api/v1/oauth-clients/{appId} — retrieve a single app."""

    @pytest.mark.order(4)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "getOAuthApp")
        assert body.get("id") == app_id
        assert "clientSecret" not in body, (
            "GET must never echo back clientSecret"
        )

    @pytest.mark.order(4)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            timeout=60,
        )
        assert resp.status_code == 401, (
            f"Expected 401 unauthorized, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getOAuthApp", status_code="401"
        )

        unknown_id = "0" * 24
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, (
            f"Expected 404 not-found, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "getOAuthApp", status_code="404"
        )


# ====================================================================
# PUT /api/v1/oauth-clients/{appId} — updateOAuthApp
# ====================================================================
class TestUpdateOAuthApp:
    """PUT /api/v1/oauth-clients/{appId} — update an existing app."""

    @pytest.mark.order(5)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        new_name = f"{shared_app['name']}-updated"
        resp = http.put(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            headers=auth_headers,
            json={"name": new_name},
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "updateOAuthApp")
        assert body["app"].get("name") == new_name
        assert "clientSecret" not in body["app"], (
            "Update must never include clientSecret"
        )

    @pytest.mark.order(5)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]

        # No Authorization → 401
        resp = http.put(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            json={"name": "anything"},
            timeout=60,
        )
        assert resp.status_code == 401, (
            f"Expected 401 unauthorized, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "updateOAuthApp", status_code="401"
        )

        # Adding `authorization_code` to grants without supplying redirectUris triggers Zod refine.
        resp = http.put(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            headers=auth_headers,
            json={
                "allowedGrantTypes": ["authorization_code"],
                "redirectUris": [],
            },
            timeout=60,
        )
        assert resp.status_code == 400, (
            f"Expected 400 validation error, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "updateOAuthApp", status_code="400"
        )


# ====================================================================
# POST /api/v1/oauth-clients/{appId}/regenerate-secret — regenerateOAuthAppSecret
# ====================================================================
class TestRegenerateOAuthAppSecret:
    """POST /api/v1/oauth-clients/{appId}/regenerate-secret — rotate the secret."""

    @pytest.mark.order(6)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        original_secret = shared_app["app"]["clientSecret"]

        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/regenerate-secret",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "regenerateOAuthAppSecret")
        assert body["clientId"] == shared_app["app"]["clientId"], (
            "clientId must remain stable across secret rotation"
        )
        assert body["clientSecret"] and body["clientSecret"] != original_secret, (
            "regenerate must return a fresh clientSecret"
        )

    @pytest.mark.order(6)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        unknown_id = "0" * 24
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}/regenerate-secret",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, (
            f"Expected 404 not-found, got {resp.status_code}: {resp.text}"
        )
        assert_response_matches_openapi_operation(
            resp.json(), "regenerateOAuthAppSecret", status_code="404"
        )


# ====================================================================
# POST /api/v1/oauth-clients/{appId}/suspend — suspendOAuthApp
# POST /api/v1/oauth-clients/{appId}/activate — activateOAuthApp
# ====================================================================
class TestSuspendActivateOAuthApp:
    """Suspend → already-suspended (400) → activate → already-active (400) flow."""

    @pytest.mark.order(7)
    def test_suspend_then_activate_response_schemas(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]

        suspend = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/suspend",
            headers=auth_headers,
            timeout=60,
        )
        assert suspend.status_code == 200, suspend.text
        assert_response_matches_openapi_operation(suspend.json(), "suspendOAuthApp")

        # Double-suspend → 400 with ApplicationJsonErrorResponse (new in this PR).
        suspend_again = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/suspend",
            headers=auth_headers,
            timeout=60,
        )
        assert suspend_again.status_code == 400, suspend_again.text
        assert_response_matches_openapi_operation(
            suspend_again.json(), "suspendOAuthApp", status_code="400"
        )

        activate = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/activate",
            headers=auth_headers,
            timeout=60,
        )
        assert activate.status_code == 200, activate.text
        assert_response_matches_openapi_operation(activate.json(), "activateOAuthApp")

        # Double-activate → 400 with ApplicationJsonErrorResponse.
        activate_again = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/activate",
            headers=auth_headers,
            timeout=60,
        )
        assert activate_again.status_code == 400, activate_again.text
        assert_response_matches_openapi_operation(
            activate_again.json(), "activateOAuthApp", status_code="400"
        )

    @pytest.mark.order(7)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        unknown_id = "0" * 24

        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}/suspend",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "suspendOAuthApp", status_code="404"
        )

        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}/activate",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "activateOAuthApp", status_code="404"
        )


# ====================================================================
# GET /api/v1/oauth-clients/{appId}/tokens — listOAuthAppTokens
# ====================================================================
class TestListOAuthAppTokens:
    """GET /api/v1/oauth-clients/{appId}/tokens — list app's active tokens."""

    @pytest.mark.order(8)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/tokens",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "listOAuthAppTokens")
        assert isinstance(body["tokens"], list)

    @pytest.mark.order(8)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        unknown_id = "0" * 24
        resp = http.get(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}/tokens",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "listOAuthAppTokens", status_code="404"
        )


# ====================================================================
# POST /api/v1/oauth-clients/{appId}/revoke-all-tokens — revokeAllOAuthAppTokens
# ====================================================================
class TestRevokeAllOAuthAppTokens:
    """POST /api/v1/oauth-clients/{appId}/revoke-all-tokens — emergency rotation."""

    @pytest.mark.order(9)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
        shared_app: dict,
    ) -> None:
        app_id = shared_app["app"]["id"]
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}/revoke-all-tokens",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "revokeAllOAuthAppTokens")
        assert "message" in body

    @pytest.mark.order(9)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        unknown_id = "0" * 24
        resp = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}/revoke-all-tokens",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "revokeAllOAuthAppTokens", status_code="404"
        )


# ====================================================================
# DELETE /api/v1/oauth-clients/{appId} — deleteOAuthApp
# ====================================================================
class TestDeleteOAuthApp:
    """DELETE /api/v1/oauth-clients/{appId} — soft-delete an app.

    Runs last (order 10) so the shared app is still alive for earlier tests.
    """

    @pytest.mark.order(10)
    def test_response_schema(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        # Use a dedicated app so the module-scoped shared_app fixture cleanup
        # still works for any later test additions.
        create = http.post(
            f"{oauth_api_base_url}/api/v1/oauth-clients",
            headers=auth_headers,
            json=_make_app_body(grant_types=["client_credentials"]),
            timeout=60,
        )
        assert create.status_code == 201, create.text
        app_id = create.json()["app"]["id"]

        resp = http.delete(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{app_id}",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert_response_matches_openapi_operation(body, "deleteOAuthApp")
        assert body.get("message")

    @pytest.mark.order(10)
    def test_negative_cases(
        self,
        oauth_api_base_url: str,
        auth_headers: dict[str, str],
        http: requests.Session,
    ) -> None:
        unknown_id = "0" * 24
        resp = http.delete(
            f"{oauth_api_base_url}/api/v1/oauth-clients/{unknown_id}",
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 404, resp.text
        assert_response_matches_openapi_operation(
            resp.json(), "deleteOAuthApp", status_code="404"
        )
