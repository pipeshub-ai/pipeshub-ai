"""Strict OpenAPI audit of POST /api/v1/oauth-clients/:appId/regenerate-secret.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import (
    REGENERATE_SECRET_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    mint_client_credentials_token,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = REGENERATE_SECRET_ROUTE


def test_regenerate_returns_a_new_secret_that_works(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(allowedScopes=["org:read"])

    resp = oauth_clients_client.regenerate_secret(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"message", "clientId", "clientSecret"}
    assert body["message"] == "Client secret regenerated successfully"
    assert body["clientId"] == app["clientId"]
    assert body["clientSecret"] != app["clientSecret"]
    assert_strict_openapi_exchange(resp, ROUTE)

    mint_client_credentials_token(
        oauth_clients_client._client.base_url, {**app, "clientSecret": body["clientSecret"]}, "org:read"
    )
