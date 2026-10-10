"""Strict OpenAPI audit of GET /api/v1/oauth-clients/:appId.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import APP_ROUTE, OAuthClientsAuditClient, SeedOAuthApp
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = APP_ROUTE


def test_get_returns_the_app_without_its_secret(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(description="spec audit", homepageUrl="https://spec-audit.example")

    resp = oauth_clients_client.get_app(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    expected = {k: v for k, v in app.items() if k != "clientSecret"}
    assert body == expected
    assert_strict_openapi_exchange(resp, ROUTE)


def test_deleted_app_is_not_found(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()
    assert oauth_clients_client.delete_app(app["id"]).status_code == 200

    resp = oauth_clients_client.get_app(app["id"])

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
