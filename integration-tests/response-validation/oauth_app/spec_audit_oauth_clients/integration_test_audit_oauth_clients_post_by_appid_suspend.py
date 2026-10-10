"""Strict OpenAPI audit of POST /api/v1/oauth-clients/:appId/suspend.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import SUSPEND_ROUTE, OAuthClientsAuditClient, SeedOAuthApp
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = SUSPEND_ROUTE


def test_suspend_then_suspend_again_is_a_bad_request(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()

    resp = oauth_clients_client.suspend_app(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["message"] == "OAuth app suspended successfully"
    assert body["app"]["id"] == app["id"]
    assert body["app"]["status"] == "suspended"
    assert "clientSecret" not in body["app"]
    assert_strict_openapi_exchange(resp, ROUTE)

    again = oauth_clients_client.suspend_app(app["id"])
    assert again.status_code == 400, again.text[:500]
    assert again.json()["error"] == {
        "code": "HTTP_BAD_REQUEST",
        "message": "OAuth app is already suspended",
        "requestId": again.json()["error"]["requestId"],
    }
    assert_strict_openapi_exchange(again, ROUTE)
