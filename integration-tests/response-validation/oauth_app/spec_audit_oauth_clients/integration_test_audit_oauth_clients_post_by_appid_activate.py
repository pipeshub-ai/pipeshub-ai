"""Strict OpenAPI audit of POST /api/v1/oauth-clients/:appId/activate.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import ACTIVATE_ROUTE, OAuthClientsAuditClient, SeedOAuthApp
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = ACTIVATE_ROUTE


def test_activate_a_suspended_app(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()
    assert oauth_clients_client.suspend_app(app["id"]).status_code == 200

    resp = oauth_clients_client.activate_app(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["message"] == "OAuth app activated successfully"
    assert body["app"]["id"] == app["id"]
    assert body["app"]["status"] == "active"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_activate_an_active_app_is_a_bad_request(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()

    resp = oauth_clients_client.activate_app(app["id"])

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["message"] == "OAuth app is already active"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_activate_a_deleted_app_is_not_found(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    # Deletion is the only way an app becomes revoked, and it also hides the app,
    # so the service's "Cannot activate a revoked app" branch is never reached.
    app = seed_oauth_app()
    assert oauth_clients_client.delete_app(app["id"]).status_code == 200

    resp = oauth_clients_client.activate_app(app["id"])

    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "OAuth app not found"
    assert_strict_openapi_exchange(resp, ROUTE)
